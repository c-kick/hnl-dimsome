"""Home Assistant runtime controller for Dimsome.

Civil dawn and dusk come straight from Home Assistant's astral helpers, which
are deterministic for any calendar date.  There is no sun-elevation sampling,
no crossing reconstruction, and no anchor cache: the controller just asks
``get_astral_event_date`` when it needs a ramp start time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, ServiceCall, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_RESUME
from .engine import (
    active_window,
    brightness_pct_to_ha,
    color_service_data,
    next_window_start,
    should_clear_manual_override_for_window,
    should_ignore_state_change,
    should_skip_for_manual_override,
    should_stand_down_for_context,
    split_turn_on_service_data,
    target_matches_state,
    target_for_now,
)
from .models import (
    ColorTarget,
    LightRuntime,
    LightTarget,
    OverrideResumeMode,
    RampWindow,
    ResolvedLightConfig,
    SunEvent,
)

_LOGGER = logging.getLogger(__name__)

RAMP_INTERVAL = timedelta(seconds=15)
REFRESH_INTERVAL = timedelta(minutes=5)
IGNORE_UPDATE_WINDOW = timedelta(seconds=10)
RESUME_SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})
AUTOMATION_TRIGGERED_EVENT = "automation_triggered"
SCRIPT_STARTED_EVENT = "script_started"
MAX_AUTOMATION_CONTEXTS = 128
SPLIT_TURN_ON_DELAY = 1.0

#: Map Dimsome civil events to Home Assistant astral event names.
_ASTRAL_EVENT = {SunEvent.CIVIL_DAWN: "dawn", SunEvent.CIVIL_DUSK: "dusk"}


class DimsomeController:
    """Own all mutable runtime state for one Dimsome config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        light_configs: list[ResolvedLightConfig],
        native_user_ids: frozenset[str] = frozenset(),
    ) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.entry_id = entry_id
        self.lights = {
            config.entity_id: LightRuntime(config=config) for config in light_configs
        }
        self._native_user_ids = native_user_ids
        self._unsubs: list[Any] = []
        self._ramp_unsub: Any | None = None
        self._wake_unsub: Any | None = None
        self._refresh_unsub: Any | None = None
        self._automation_context_ids: list[str] = []
        self._turn_on_tasks: set[asyncio.Task] = set()
        self._stopped = False

    def _civil_lookup(self, event: SunEvent, day: date) -> datetime | None:
        """Resolve a civil sun event on a calendar date via HA's astral data."""
        return get_astral_event_date(self.hass, _ASTRAL_EVENT[event], day)

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
        self._refresh_unsub = async_track_time_interval(
            self.hass, self.async_tick, REFRESH_INTERVAL
        )
        await self.async_tick()

    async def async_stop(self) -> None:
        """Stop listeners and pending timers."""
        self._stopped = True
        if self._ramp_unsub is not None:
            self._ramp_unsub()
            self._ramp_unsub = None
        if self._wake_unsub is not None:
            self._wake_unsub()
            self._wake_unsub = None
        if self._refresh_unsub is not None:
            self._refresh_unsub()
            self._refresh_unsub = None
        for runtime in self.lights.values():
            runtime.pending_target = None
            _cancel_grace_resume(runtime)
        tasks = list(self._turn_on_tasks)
        for task in tasks:
            task.cancel()
        self._turn_on_tasks.clear()
        while self._unsubs:
            self._unsubs.pop()()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def async_resume(self, entity_ids: Collection[str] | None = None) -> None:
        """Resume Dimsome control for selected lights."""
        selected = set(self.lights if entity_ids is None else entity_ids)
        if not selected:
            return
        for entity_id, runtime in self.lights.items():
            if entity_id not in selected:
                continue
            _clear_override(runtime)
            runtime.last_target = None
            runtime.pending_target = None
            runtime.expected_target = None
            _cancel_grace_resume(runtime)
        await self.async_tick()

    async def async_set_enabled(self, entity_id: str, enabled: bool) -> None:
        """Enable or indefinitely pause Dimsome control for one light."""
        runtime = self.lights[entity_id]
        runtime.config = replace(runtime.config, enabled=enabled)
        runtime.last_target = None
        runtime.pending_target = None
        runtime.stood_down = not enabled
        runtime.stood_down_window = None
        if not enabled:
            _cancel_grace_resume(runtime)
        await self.async_tick()

    def manual_overrides(self) -> dict[str, RampWindow]:
        """Return the ramp window each manually overridden light stood down for."""
        return {
            entity_id: runtime.stood_down_window
            for entity_id, runtime in self.lights.items()
            if runtime.stood_down and runtime.stood_down_window is not None
        }

    def restore_manual_overrides(self, overrides: dict[str, RampWindow]) -> None:
        """Carry manual overrides over from the controller this one replaces.

        A config save reloads the entry; without this the new controller would
        overwrite lights the user adjusted during the running ramp.  An
        override for a ramp that has since ended is cleared on the next tick.
        """
        for entity_id, window in overrides.items():
            runtime = self.lights.get(entity_id)
            if runtime is None or not runtime.config.enabled:
                continue
            runtime.stood_down = True
            runtime.stood_down_window = window

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
        window = active_window(runtime.config, now, self._civil_lookup)
        target = target_for_now(runtime.config, now, self._civil_lookup)
        return {
            "enabled": runtime.config.enabled,
            "status": self._status_for_runtime(runtime, window),
            "stood_down": runtime.stood_down,
            "stood_down_window": _window_status(runtime.stood_down_window),
            "active_window": _window_status(window),
            "next_window_start": _datetime_status(
                next_window_start(runtime.config, now, self._civil_lookup)
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
        if self._stopped:
            return
        now = dt_util.now()
        any_active = False
        next_start = None
        for runtime in self.lights.values():
            if self._stopped:
                return
            if not runtime.config.enabled:
                runtime.last_target = None
                self._record_decision(runtime, "disabled", now)
                continue
            state = self.hass.states.get(runtime.config.entity_id)
            window = active_window(runtime.config, now, self._civil_lookup)
            if should_clear_manual_override_for_window(
                stood_down=runtime.stood_down,
                stood_down_window=runtime.stood_down_window,
                window=window,
            ):
                _clear_override(runtime)
                _LOGGER.debug(
                    "Resuming %s for new ramp window", runtime.config.entity_id
                )
            candidate_start = next_window_start(runtime.config, now, self._civil_lookup)
            if candidate_start is not None and (
                next_start is None or candidate_start < next_start
            ):
                next_start = candidate_start
            target = target_for_now(runtime.config, now, self._civil_lookup)
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
            try:
                await self._async_apply_target(runtime, target)
            except Exception:
                _LOGGER.exception(
                    "Failed to apply Dimsome target for %s",
                    runtime.config.entity_id,
                )
                self._record_decision(runtime, "apply_failed", now)
                continue
            self._record_decision(runtime, "applied_target", now)

        if self._stopped:
            return
        if any_active:
            self._cancel_wake_timer()
            if self._ramp_unsub is None:
                self._ramp_unsub = async_track_time_interval(
                    self.hass, self.async_tick, RAMP_INTERVAL
                )
        else:
            if self._ramp_unsub is not None:
                self._ramp_unsub()
                self._ramp_unsub = None
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
        if next_start is None or self._stopped:
            return
        delay = max(0.0, next_start.timestamp() - now.timestamp())
        self._wake_unsub = async_call_later(self.hass, delay, self.async_tick)

    @callback
    def _async_light_changed(self, event: Event) -> None:
        """Handle controlled light state changes."""
        entity_id = event.data[ATTR_ENTITY_ID]
        runtime = self.lights[entity_id]
        if self._stopped or not runtime.config.enabled:
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
            if not self._can_apply_target(runtime):
                return
            _clear_override(runtime)
            runtime.last_target = None
            self._create_turn_on_task(runtime)
            return

        now = dt_util.now()
        if new_state.state != STATE_ON:
            runtime.last_target = None
            window = active_window(runtime.config, now, self._civil_lookup)
            if (
                new_state.state == STATE_OFF
                and window is not None
                and should_stand_down_for_context(
                    new_state.context,
                    set(self._automation_context_ids),
                    self._native_user_ids,
                )
            ):
                self._stand_down(runtime, window)
            return
        if getattr(new_state.context, "id", None) == runtime.last_apply_context_id:
            return
        window = active_window(runtime.config, now, self._civil_lookup)
        manual_context = should_stand_down_for_context(
            new_state.context,
            set(self._automation_context_ids),
            self._native_user_ids,
        )
        if old_state is not None and old_state.state == STATE_OFF:
            runtime.last_target = None
            if window is not None and manual_context:
                self._stand_down(runtime, window)
                return
            if not self._can_apply_target(runtime):
                return
            _clear_override(runtime)
            self._create_turn_on_task(runtime)
            return

        # Known user actions take precedence over state-echo heuristics.
        if should_ignore_state_change(
            in_flight=runtime.in_flight,
            now=now,
            ignore_updates_until=runtime.ignore_updates_until,
            expected_target=runtime.expected_target,
            attrs=new_state.attributes,
        ) and not getattr(new_state.context, "user_id", None):
            return
        if window is None:
            return
        if not manual_context:
            _LOGGER.debug("Ignoring automation-originated change for %s", entity_id)
            return
        _LOGGER.debug("Standing down %s after external light change", entity_id)
        self._stand_down(runtime, window)

    @callback
    def _async_automation_or_script_started(self, event: Event) -> None:
        """Remember automation/script contexts so they do not stop active ramps."""
        context_id = event.context.id
        if context_id is None:
            return
        self._automation_context_ids.append(context_id)
        del self._automation_context_ids[:-MAX_AUTOMATION_CONTEXTS]

    def _create_turn_on_task(self, runtime: LightRuntime) -> None:
        """Run the turn-on handler in a task that stop() can cancel."""
        task = self.hass.async_create_task(self._async_handle_turn_on(runtime))
        self._turn_on_tasks.add(task)
        task.add_done_callback(self._turn_on_tasks.discard)

    async def _async_handle_turn_on(self, runtime: LightRuntime) -> None:
        """Apply the current expected value when a light turns on."""
        if runtime.config.settle_delay > timedelta(0):
            await asyncio.sleep(runtime.config.settle_delay.total_seconds())
        state = self.hass.states.get(runtime.config.entity_id)
        if state is None or state.state != STATE_ON:
            return
        if not self._can_apply_target(runtime):
            return
        now = dt_util.now()
        target = target_for_now(runtime.config, now, self._civil_lookup)
        if target is not None:
            await self._async_apply_target(runtime, target)
            await self._async_verify_turn_on_target(runtime)

    async def _async_verify_turn_on_target(self, runtime: LightRuntime) -> None:
        """Reapply turn-on targets that were lost to device restore timing."""
        await asyncio.sleep(IGNORE_UPDATE_WINDOW.total_seconds())
        if not self._can_apply_target(runtime):
            return
        now = dt_util.now()
        # Verify against the current target: during a ramp it has moved on
        # since turn-on, and the moved-on value is what the light should show.
        current = target_for_now(runtime.config, now, self._civil_lookup)
        if current is None:
            return
        state = self.hass.states.get(runtime.config.entity_id)
        if state is None or state.state != STATE_ON:
            return
        context = getattr(state, "context", None)
        window = active_window(runtime.config, now, self._civil_lookup)
        if (
            window is None
            and getattr(context, "id", None) != runtime.last_apply_context_id
            and should_stand_down_for_context(
                context,
                set(self._automation_context_ids),
                self._native_user_ids,
            )
        ):
            return
        if target_matches_state(current, state.attributes):
            return
        runtime.last_target = None
        await self._async_apply_target(runtime, current)

    def _can_apply_target(self, runtime: LightRuntime) -> bool:
        """Recheck permission after waits without extending an old override."""
        if self._stopped or not runtime.config.enabled:
            return False
        if not runtime.stood_down:
            return True
        window = active_window(runtime.config, dt_util.now(), self._civil_lookup)
        return not should_skip_for_manual_override(
            stood_down=runtime.stood_down, window=window
        ) or should_clear_manual_override_for_window(
            stood_down=runtime.stood_down,
            stood_down_window=runtime.stood_down_window,
            window=window,
        )

    async def _async_apply_target(
        self, runtime: LightRuntime, target: LightTarget
    ) -> None:
        """Apply a target with per-light backpressure."""
        if not self._can_apply_target(runtime):
            runtime.pending_target = None
            return
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
        context = Context()
        runtime.last_apply_context_id = context.id
        try:
            applied = await self._async_call_light(runtime, target, context)
            if not applied or not self._can_apply_target(runtime):
                runtime.last_target = None
                runtime.pending_target = None
                return
            runtime.last_target = target
            runtime.expected_target = target
            runtime.ignore_updates_until = dt_util.now() + IGNORE_UPDATE_WINDOW
        except Exception:
            runtime.pending_target = None
            raise
        finally:
            runtime.in_flight = False
        if runtime.pending_target is not None:
            pending = runtime.pending_target
            runtime.pending_target = None
            await self._async_apply_target(runtime, pending)

    async def _async_call_light(
        self, runtime: LightRuntime, target: LightTarget, context: Context
    ) -> bool:
        """Call light.turn_on for brightness and optional color."""
        base_data: dict[str, Any] = {
            ATTR_ENTITY_ID: runtime.config.entity_id,
            ATTR_BRIGHTNESS: brightness_pct_to_ha(target.brightness_pct),
        }
        color_data = color_service_data(target)
        payloads = (
            split_turn_on_service_data(runtime.config.entity_id, target)
            if color_data and runtime.config.split_turn_on_calls
            else [{**base_data, **color_data}]
        )
        for index, data in enumerate(payloads):
            if index > 0:
                await asyncio.sleep(SPLIT_TURN_ON_DELAY)
            state = self.hass.states.get(runtime.config.entity_id)
            if (
                not self._can_apply_target(runtime)
                or state is None
                or state.state != STATE_ON
            ):
                return False
            await self.hass.services.async_call(
                LIGHT_DOMAIN, "turn_on", data, blocking=True, context=context
            )
        return True

    def _stand_down(self, runtime: LightRuntime, window: RampWindow) -> None:
        """Leave the light alone for the rest of window after a manual change."""
        runtime.stood_down = True
        runtime.stood_down_window = window
        self._schedule_grace_resume(runtime)

    def _schedule_grace_resume(self, runtime: LightRuntime) -> None:
        """Schedule optional automatic resume after a manual override."""
        _cancel_grace_resume(runtime)
        if (
            runtime.config.override_resume_mode is not OverrideResumeMode.AFTER_GRACE_PERIOD
            or runtime.config.override_grace_period is None
        ):
            return

        async def _resume(_: Any) -> None:
            current_window = active_window(runtime.config, dt_util.now(), self._civil_lookup)
            runtime.grace_unsub = None
            if current_window == runtime.stood_down_window:
                return
            _clear_override(runtime)
            await self.async_tick()

        runtime.grace_unsub = async_call_later(
            self.hass, runtime.config.override_grace_period.total_seconds(), _resume
        )

    def _record_decision(
        self, runtime: LightRuntime, decision: str, at: datetime
    ) -> None:
        """Remember the most recent tick decision for diagnostics."""
        runtime.last_decision = decision
        runtime.last_decision_at = at


def _clear_override(runtime: LightRuntime) -> None:
    runtime.stood_down = False
    runtime.stood_down_window = None


def _cancel_grace_resume(runtime: LightRuntime) -> None:
    if runtime.grace_unsub is not None:
        runtime.grace_unsub()
        runtime.grace_unsub = None


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


def _color_status(color: ColorTarget | None) -> dict[str, Any] | None:
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


async def async_resume_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle dimsome.resume."""
    entity_ids = call.data.get(ATTR_ENTITY_ID)
    if ATTR_ENTITY_ID not in call.data:
        selected = None
    elif isinstance(entity_ids, str):
        selected = {entity_ids}
    else:
        selected = set(entity_ids)
        if not selected:
            return
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
