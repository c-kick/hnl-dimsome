"""Pure scheduling and ramp logic for Dimsome."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import isclose
from typing import Any

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
)

CIVIL_ELEVATION = -6.0
BRIGHTNESS_TOLERANCE = 2
COLOR_TEMP_TOLERANCE = 50


@dataclass(frozen=True)
class SunElevationSample:
    """One sun elevation sample."""

    at: datetime
    elevation: float


def parse_time(value: str) -> time:
    """Parse HH:MM or HH:MM:SS."""
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return time(parts[0], parts[1])
    if len(parts) == 3:
        return time(parts[0], parts[1], parts[2])
    raise ValueError(f"Invalid time: {value}")


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


def civil_event_time(
    samples: list[SunElevationSample], event: SunEvent
) -> datetime | None:
    """Estimate civil dawn/dusk from sun.sun elevation samples."""
    previous: SunElevationSample | None = None
    for sample in samples:
        if previous is None:
            previous = sample
            continue
        prev_delta = previous.elevation - CIVIL_ELEVATION
        next_delta = sample.elevation - CIVIL_ELEVATION
        crosses = prev_delta == 0 or next_delta == 0 or (prev_delta < 0 < next_delta) or (
            prev_delta > 0 > next_delta
        )
        if not crosses:
            previous = sample
            continue
        rising = sample.elevation > previous.elevation
        if event is SunEvent.CIVIL_DAWN and not rising:
            previous = sample
            continue
        if event is SunEvent.CIVIL_DUSK and rising:
            previous = sample
            continue
        if isclose(sample.elevation, previous.elevation):
            return sample.at
        ratio = (CIVIL_ELEVATION - previous.elevation) / (
            sample.elevation - previous.elevation
        )
        return previous.at + (sample.at - previous.at) * ratio
    return None


def reconstructed_civil_samples(
    *,
    elevation: float,
    next_dawn: object,
    next_dusk: object,
    now: datetime | None = None,
) -> list[SunElevationSample]:
    """Reconstruct the last civil crossing from sun.sun's next event attributes."""
    is_after_dusk = elevation < CIVIL_ELEVATION
    value = next_dusk if is_after_dusk else next_dawn
    if not isinstance(value, str):
        return _fallback_civil_night_samples(is_after_dusk, next_dawn)
    try:
        next_event = datetime.fromisoformat(value)
    except ValueError:
        return _fallback_civil_night_samples(is_after_dusk, next_dawn)
    previous_event = next_event - timedelta(days=1)
    if now is not None and previous_event > now:
        previous_event = now
    before_elevation = CIVIL_ELEVATION + 1 if is_after_dusk else CIVIL_ELEVATION - 1
    return [
        SunElevationSample(previous_event - timedelta(seconds=1), before_elevation),
        SunElevationSample(previous_event, CIVIL_ELEVATION),
    ]


def _fallback_civil_night_samples(
    is_after_dusk: bool, next_dawn: object
) -> list[SunElevationSample]:
    """Infer a previous dusk marker when current elevation proves civil night."""
    if not is_after_dusk or not isinstance(next_dawn, str):
        return []
    try:
        next_event = datetime.fromisoformat(next_dawn)
    except ValueError:
        return []
    previous_event = next_event - timedelta(hours=8)
    return [
        SunElevationSample(previous_event - timedelta(seconds=1), CIVIL_ELEVATION + 1),
        SunElevationSample(previous_event, CIVIL_ELEVATION),
    ]


def upcoming_civil_samples(
    *, next_dawn: object, next_dusk: object
) -> list[SunElevationSample]:
    """Reconstruct upcoming civil crossings from sun.sun's next event attributes."""
    samples: list[SunElevationSample] = []
    for value, before_elevation in (
        (next_dawn, CIVIL_ELEVATION - 1),
        (next_dusk, CIVIL_ELEVATION + 1),
    ):
        if not isinstance(value, str):
            continue
        try:
            next_event = datetime.fromisoformat(value)
        except ValueError:
            continue
        samples.extend(
            [
                SunElevationSample(next_event - timedelta(seconds=1), before_elevation),
                SunElevationSample(next_event, CIVIL_ELEVATION),
            ]
        )
    return samples


