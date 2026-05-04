"""Home Assistant runtime controller for Dimsome."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, DOMAIN as LIGHT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, ServiceCall, State, callback
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
    should_ignore_state_change,
    target_for_now,
)
from .models import (
    ColorMode,
    LightRuntime,
    LightTarget,
    OverrideResumeMode,
    ResolvedLightConfig,
    ScheduleType,
    SequenceKind,
    SunEvent,
)

_LOGGER = logging.getLogger(__name__)

RAMP_INTERVAL = timedelta(seconds=15)
IGNORE_UPDATE_WINDOW = timedelta(seconds=3)
SUN_ENTITY_ID = "sun.sun"


class DimsomeController:
    """Own all mutable runtime state for one Dimsome config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        light_configs: list[ResolvedLightConfig],
    ) -> None:
        """Initialize the controller."""
        self.hass = hass
        self.entry_id = entry_id
        self.lights = {
            config.entity_id: LightRuntime(config=config) for config in light_configs
        }
        self._unsubs: list[Any] = []
        self._ramp_unsub: Any | None = None
        self._sun_samples: list[SunElevationSample] = []

    async def async_start(self) -> None:
        """Start listeners and reconstruct current phase."""
        if not self.lights:
            return
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, list(self.lights), self._async_light_changed
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
            self._record_sun_sample(self.hass.states.get(SUN_ENTITY_ID))

        await self.async_tick()

    async def async_stop(self) -> None:
        """Stop listeners and pending timers."""
        if self._ramp_unsub is not None:
            self._ramp_unsub()
            self._ramp_unsub = None
        for runtime in self.lights.values():
            if runtime.grace_unsub is not None:
                runtime.grace_unsub()
                runtime.grace_unsub = None
        while self._unsubs:
            self._unsubs.pop()()

    async def async_resume(self, entity_ids: list[str] | None = None) -> None:
        """Resume Dimsome control for selected lights."""
        selected = set(entity_ids or self.lights)
        for entity_id, runtime in self.lights.items():
            if entity_id not in selected:
                continue
            runtime.stood_down = False
            if runtime.grace_unsub is not None:
                runtime.grace_unsub()
                runtime.grace_unsub = None
        await self.async_tick()

    async def async_tick(self, *_: Any) -> None:
        """Apply current targets and manage the active ramp timer."""
        now = dt_util.now()
        any_active = False
        for runtime in self.lights.values():
            state = self.hass.states.get(runtime.config.entity_id)
            window = active_window(runtime.config, now, self._sun_samples)
            target = target_for_now(runtime.config, now, self._sun_samples)
            if target is None:
                runtime.last_target = None
                continue
            if window is not None:
                any_active = True
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, STATE_OFF):
                continue
            if runtime.stood_down:
                continue
            await self._async_apply_target(runtime, target)

        if any_active and self._ramp_unsub is None:
            self._ramp_unsub = async_track_time_interval(
                self.hass, self.async_tick, RAMP_INTERVAL
            )
        elif not any_active and self._ramp_unsub is not None:
            self._ramp_unsub()
            self._ramp_unsub = None

    @callback
    def _async_light_changed(self, event: Event) -> None:
        """Handle controlled light state changes."""
        entity_id = event.data[ATTR_ENTITY_ID]
        runtime = self.lights[entity_id]
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        if new_state is None:
            return

        if old_state is not None and old_state.state == STATE_OFF and new_state.state == STATE_ON:
            runtime.stood_down = False
            self.hass.async_create_task(self._async_handle_turn_on(runtime))
            return

        now = dt_util.now()
        if new_state.state != STATE_ON:
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
        runtime.stood_down = True
        _LOGGER.debug("Standing down %s after external light change", entity_id)
        self._schedule_grace_resume(runtime)

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
        now = dt_util.now()
        target = target_for_now(runtime.config, now, self._sun_samples)
        if target is not None:
            await self._async_apply_target(runtime, target)

    async def _async_apply_target(
        self, runtime: LightRuntime, target: LightTarget
    ) -> None:
        """Apply a target with per-light backpressure."""
        if runtime.last_target == target:
            return
        if runtime.in_flight:
            runtime.pending_target = target
            return
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
            await self.hass.services.async_call(
                LIGHT_DOMAIN, "turn_on", base_data, blocking=True
            )
            await self.hass.services.async_call(
                LIGHT_DOMAIN,
                "turn_on",
                {ATTR_ENTITY_ID: runtime.config.entity_id, **color_data},
                blocking=True,
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
        self._sun_samples.append(SunElevationSample(dt_util.now(), elevation))
        cutoff = dt_util.now() - timedelta(days=2)
        self._sun_samples = [sample for sample in self._sun_samples if sample.at >= cutoff]


def color_service_data(target: LightTarget) -> dict[str, Any]:
    """Convert a target color into light.turn_on service data."""
    if target.color is None:
        return {}
    if target.color.mode is ColorMode.COLOR_TEMP_KELVIN:
        return {ColorMode.COLOR_TEMP_KELVIN.value: target.color.value}
    return {}


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


async def async_resume_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle dimsome.resume."""
    entity_ids = call.data.get(ATTR_ENTITY_ID)
    if isinstance(entity_ids, str):
        selected = [entity_ids]
    else:
        selected = list(entity_ids) if entity_ids else None
    for controller in hass.data.get(DOMAIN, {}).values():
        await controller.async_resume(selected)


def register_services(hass: HomeAssistant) -> None:
    """Register Dimsome services once."""
    if hass.services.has_service(DOMAIN, SERVICE_RESUME):
        return
    hass.services.async_register(DOMAIN, SERVICE_RESUME, async_resume_service)
