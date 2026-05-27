"""Home Assistant runtime controller for Dimsome."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, ServiceCall, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_RESUME
from .engine import (
    CIVIL_ELEVATION,
    SunElevationSample,
    active_window,
    brightness_pct_to_ha,
    civil_event_cache_samples,
    next_window_start,
    reconstructed_civil_samples,
    should_clear_manual_override_for_window,
    should_ignore_state_change,
    should_skip_for_manual_override,
    should_stand_down_for_context,
    split_turn_on_service_data,
    serialize_civil_event_cache,
    target_matches_state,
    target_for_now,
    upcoming_civil_samples,
    update_civil_event_cache,
)
from .models import (
    ColorMode,
    LightRuntime,
    LightTarget,
    OverrideResumeMode,
    RampWindow,
    ResolvedLightConfig,
    ScheduleType,
    SequenceKind,
    SunEvent,
)

_LOGGER = logging.getLogger(__name__)

RAMP_INTERVAL = timedelta(seconds=15)
SUN_REFRESH_INTERVAL = timedelta(minutes=5)
IGNORE_UPDATE_WINDOW = timedelta(seconds=10)
SUN_ENTITY_ID = "sun.sun"
SUN_ATTR_NEXT_DAWN = "next_dawn"
SUN_ATTR_NEXT_DUSK = "next_dusk"
RESUME_SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})
AUTOMATION_TRIGGERED_EVENT = "automation_triggered"
SCRIPT_STARTED_EVENT = "script_started"
MAX_AUTOMATION_CONTEXTS = 128
SPLIT_TURN_ON_DELAY = 1.0


class DimsomeController:
    """Own all mutable runtime state for one Dimsome config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        light_configs: list[ResolvedLightConfig],
        civil_event_cache: dict[SunEvent, datetime] | None = None,
        civil_event_cache_save: Any | None = None,
    ) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.entry_id = entry_id
        self.lights = {
            config.entity_id: LightRuntime(config=config) for config in light_configs
        }
        self._unsubs: list[Any] = []
        self._ramp_unsub: Any | None = None
        self._wake_unsub: Any | None = None
        self._sun_refresh_unsub: Any | None = None
        self._sun_samples: list[SunElevationSample] = []
        self._civil_event_cache = civil_event_cache or {}
        self._civil_event_cache_save = civil_event_cache_save
        self._automation_context_ids: list[str] = []

    async def async_start(self) -> None:
        """Start listeners and reconstruct current phase."""
        if not self.lights:
            return
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, list(self.lights), self._async_light_changed
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                AUTOMATION_TRIGGERED_EVENT, self._async_automation_or_script_started
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                SCRIPT_STARTED_EVENT, self._async_automation_or_script_started
            )
        )
        if any(
            uses_civil_schedule(runtime.config) for runtime in self.lights.values()
        ):
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, [SUN_ENTITY_ID], self._async_sun_changed
                )
            )
            self._sun_refresh_unsub = async_track_time_interval(
                self.hass, self.async_tick, SUN_REFRESH_INTERVAL
            )
            self._record_sun_sample(self.hass.states.get(SUN_ENTITY_ID))

        await self.async_tick()

    async def async_stop(self) -> None:
        """Stop listeners and pending timers."""
        if self._ramp_unsub is not None:
            self._ramp_unsub()
            self._ramp_unsub = None
        if self._wake_unsub is not None:
            self._wake_unsub()
            self._wake_unsub = None
        if self._sun_refresh_unsub is not None:
            self._sun_refresh_unsub()
            self._sun_refresh_unsub = None
        for runtime in self.lights.values():
            if runtime.grace_unsub is not None:
                runtime.grace_unsub()
                runtime.grace_unsub = None
        while self._unsubs:
            self._unsubs.pop()()

    async def async_resume(self, entity_ids: Collection[str] | None = None) -> None:
        """Resume Dimsome control for selected lights."""
        selected = set(entity_ids or self.lights)
        for entity_id, runtime in self.lights.items():
            if entity_id not in selected:
                continue
            runtime.stood_down = False
            runtime.stood_down_window = None
            runtime.last_target = None
            runtime.pending_target = None
            runtime.expected_target = None
            if runtime.grace_unsub is not None:
                runtime.grace_unsub()
                runtime.grace_unsub = None
        await self.async_tick()

    async def async_set_enabled(self, entity_id: str, enabled: bool) -> None:
        """Enable or indefinitely pause Dimsome control for one light."""
        runtime = self.lights[entity_id]
        runtime.config = replace(runtime.config, enabled=enabled)
        runtime.last_target = None
        runtime.pending_target = None
        if not enabled:
            runtime.stood_down = True
            runtime.stood_down_window = None
            if runtime.grace_unsub is not None:
                runtime.grace_unsub()
                runtime.grace_unsub = None
        else:
            runtime.stood_down = False
            runtime.stood_down_window = None
        await self.async_tick()

    def runtime_status(self, entity_id: str | None = None) -> dict[str, Any]:
        """Return diagnostic runtime state for one light or all lights."""
        now = dt_util.now()
        selected = (
            {entity_id: self.lights[entity_id]}
            if entity_id is not None
            else self.lights
        )
        return {
            light_entity_id: self._runtime_status_for_light(runtime, now)
            for light_entity_id, runtime in selected.items()
        }

    def _runtime_status_for_light(
        self, runtime: LightRuntime, now: datetime
    ) -> dict[str, Any]:
        """Return diagnostic runtime state for one light."""
        window = active_window(runtime.config, now, self._sun_samples)
        target = target_for_now(runtime.config, now, self._sun_samples)
        return {
            "enabled": runtime.config.enabled,
            "status": self._status_for_runtime(runtime, window),
            "stood_down": runtime.stood_down,
            "stood_down_window": _window_status(runtime.stood_down_window),
            "active_window": _window_status(window),
            "civil_event_cache": {
                event.value: _datetime_status(at)
                for event, at in sorted(
                    self._civil_event_cache.items(), key=lambda item: item[0].value
                )
            },
            "next_window_start": _datetime_status(
                next_window_start(runtime.config, now, self._sun_samples)
            ),
            "target": _target_status(target),
            "last_target": _target_status(runtime.last_target),
            "expected_target": _target_status(runtime.expected_target),
            "pending_target": _target_status(runtime.pending_target),
            "in_flight": runtime.in_flight,
            "ignore_updates_until": _datetime_status(runtime.ignore_updates_until),
            "last_decision": runtime.last_decision,
            "last_decision_at": _datetime_status(runtime.last_decision_at),
        }

    def _status_for_runtime(
        self, runtime: LightRuntime, window: RampWindow | None
    ) -> str:
        """Return a concise human-readable runtime status."""
        if not runtime.config.enabled:
            return "disabled"
        if runtime.stood_down and window is not None:
            return "manual_override"
        if window is not None:
            return "ramping"
        if runtime.stood_down:
            return "stood_down"
        return "tracking"

    async def async_tick(self, *_: Any) -> None:
        """Apply current targets and manage the active ramp timer."""
        now = dt_util.now()
        if any(uses_civil_schedule(runtime.config) for runtime in self.lights.values()):
            self._record_sun_sample(self.hass.states.get(SUN_ENTITY_ID))
        any_active = False
        next_start = None
        for runtime in self.lights.values():
            if not runtime.config.enabled:
                runtime.last_target = None
                self._record_decision(runtime, "disabled", now)
                continue
            state = self.hass.states.get(runtime.config.entity_id)
            window = active_window(runtime.config, now, self._sun_samples)
            if should_clear_manual_override_for_window(
                stood_down=runtime.stood_down,
                stood_down_window=runtime.stood_down_window,
                window=window,
            ):
                runtime.stood_down = False
                runtime.stood_down_window = None
                _LOGGER.debug(
                    "Resuming %s for new ramp window", runtime.config.entity_id
                )
            candidate_start = next_window_start(runtime.config, now, self._sun_samples)
            if candidate_start is not None and (
                next_start is None or candidate_start < next_start
            ):
                next_start = candidate_start
            target = target_for_now(runtime.config, now, self._sun_samples)
            if window is None and target is not None:
                from .engine import (
                    candidate_windows as _cw,
                    is_civil_night as _icn,
                    latest_sun_elevation as _lse,
                )
                elevation = _lse(now, self._sun_samples)
                civil_night = _icn(now, self._sun_samples)
                all_windows = _cw(runtime.config, now, self._sun_samples)
                if civil_night or any(
                    abs((w.start - now).total_seconds()) < 7200 for w in all_windows
                ):
                    _LOGGER.warning(
                        "DIAG %s now=%s elev=%.2f civil_night=%s window=None target=%s "
                        "all_windows=%s samples=%d",
                        runtime.config.entity_id,
                        now.isoformat(),
                        elevation if elevation is not None else -99.0,
                        civil_night,
                        target,
                        [(w.sequence, w.start.isoformat(), w.end.isoformat()) for w in all_windows],
                        len(self._sun_samples),
                    )
            if target is None:
                runtime.last_target = None
                self._record_decision(runtime, "no_target", now)
                continue
            if window is not None:
                any_active = True
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, STATE_OFF):
                _LOGGER.debug(
                    "Skipping %s because state is %s",
                    runtime.config.entity_id,
                    state.state if state is not None else None,
                )
                self._record_decision(
                    runtime,
                    f"skipped_state_{state.state if state is not None else 'missing'}",
                    now,
                )
                continue
            if should_skip_for_manual_override(
                stood_down=runtime.stood_down, window=window
            ):
                _LOGGER.debug(
                    "Skipping %s because it is stood down for the active ramp",
                    runtime.config.entity_id,
                )
                self._record_decision(runtime, "skipped_manual_override", now)
                continue
            await self._async_apply_target(runtime, target)
            self._record_decision(runtime, "applied_target", now)

        if any_active:
            self._cancel_wake_timer()
        if any_active and self._ramp_unsub is None:
            self._ramp_unsub = async_track_time_interval(
                self.hass, self.async_tick, RAMP_INTERVAL
            )
        elif not any_active and self._ramp_unsub is not None:
            self._ramp_unsub()
            self._ramp_unsub = None
        if not any_active:
            self._schedule_wake_timer(now, next_start)

    def _cancel_wake_timer(self) -> None:
        """Cancel the one-shot timer for the next ramp start."""
        if self._wake_unsub is None:
            return
        self._wake_unsub()
        self._wake_unsub = None

    def _schedule_wake_timer(
        self, now: datetime, next_start: datetime | None
    ) -> None:
        """Schedule a one-shot tick for the next known ramp start."""
        self._cancel_wake_timer()
        if next_start is None:
            return
        delay = max(0.0, (next_start - now).total_seconds())
        self._wake_unsub = async_call_later(self.hass, delay, self.async_tick)

    @callback
    def _async_light_changed(self, event: Event) -> None:
        """Handle controlled light state changes."""
        entity_id = event.data[ATTR_ENTITY_ID]
        runtime = self.lights[entity_id]
        if not runtime.config.enabled:
            return
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        if new_state is None:
            return

        if (
            runtime.config.apply_on_recovered_on
            and (
                old_state is None
                or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            )
            and new_state.state == STATE_ON
        ):
            runtime.stood_down = False
            runtime.stood_down_window = None
            runtime.last_target = None
            self.hass.async_create_task(self._async_handle_turn_on(runtime))
            return

        if old_state is not None and old_state.state == STATE_OFF and new_state.state == STATE_ON:
            runtime.stood_down = False
            runtime.stood_down_window = None
            runtime.last_target = None
            self.hass.async_create_task(self._async_handle_turn_on(runtime))
            return

        now = dt_util.now()
        if new_state.state != STATE_ON:
            runtime.last_target = None
            return
        if should_ignore_state_change(
            in_flight=runtime.in_flight,
            now=now,
            ignore_updates_until=runtime.ignore_updates_until,
            expected_target=runtime.expected_target,
            attrs=new_state.attributes,
        ):
            return

        window = active_window(runtime.config, now, self._sun_samples)
        if window is None:
            return
        if not should_stand_down_for_context(
            new_state.context, set(self._automation_context_ids)
        ):
            _LOGGER.debug("Ignoring automation-originated change for %s", entity_id)
            return
        runtime.stood_down = True
        runtime.stood_down_window = window
        _LOGGER.debug("Standing down %s after external light change", entity_id)
        self._schedule_grace_resume(runtime)

    @callback
    def _async_automation_or_script_started(self, event: Event) -> None:
        """Remember automation/script contexts so they do not stop active ramps."""
        context_id = event.context.id
        if context_id is None:
            return
        self._automation_context_ids.append(context_id)
        del self._automation_context_ids[:-MAX_AUTOMATION_CONTEXTS]

    @callback
    def _async_sun_changed(self, event: Event) -> None:
        """Detect civil dawn/dusk by sun.sun elevation threshold crossing."""
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        self._record_sun_sample(new_state)
        if old_state is None or new_state is None:
            return
        old_elevation = _state_elevation(old_state)
        new_elevation = _state_elevation(new_state)
        if old_elevation is None or new_elevation is None:
            return
        event_kind: SunEvent | None = None
        if old_elevation < CIVIL_ELEVATION <= new_elevation:
            event_kind = SunEvent.CIVIL_DAWN
        elif old_elevation > CIVIL_ELEVATION >= new_elevation:
            event_kind = SunEvent.CIVIL_DUSK
        if event_kind is None:
            return
        now = dt_util.now()
        self._sun_samples.append(SunElevationSample(now, CIVIL_ELEVATION))
        for runtime in self.lights.values():
            if schedule_uses_event(runtime.config, event_kind):
                runtime.stood_down = False
        self.hass.async_create_task(self.async_tick())

    async def _async_handle_turn_on(self, runtime: LightRuntime) -> None:
        """Apply the current expected value when a light turns on."""
        if runtime.config.settle_delay > timedelta(0):
            await asyncio.sleep(runtime.config.settle_delay.total_seconds())
        now = dt_util.now()
        target = target_for_now(runtime.config, now, self._sun_samples)
        if target is not None:
            await self._async_apply_target(runtime, target)
            await self._async_verify_turn_on_target(runtime, target)

    async def _async_verify_turn_on_target(
        self, runtime: LightRuntime, target: LightTarget
    ) -> None:
        """Reapply turn-on targets that were lost to device restore timing."""
        await asyncio.sleep(IGNORE_UPDATE_WINDOW.total_seconds())
        now = dt_util.now()
        if target_for_now(runtime.config, now, self._sun_samples) != target:
            return
        state = self.hass.states.get(runtime.config.entity_id)
        if state is None or state.state != STATE_ON:
            return
        if target_matches_state(target, state.attributes):
            return
        runtime.last_target = None
        await self._async_apply_target(runtime, target)

    async def _async_apply_target(
        self, runtime: LightRuntime, target: LightTarget
    ) -> None:
        """Apply a target with per-light backpressure."""
        if runtime.last_target == target:
            return
        if runtime.in_flight:
            _LOGGER.debug(
                "Queueing pending Dimsome target for %s: %s",
                runtime.config.entity_id,
                target,
            )
            runtime.pending_target = target
            return
        _LOGGER.debug(
            "Applying Dimsome target for %s: %s", runtime.config.entity_id, target
        )
        runtime.in_flight = True
        runtime.expected_target = target
        runtime.ignore_updates_until = dt_util.now() + IGNORE_UPDATE_WINDOW
        try:
            await self._async_call_light(runtime, target)
            runtime.last_target = target
            runtime.expected_target = target
            runtime.ignore_updates_until = dt_util.now() + IGNORE_UPDATE_WINDOW
        finally:
            runtime.in_flight = False
        if runtime.pending_target is not None:
            pending = runtime.pending_target
            runtime.pending_target = None
            await self._async_apply_target(runtime, pending)

    async def _async_call_light(self, runtime: LightRuntime, target: LightTarget) -> None:
        """Call light.turn_on for brightness and optional color."""
        base_data: dict[str, Any] = {
            ATTR_ENTITY_ID: runtime.config.entity_id,
            ATTR_BRIGHTNESS: brightness_pct_to_ha(target.brightness_pct),
        }
        color_data = color_service_data(target)
        if color_data and runtime.config.split_turn_on_calls:
            for index, data in enumerate(
                split_turn_on_service_data(runtime.config.entity_id, target)
            ):
                if index > 0:
                    await asyncio.sleep(SPLIT_TURN_ON_DELAY)
                await self.hass.services.async_call(
                    LIGHT_DOMAIN, "turn_on", data, blocking=True
                )
            return
        await self.hass.services.async_call(
            LIGHT_DOMAIN,
            "turn_on",
            {**base_data, **color_data},
            blocking=True,
        )

    def _schedule_grace_resume(self, runtime: LightRuntime) -> None:
        """Schedule optional automatic resume after a manual override."""
        if runtime.grace_unsub is not None:
            runtime.grace_unsub()
            runtime.grace_unsub = None
        if (
            runtime.config.override_resume_mode is not OverrideResumeMode.AFTER_GRACE_PERIOD
            or runtime.config.override_grace_period is None
        ):
            return

        async def _resume(_: Any) -> None:
            runtime.stood_down = False
            runtime.stood_down_window = None
            runtime.grace_unsub = None
            await self.async_tick()

        runtime.grace_unsub = async_call_later(
            self.hass, runtime.config.override_grace_period.total_seconds(), _resume
        )

    def _record_sun_sample(self, state: State | None) -> None:
        """Record bounded sun elevation samples."""
        elevation = _state_elevation(state)
        if elevation is None:
            return
        cache = getattr(self, "_civil_event_cache", None)
        if cache is None:
            cache = self._civil_event_cache = {}
        if state is not None:
            cache_changed = update_civil_event_cache(
                cache,
                now=dt_util.now(),
                next_dawn=state.attributes.get(SUN_ATTR_NEXT_DAWN),
                next_dusk=state.attributes.get(SUN_ATTR_NEXT_DUSK),
                ramp_duration=max(
                    (runtime.config.ramp_duration for runtime in self.lights.values()),
                    default=timedelta(0),
                ),
            )
            if cache_changed and self._civil_event_cache_save is not None:
                self._civil_event_cache_save(serialize_civil_event_cache(cache))
        self._sun_samples.extend(civil_event_cache_samples(cache))
        self._sun_samples.extend(_reconstructed_civil_samples(state, elevation))
        self._sun_samples.extend(_upcoming_civil_samples(state))
        self._sun_samples.append(SunElevationSample(dt_util.now(), elevation))
        cutoff = dt_util.now() - timedelta(days=2)
        bounded = (sample for sample in self._sun_samples if sample.at >= cutoff)
        self._sun_samples = _dedupe_sun_samples(bounded)

    def _record_decision(
        self, runtime: LightRuntime, decision: str, at: datetime
    ) -> None:
        """Remember the most recent tick decision for diagnostics."""
        runtime.last_decision = decision
        runtime.last_decision_at = at


