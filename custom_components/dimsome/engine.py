"""Pure scheduling and ramp logic for Dimsome.

Civil dawn and dusk are deterministic for any date, so the engine never
samples sun elevation or reconstructs threshold crossings.  Callers supply a
``civil_lookup`` that resolves a :class:`SunEvent` on a given date to its
concrete datetime (Home Assistant's astral helpers in production, a stub in
tests).  Everything here is a pure function of ``(config, now, civil_lookup)``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta

from .models import (
    ColorMode,
    ColorTarget,
    LightTarget,
    RampWindow,
    ResolvedLightConfig,
    ScheduleConfig,
    ScheduleType,
    SequenceKind,
    SunEvent,
    parse_time as parse_time,  # re-exported; lives in models so validation can use it
)

BRIGHTNESS_TOLERANCE = 2
COLOR_TEMP_TOLERANCE = 50

#: Resolve a civil sun event on a calendar date to its concrete datetime.
CivilLookup = Callable[[SunEvent, date], "datetime | None"]


def brightness_pct_to_ha(value: int) -> int:
    """Convert percentage brightness to HA's 1-255 scale."""
    return max(1, min(255, round(value * 255 / 100)))


def brightness_ha_to_pct(value: int | None) -> int | None:
    """Convert HA brightness to percentage."""
    if value is None:
        return None
    return max(1, min(100, round(value * 100 / 255)))


def split_turn_on_service_data(
    entity_id: str, target: LightTarget
) -> list[dict[str, object]]:
    """Return split light.turn_on payloads with brightness applied last."""
    brightness_data: dict[str, object] = {
        "entity_id": entity_id,
        "brightness": brightness_pct_to_ha(target.brightness_pct),
    }
    if target.color is None:
        return [brightness_data]
    if target.color.mode is ColorMode.COLOR_TEMP_KELVIN:
        return [
            {"entity_id": entity_id, ColorMode.COLOR_TEMP_KELVIN.value: target.color.value},
            brightness_data,
        ]
    return [brightness_data]


def schedule_start(
    schedule: ScheduleConfig,
    day: datetime,
    civil_lookup: CivilLookup,
) -> datetime | None:
    """Return the concrete start datetime for a schedule on day."""
    if schedule.type is ScheduleType.FIXED_TIME:
        assert schedule.at is not None
        return datetime.combine(day.date(), parse_time(schedule.at), tzinfo=day.tzinfo)
    assert schedule.event is not None
    return civil_lookup(schedule.event, day.date())


def candidate_windows(
    config: ResolvedLightConfig,
    now: datetime,
    civil_lookup: CivilLookup,
) -> list[RampWindow]:
    """Build nearby ramp windows around now."""
    windows: list[RampWindow] = []
    for offset in (-1, 0, 1):
        day = now + timedelta(days=offset)
        dim_start = schedule_start(config.dim_schedule, day, civil_lookup)
        if dim_start is not None:
            windows.append(
                RampWindow(
                    sequence=SequenceKind.DIM,
                    start=dim_start,
                    end=dim_start + config.ramp_duration,
                )
            )
        brighten_start = schedule_start(config.brighten_schedule, day, civil_lookup)
        if brighten_start is not None:
            windows.append(
                RampWindow(
                    sequence=SequenceKind.BRIGHTEN,
                    start=brighten_start,
                    end=brighten_start + config.ramp_duration,
                )
            )
    return sorted(windows, key=lambda window: window.start)


def active_window(
    config: ResolvedLightConfig,
    now: datetime,
    civil_lookup: CivilLookup,
) -> RampWindow | None:
    """Return the currently active ramp window, if any.

    When windows overlap (ramps longer than the gap between schedules), the
    most recently started window wins.
    """
    for window in reversed(candidate_windows(config, now, civil_lookup)):
        if window.start <= now <= window.end:
            return window
    return None


def next_window_start(
    config: ResolvedLightConfig,
    now: datetime,
    civil_lookup: CivilLookup,
) -> datetime | None:
    """Return the next known ramp start after now."""
    starts = [
        window.start
        for window in candidate_windows(config, now, civil_lookup)
        if window.start > now
    ]
    return min(starts, default=None)


def _last_completed_window(
    config: ResolvedLightConfig,
    now: datetime,
    civil_lookup: CivilLookup,
) -> RampWindow | None:
    """Return the most recently finished ramp window before now."""
    previous = [
        window
        for window in candidate_windows(config, now, civil_lookup)
        if window.end <= now
    ]
    return previous[-1] if previous else None


def is_low_plateau(
    config: ResolvedLightConfig,
    now: datetime,
    civil_lookup: CivilLookup,
) -> bool:
    """Return whether now is after dimming and before the next brightening."""
    last = _last_completed_window(config, now, civil_lookup)
    return last is not None and last.sequence is SequenceKind.DIM


