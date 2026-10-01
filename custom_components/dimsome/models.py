"""Typed data models for Dimsome."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Any

from .const import (
    CONF_ENTITY_ID,
    CONF_BRIGHTEN_SCHEDULE,
    CONF_DIM_SCHEDULE,
    CONF_ENABLED,
    CONF_GLOBAL,
    CONF_LIGHTS,
    CONF_NATIVE_USER_IDS,
    CONF_MAX_BRIGHTNESS_PCT,
    CONF_MAX_COLOR,
    CONF_MIN_BRIGHTNESS_PCT,
    CONF_MIN_COLOR,
    CONF_OVERRIDE_GRACE_PERIOD,
    CONF_OVERRIDE_RESUME_MODE,
    CONF_APPLY_ON_RECOVERED_ON,
    CONF_RAMP_DURATION,
    CONF_SETTLE_DELAY,
    CONF_SPLIT_TURN_ON_CALLS,
)


class SequenceKind(StrEnum):
    """A Dimsome light sequence."""

    DIM = "dim"
    BRIGHTEN = "brighten"


class ScheduleType(StrEnum):
    """Supported schedule sources."""

    FIXED_TIME = "fixed_time"
    CIVIL_SUN = "civil_sun"


class SunEvent(StrEnum):
    """Civil sun events a schedule can start at."""

    CIVIL_DAWN = "civil_dawn"
    CIVIL_DUSK = "civil_dusk"


class ColorMode(StrEnum):
    """Supported color target modes."""

    COLOR_TEMP_KELVIN = "color_temp_kelvin"


class OverrideResumeMode(StrEnum):
    """How Dimsome resumes after a manual override."""

    MANUAL_ONLY = "manual_only"
    AFTER_GRACE_PERIOD = "after_grace_period"


@dataclass(frozen=True)
class ColorTarget:
    """A typed light color target."""

    mode: ColorMode
    value: int


@dataclass(frozen=True)
class ScheduleConfig:
    """A schedule for one sequence direction."""

    type: ScheduleType
    at: str | None = None
    event: SunEvent | None = None
    # Optional clock-time bounds on a civil-sun start: brighten "no later
    # than" 07:00, dim "no earlier than" 21:00.  Ignored for fixed times.
    not_later_than: str | None = None
    not_earlier_than: str | None = None


@dataclass(frozen=True)
class ResolvedLightConfig:
    """Effective configuration for one controlled light."""

    entity_id: str
    enabled: bool
    min_brightness_pct: int
    max_brightness_pct: int
    min_color: ColorTarget | None
    max_color: ColorTarget | None
    dim_schedule: ScheduleConfig
    brighten_schedule: ScheduleConfig
    ramp_duration: timedelta
    override_resume_mode: OverrideResumeMode
    override_grace_period: timedelta | None
    split_turn_on_calls: bool
    apply_on_recovered_on: bool
    settle_delay: timedelta = timedelta(milliseconds=500)


@dataclass(frozen=True)
class LightTarget:
    """A computed target for a light."""

    brightness_pct: int
    color: ColorTarget | None = None


@dataclass(frozen=True)
class RampWindow:
    """A concrete ramp window."""

    sequence: SequenceKind
    start: datetime
    end: datetime


@dataclass
class LightRuntime:
    """Mutable runtime state for one controlled light."""

    config: ResolvedLightConfig
    stood_down: bool = False
    stood_down_window: RampWindow | None = None
    ignore_updates_until: datetime | None = None
    expected_target: LightTarget | None = None
    last_target: LightTarget | None = None
    in_flight: bool = False
    pending_target: LightTarget | None = None
    grace_unsub: Any | None = None
    last_decision: str | None = None
    last_decision_at: datetime | None = None
    last_apply_context_id: str | None = None


def parse_time(value: str) -> time:
    """Parse HH:MM or HH:MM:SS."""
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return time(parts[0], parts[1])
    if len(parts) == 3:
        return time(parts[0], parts[1], parts[2])
    raise ValueError(f"Invalid time: {value}")


def parse_duration(value: Any, default: timedelta | None = None) -> timedelta | None:
    """Parse a duration from seconds, HH:MM:SS, or an existing timedelta."""
    if value is None:
        return default
    if isinstance(value, timedelta):
        return value
    if isinstance(value, int | float):
        return timedelta(seconds=float(value))
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            return timedelta(hours=hours, minutes=minutes, seconds=seconds)
        if len(parts) == 2:
            hours, minutes = (int(part) for part in parts)
            return timedelta(hours=hours, minutes=minutes)
    raise ValueError(f"Invalid duration: {value!r}")


def parse_color(value: Any) -> ColorTarget | None:
    """Parse a supported color config."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Color must be an object")
    mode = ColorMode(value.get("mode"))
    color_value = int(value["value"])
    if color_value < 1000 or color_value > 12000:
        raise ValueError("color_temp_kelvin must be between 1000 and 12000")
    return ColorTarget(mode=mode, value=color_value)


def parse_schedule(value: Any) -> ScheduleConfig:
    """Parse a schedule config."""
    if not isinstance(value, dict):
        raise ValueError("Schedule must be an object")
    schedule_type = ScheduleType(value.get("type"))
    if schedule_type is ScheduleType.FIXED_TIME:
        at = value.get("at")
        if not isinstance(at, str):
            raise ValueError("Fixed schedule requires at: HH:MM or HH:MM:SS")
        try:
            parse_time(at)
        except (TypeError, ValueError) as err:
            raise ValueError(
                f"Invalid fixed schedule time {at!r}: must be HH:MM or HH:MM:SS"
            ) from err
        return ScheduleConfig(type=schedule_type, at=at)
    event = SunEvent(value.get("event"))
    return ScheduleConfig(
        type=schedule_type,
        event=event,
        not_later_than=_optional_limit(value, "not_later_than"),
        not_earlier_than=_optional_limit(value, "not_earlier_than"),
    )