def color_service_data(target: LightTarget) -> dict[str, Any]:
    """Convert a target color into light.turn_on service data."""
    if target.color is None:
        return {}
    if target.color.mode is ColorMode.COLOR_TEMP_KELVIN:
        return {ColorMode.COLOR_TEMP_KELVIN.value: target.color.value}
    return {}


def _datetime_status(value: datetime | None) -> str | None:
    """Return an ISO timestamp for diagnostic output."""
    return value.isoformat() if value is not None else None


def _target_status(target: LightTarget | None) -> dict[str, Any] | None:
    """Return serializable target diagnostics."""
    if target is None:
        return None
    return {
        "brightness_pct": target.brightness_pct,
        "brightness": brightness_pct_to_ha(target.brightness_pct),
        "color": _color_status(target.color),
    }


def _color_status(color: Any | None) -> dict[str, Any] | None:
    """Return serializable color diagnostics."""
    if color is None:
        return None
    return {"mode": color.mode.value, "value": color.value}


def _window_status(window: RampWindow | None) -> dict[str, str] | None:
    """Return serializable ramp window diagnostics."""
    if window is None:
        return None
    return {
        "sequence": window.sequence.value,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
    }


def uses_civil_schedule(config: ResolvedLightConfig) -> bool:
    """Return whether a light config depends on sun elevation."""
    return (
        config.dim_schedule.type is ScheduleType.CIVIL_SUN
        or config.brighten_schedule.type is ScheduleType.CIVIL_SUN
    )