def is_high_plateau(
    config: ResolvedLightConfig,
    now: datetime,
    civil_lookup: CivilLookup,
) -> bool:
    """Return whether now is after brightening and before the next dimming."""
    last = _last_completed_window(config, now, civil_lookup)
    return last is not None and last.sequence is SequenceKind.BRIGHTEN


def interpolate(start: int, end: int, progress: float) -> int:
    """Linearly interpolate integers."""
    return round(start + (end - start) * max(0.0, min(1.0, progress)))


def target_for_window(config: ResolvedLightConfig, window: RampWindow, now: datetime) -> LightTarget:
    """Compute the expected target for a ramp window."""
    progress = (now - window.start) / (window.end - window.start)
    if window.sequence is SequenceKind.DIM:
        brightness = interpolate(
            config.max_brightness_pct, config.min_brightness_pct, progress
        )
        color = interpolate_color(config.max_color, config.min_color, progress)
    else:
        brightness = interpolate(
            config.min_brightness_pct, config.max_brightness_pct, progress
        )
        color = interpolate_color(config.min_color, config.max_color, progress)
    return LightTarget(brightness_pct=brightness, color=color)


def low_plateau_target(config: ResolvedLightConfig) -> LightTarget:
    """Return the target for the low plateau."""
    return LightTarget(config.min_brightness_pct, config.min_color)


def high_plateau_target(config: ResolvedLightConfig) -> LightTarget:
    """Return the target for the high plateau."""
    return LightTarget(config.max_brightness_pct, config.max_color)


def target_for_now(
    config: ResolvedLightConfig,
    now: datetime,
    civil_lookup: CivilLookup,
) -> LightTarget | None:
    """Return the target Dimsome should enforce right now, if any."""
    window = active_window(config, now, civil_lookup)
    if window is not None:
        return target_for_window(config, window, now)
    if is_low_plateau(config, now, civil_lookup):
        return low_plateau_target(config)
    if is_high_plateau(config, now, civil_lookup):
        return high_plateau_target(config)
    return None


def interpolate_color(
    start: ColorTarget | None, end: ColorTarget | None, progress: float
) -> ColorTarget | None:
    """Interpolate supported color targets."""
    if start is None or end is None:
        return end if progress >= 1 else start
    if start.mode is not end.mode:
        return end if progress >= 1 else start
    if start.mode is ColorMode.COLOR_TEMP_KELVIN:
        return ColorTarget(start.mode, interpolate(start.value, end.value, progress))
    return None


def target_matches_state(target: LightTarget, attrs: dict[str, object]) -> bool:
    """Return whether HA state attrs are close enough to an expected target."""
    current_pct = brightness_ha_to_pct(attrs.get("brightness"))  # type: ignore[arg-type]
    if current_pct is None or abs(current_pct - target.brightness_pct) > BRIGHTNESS_TOLERANCE:
        return False
    if target.color is None:
        return True
    if target.color.mode is ColorMode.COLOR_TEMP_KELVIN:
        current_kelvin = attrs.get("color_temp_kelvin")
        if current_kelvin is None:
            return True
        return abs(int(current_kelvin) - target.color.value) <= COLOR_TEMP_TOLERANCE
    return False


def should_ignore_state_change(
    *,
    in_flight: bool,
    now: datetime,
    ignore_updates_until: datetime | None,
    expected_target: LightTarget | None,
    attrs: dict[str, object],
) -> bool:
    """Return whether a state change should be treated as Dimsome's own update."""
    if in_flight:
        return True
    # A report that matches what Dimsome last sent is Dimsome's own update no
    # matter how late it arrives; slow devices must not stand themselves down.
    if (
        expected_target is not None
        and attrs.get("brightness") is not None
        and target_matches_state(expected_target, attrs)
    ):
        return True
    if ignore_updates_until is None or now > ignore_updates_until:
        return False
    if expected_target is None:
        return True
    if attrs.get("brightness") is None:
        return True
    return False


def should_stand_down_for_context(
    context: object,
    automation_context_ids: set[str],
    native_user_ids: frozenset[str] = frozenset(),
) -> bool:
    """Return whether an external state change should be treated as manual."""
    user_id = getattr(context, "user_id", None)
    if user_id is not None:
        return user_id not in native_user_ids
    context_id = getattr(context, "id", None)
    parent_id = getattr(context, "parent_id", None)
    if context_id in automation_context_ids or parent_id in automation_context_ids:
        return False
    return True


def should_skip_for_manual_override(
    *, stood_down: bool, window: RampWindow | None
) -> bool:
    """Return whether a manual override should defer Dimsome's current target."""
    return stood_down and window is not None


def should_clear_manual_override_for_window(
    *, stood_down: bool, stood_down_window: RampWindow | None, window: RampWindow | None
) -> bool:
    """Return whether a manual override belongs to an older ramp window."""
    return stood_down and stood_down_window is not None and stood_down_window != window