def _optional_limit(value: dict[str, Any], key: str) -> str | None:
    limit = value.get(key)
    if limit is None or limit == "":
        return None
    try:
        parse_time(limit)
    except (AttributeError, TypeError, ValueError) as err:
        raise ValueError(
            f"Invalid {key} time {limit!r}: must be HH:MM or HH:MM:SS"
        ) from err
    return limit


# Home Assistant's entity id rules (helpers valid_entity_id), light domain only.
_LIGHT_ENTITY_ID = re.compile(r"^light\.(?!_)(?!.*__)[\da-z_]+(?<!_)$")


def _setting(
    light: dict[str, Any], global_config: dict[str, Any], key: str, default: Any = None
) -> Any:
    """Return the light's own value for key, else the global one, else default."""
    return light.get(key, global_config.get(key, default))


def _require_brightness(value: Any, name: str) -> int:
    brightness = int(value)
    if brightness < 1 or brightness > 100:
        raise ValueError(f"{name} must be between 1 and 100")
    return brightness


def resolve_native_user_ids(config: dict[str, Any]) -> frozenset[str]:
    """Resolve HA user ids whose light changes count as automations, not manual."""
    global_config = config.get(CONF_GLOBAL, {})
    if not isinstance(global_config, dict):
        raise ValueError("global must be an object")
    raw = global_config.get(CONF_NATIVE_USER_IDS, [])
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError("native_user_ids must be a list of user id strings")
    return frozenset(item.strip() for item in raw if item.strip())


def resolve_light_configs(config: dict[str, Any]) -> list[ResolvedLightConfig]:
    """Resolve global defaults and per-light overrides into immutable configs."""
    # Also validates `global` itself, so config-save rejects bad values.
    resolve_native_user_ids(config)
    global_config = config.get(CONF_GLOBAL, {})
    light_configs = config.get(CONF_LIGHTS, [])
    if not isinstance(light_configs, list) or not light_configs:
        return []

    resolved: list[ResolvedLightConfig] = []
    seen: set[str] = set()
    for light in light_configs:
        if not isinstance(light, dict):
            raise ValueError("Each light must be an object")
        entity_id = light.get(CONF_ENTITY_ID)
        if not isinstance(entity_id, str) or not _LIGHT_ENTITY_ID.match(entity_id):
            raise ValueError(
                f"{entity_id!r} is not a light entity id (expected light.<name>)"
            )
        if entity_id in seen:
            raise ValueError(f"Duplicate DimSome light: {entity_id}")
        seen.add(entity_id)

        min_brightness = _require_brightness(
            light[CONF_MIN_BRIGHTNESS_PCT], CONF_MIN_BRIGHTNESS_PCT
        )
        max_brightness = _require_brightness(
            light[CONF_MAX_BRIGHTNESS_PCT], CONF_MAX_BRIGHTNESS_PCT
        )
        if min_brightness > max_brightness:
            raise ValueError("min_brightness_pct must be <= max_brightness_pct")

        ramp_duration = parse_duration(
            _setting(light, global_config, CONF_RAMP_DURATION), timedelta(minutes=60)
        )
        assert ramp_duration is not None
        if ramp_duration <= timedelta(0):
            raise ValueError("ramp_duration must be positive")
        settle_delay = parse_duration(
            light.get(CONF_SETTLE_DELAY),
            timedelta(milliseconds=500),
        )
        assert settle_delay is not None
        if settle_delay < timedelta(0):
            raise ValueError("settle_delay must not be negative")

        grace_period = parse_duration(
            _setting(light, global_config, CONF_OVERRIDE_GRACE_PERIOD)
        )
        if grace_period is not None and grace_period <= timedelta(0):
            raise ValueError("override_grace_period must be positive")
        resume_mode = OverrideResumeMode(
            _setting(
                light,
                global_config,
                CONF_OVERRIDE_RESUME_MODE,
                OverrideResumeMode.MANUAL_ONLY.value,
            )
        )
        if resume_mode is OverrideResumeMode.AFTER_GRACE_PERIOD and grace_period is None:
            raise ValueError("after_grace_period requires override_grace_period")

        resolved.append(
            ResolvedLightConfig(
                entity_id=entity_id,
                enabled=bool(light.get(CONF_ENABLED, True)),
                min_brightness_pct=min_brightness,
                max_brightness_pct=max_brightness,
                min_color=parse_color(light.get(CONF_MIN_COLOR)),
                max_color=parse_color(light.get(CONF_MAX_COLOR)),
                dim_schedule=parse_schedule(
                    _setting(
                        light,
                        global_config,
                        CONF_DIM_SCHEDULE,
                        {"type": ScheduleType.CIVIL_SUN, "event": SunEvent.CIVIL_DUSK},
                    )
                ),
                brighten_schedule=parse_schedule(
                    _setting(
                        light,
                        global_config,
                        CONF_BRIGHTEN_SCHEDULE,
                        {"type": ScheduleType.CIVIL_SUN, "event": SunEvent.CIVIL_DAWN},
                    )
                ),
                ramp_duration=ramp_duration,
                override_resume_mode=resume_mode,
                override_grace_period=grace_period,
                split_turn_on_calls=bool(
                    _setting(light, global_config, CONF_SPLIT_TURN_ON_CALLS, False)
                ),
                apply_on_recovered_on=bool(
                    _setting(light, global_config, CONF_APPLY_ON_RECOVERED_ON, True)
                ),
                settle_delay=settle_delay,
            )
        )
    return resolved
