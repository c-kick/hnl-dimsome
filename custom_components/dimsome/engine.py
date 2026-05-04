"""Pure scheduling and ramp logic for Dimsome."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import isclose

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


def is_low_plateau(
    config: ResolvedLightConfig,
    now: datetime,
    sun_samples: list[SunElevationSample],
) -> bool:
    """Return whether now is after dimming and before brightening."""
    previous_windows = [
        window for window in candidate_windows(config, now, sun_samples) if window.end <= now
    ]
    if not previous_windows:
        return False
    return previous_windows[-1].sequence is SequenceKind.DIM


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
            return False
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