def schedule_start(
    schedule: ScheduleConfig,
    day: datetime,
    sun_samples: list[SunElevationSample],
) -> datetime | None:
    """Return the concrete start datetime for a schedule on day."""
    if schedule.type is ScheduleType.FIXED_TIME:
        assert schedule.at is not None
        return datetime.combine(day.date(), parse_time(schedule.at), tzinfo=day.tzinfo)
    assert schedule.event is not None
    day_samples = [sample for sample in sun_samples if sample.at.date() == day.date()]
    return civil_event_time(day_samples, schedule.event)


def candidate_windows(
    config: ResolvedLightConfig,
    now: datetime,
    sun_samples: list[SunElevationSample],
) -> list[RampWindow]:
    """Build nearby ramp windows around now."""
    windows: list[RampWindow] = []
    for offset in (-1, 0, 1):
        day = now + timedelta(days=offset)
        dim_start = schedule_start(config.dim_schedule, day, sun_samples)
        if dim_start is not None:
            windows.append(
                RampWindow(
                    sequence=SequenceKind.DIM,
                    start=dim_start,
                    end=dim_start + config.ramp_duration,
                )
            )
        brighten_start = schedule_start(config.brighten_schedule, day, sun_samples)
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
    sun_samples: list[SunElevationSample],
) -> RampWindow | None:
    """Return the currently active ramp window, if any."""
    for window in candidate_windows(config, now, sun_samples):
        if window.start <= now <= window.end:
            return window
    return None


def next_window_start(
    config: ResolvedLightConfig,
    now: datetime,
    sun_samples: list[SunElevationSample],
) -> datetime | None:
    """Return the next known ramp start after now."""
    starts = [
        window.start
        for window in candidate_windows(config, now, sun_samples)
        if window.start > now
    ]
    return min(starts, default=None)


def latest_sun_elevation(
    now: datetime, sun_samples: list[SunElevationSample]
) -> float | None:
    """Return the latest known sun elevation at or before now."""
    previous_samples = [sample for sample in sun_samples if sample.at <= now]
    if not previous_samples:
        return None
    latest = max(previous_samples, key=lambda sample: sample.at)
    return latest.elevation


def is_civil_night(now: datetime, sun_samples: list[SunElevationSample]) -> bool:
    """Return whether the latest sun elevation is below civil twilight."""
    elevation = latest_sun_elevation(now, sun_samples)
    return elevation is not None and elevation < CIVIL_ELEVATION


def is_civil_day(now: datetime, sun_samples: list[SunElevationSample]) -> bool:
    """Return whether the latest sun elevation is at or above civil twilight."""
    elevation = latest_sun_elevation(now, sun_samples)
    return elevation is not None and elevation >= CIVIL_ELEVATION


def is_low_plateau(
    config: ResolvedLightConfig,
    now: datetime,
    sun_samples: list[SunElevationSample],
) -> bool:
    """Return whether now is after dimming and before brightening."""
    if (
        config.dim_schedule.type is ScheduleType.CIVIL_SUN
        and is_civil_night(now, sun_samples)
    ):
        return True
    previous_windows = [
        window for window in candidate_windows(config, now, sun_samples) if window.end <= now
    ]
    if not previous_windows:
        return False
    return previous_windows[-1].sequence is SequenceKind.DIM


def is_high_plateau(
    config: ResolvedLightConfig,
    now: datetime,
    sun_samples: list[SunElevationSample],
) -> bool:
    """Return whether now is after brightening and before dimming."""
    if (
        config.brighten_schedule.type is ScheduleType.CIVIL_SUN
        and is_civil_day(now, sun_samples)
    ):
        return True
    previous_windows = [
        window for window in candidate_windows(config, now, sun_samples) if window.end <= now
    ]
    if not previous_windows:
        return False
    return previous_windows[-1].sequence is SequenceKind.BRIGHTEN


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
    sun_samples: list[SunElevationSample],
) -> LightTarget | None:
    """Return the target Dimsome should enforce right now, if any."""
    window = active_window(config, now, sun_samples)
    if window is not None:
        return target_for_window(config, window, now)
    if is_low_plateau(config, now, sun_samples):
        return low_plateau_target(config)
    if is_high_plateau(config, now, sun_samples):
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
    if ignore_updates_until is None or now > ignore_updates_until:
        return False
    if expected_target is None:
        return True
    return target_matches_state(expected_target, attrs)


def should_stand_down_for_context(
    context: Any, automation_context_ids: set[str]
) -> bool:
    """Return whether an external state change should be treated as manual."""
    user_id = getattr(context, "user_id", None)
    if user_id is not None:
        return True
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