def schedule_uses_event(config: ResolvedLightConfig, event: SunEvent) -> bool:
    """Return whether either schedule starts at the given civil event."""
    return (
        config.dim_schedule.event is event or config.brighten_schedule.event is event
    )


def _state_elevation(state: State | None) -> float | None:
    """Extract elevation from sun.sun state."""
    if state is None:
        return None
    elevation = state.attributes.get("elevation")
    if elevation is None:
        return None
    try:
        return float(elevation)
    except (TypeError, ValueError):
        return None


def _dedupe_sun_samples(
    samples: Collection[SunElevationSample],
) -> list[SunElevationSample]:
    """Deduplicate samples while preserving exact civil crossing markers."""
    by_time: dict[datetime, SunElevationSample] = {}
    for sample in samples:
        existing = by_time.get(sample.at)
        if existing is None or existing.elevation != CIVIL_ELEVATION:
            by_time[sample.at] = sample
    return sorted(by_time.values(), key=lambda sample: sample.at)


def _reconstructed_civil_samples(
    state: State | None, elevation: float
) -> list[SunElevationSample]:
    """Reconstruct the last civil crossing exposed by sun.sun after reload."""
    if state is None:
        return []
    return reconstructed_civil_samples(
        elevation=elevation,
        next_dawn=state.attributes.get(SUN_ATTR_NEXT_DAWN),
        next_dusk=state.attributes.get(SUN_ATTR_NEXT_DUSK),
        now=dt_util.now(),
    )


