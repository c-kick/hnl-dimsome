"""Tests for Dimsome's pure engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.dimsome.engine import (
    active_window,
    brightness_pct_to_ha,
    civil_event_time,
    is_low_plateau,
    next_window_start,
    should_ignore_state_change,
    SunElevationSample,
    target_for_now,
    target_for_window,
    target_matches_state,
)
from custom_components.dimsome.models import (
    ColorMode,
    ColorTarget,
    ResolvedLightConfig,
    ScheduleConfig,
    ScheduleType,
    SequenceKind,
    SunEvent,
    OverrideResumeMode,
    resolve_light_configs,
)

TZ = ZoneInfo("Europe/Amsterdam")


def fixed_config() -> ResolvedLightConfig:
    """Return a simple fixed schedule config."""
    return ResolvedLightConfig(
        entity_id="light.test",
        min_brightness_pct=10,
        max_brightness_pct=80,
        min_color=ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2200),
        max_color=ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 4000),
        dim_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="22:00"),
        brighten_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="06:00"),
        ramp_duration=timedelta(hours=1),
        override_resume_mode=OverrideResumeMode.MANUAL_ONLY,
        override_grace_period=None,
        split_turn_on_calls=False,
    )


def test_resolves_global_defaults_and_per_light_overrides() -> None:
    """Per-light settings override global defaults."""
    configs = resolve_light_configs(
        {
            "global": {
                "dim_schedule": {"type": "fixed_time", "at": "21:00"},
                "brighten_schedule": {"type": "civil_sun", "event": "civil_dawn"},
                "ramp_duration": "01:00:00",
                "override_resume_mode": "manual_only",
            },
            "lights": [
                {
                    "entity_id": "light.test",
                    "min_brightness_pct": 15,
                    "max_brightness_pct": 75,
                    "ramp_duration": "00:30:00",
                }
            ],
        }
    )

    assert configs[0].ramp_duration == timedelta(minutes=30)
    assert configs[0].dim_schedule.at == "21:00"
    assert configs[0].brighten_schedule.event is SunEvent.CIVIL_DAWN


def test_rejects_duplicate_lights() -> None:
    """One config cannot control the same light twice."""
    with pytest.raises(ValueError, match="Duplicate"):
        resolve_light_configs(
            {
                "global": {
                    "dim_schedule": {"type": "fixed_time", "at": "21:00"},
                    "brighten_schedule": {"type": "fixed_time", "at": "06:00"},
                },
                "lights": [
                    {
                        "entity_id": "light.test",
                        "min_brightness_pct": 10,
                        "max_brightness_pct": 80,
                    },
                    {
                        "entity_id": "light.test",
                        "min_brightness_pct": 10,
                        "max_brightness_pct": 80,
                    },
                ],
            }
        )


def test_fixed_schedule_reconstructs_mid_ramp() -> None:
    """The active fixed ramp is reconstructed from wall-clock time."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)
    window = active_window(fixed_config(), now, [])

    assert window is not None
    assert window.sequence is SequenceKind.DIM
    assert target_for_window(fixed_config(), window, now).brightness_pct == 45


def test_fixed_schedule_finds_next_ramp_start() -> None:
    """The runtime can schedule a wake-up before the next fixed ramp."""
    now = datetime(2026, 5, 4, 21, 55, tzinfo=TZ)

    assert next_window_start(fixed_config(), now, []) == datetime(
        2026, 5, 4, 22, 0, tzinfo=TZ
    )


def test_low_plateau_across_midnight() -> None:
    """After dimming and before brightening works across midnight."""
    now = datetime(2026, 5, 5, 1, 0, tzinfo=TZ)

    assert is_low_plateau(fixed_config(), now, []) is True
    assert target_for_now(fixed_config(), now, []).brightness_pct == 10



def test_civil_event_uses_elevation_crossing() -> None:
    """Civil dusk is estimated from elevation crossing -6 degrees."""
    samples = [
        SunElevationSample(datetime(2026, 5, 4, 21, 0, tzinfo=TZ), -5.0),
        SunElevationSample(datetime(2026, 5, 4, 21, 20, tzinfo=TZ), -7.0),
    ]

    assert civil_event_time(samples, SunEvent.CIVIL_DUSK) == datetime(
        2026, 5, 4, 21, 10, tzinfo=TZ
    )


def test_target_matching_uses_tolerance() -> None:
    """Expected reports tolerate HA/device quantization."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)
    window = active_window(fixed_config(), now, [])
    assert window is not None
    target = target_for_window(
        fixed_config(), window, now
    )

    assert target_matches_state(
        target,
        {
            "brightness": brightness_pct_to_ha(46),
            "color_temp_kelvin": 3105,
        },
    )


def test_in_flight_update_is_not_manual_override() -> None:
    """State reports during our service call are ignored even if partial."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)

    assert should_ignore_state_change(
        in_flight=True,
        now=now,
        ignore_updates_until=None,
        expected_target=None,
        attrs={"brightness": brightness_pct_to_ha(99)},
    )


def test_non_matching_update_after_ignore_window_is_external() -> None:
    """After the guard expires, changed attrs are treated as external."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)

    assert not should_ignore_state_change(
        in_flight=False,
        now=now,
        ignore_updates_until=now - timedelta(seconds=1),
        expected_target=None,
        attrs={"brightness": brightness_pct_to_ha(99)},
    )