def _upcoming_civil_samples(state: State | None) -> list[SunElevationSample]:
    """Reconstruct upcoming civil crossings exposed by sun.sun."""
    if state is None:
        return []
    return upcoming_civil_samples(
        next_dawn=state.attributes.get(SUN_ATTR_NEXT_DAWN),
        next_dusk=state.attributes.get(SUN_ATTR_NEXT_DUSK),
    )


async def async_resume_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle dimsome.resume."""
    entity_ids = call.data.get(ATTR_ENTITY_ID)
    if isinstance(entity_ids, str):
        selected = {entity_ids}
    else:
        selected = set(entity_ids) if entity_ids else None
    controllers: list[DimsomeController] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            controllers.append(entry.runtime_data)
    if not controllers:
        raise ServiceValidationError("No loaded Dimsome config entries")
    if selected is not None:
        configured = {
            entity_id for controller in controllers for entity_id in controller.lights
        }
        missing = selected - configured
        if missing:
            raise ServiceValidationError(
                f"Lights are not configured in Dimsome: {', '.join(sorted(missing))}"
            )
    for controller in controllers:
        await controller.async_resume(selected)


def register_services(hass: HomeAssistant) -> None:
    """Register Dimsome services once."""
    if hass.services.has_service(DOMAIN, SERVICE_RESUME):
        return

    async def _async_resume_service(call: ServiceCall) -> None:
        await async_resume_service(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESUME,
        _async_resume_service,
        schema=RESUME_SERVICE_SCHEMA,
    )
