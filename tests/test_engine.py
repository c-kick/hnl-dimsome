"""Tests for Dimsome's pure engine."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.dimsome.engine import (
    active_window,
    brightness_pct_to_ha,
    civil_event_time,
    high_plateau_target,
    is_low_plateau,
    next_window_start,
    reconstructed_civil_samples,
    should_clear_manual_override_for_window,
    should_ignore_state_change,
    should_skip_for_manual_override,
    should_stand_down_for_context,
    split_turn_on_service_data,
    SunElevationSample,
    target_for_now,
    target_for_window,
    target_matches_state,
)
from custom_components.dimsome.models import (
    ColorMode,
    ColorTarget,
    LightTarget,
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
        enabled=True,
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
        apply_on_recovered_on=True,
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


def test_light_enabled_defaults_to_true_and_can_be_disabled() -> None:
    """Per-light enabled state defaults on and can persist off."""
    configs = resolve_light_configs(
        {
            "global": {
                "dim_schedule": {"type": "fixed_time", "at": "21:00"},
                "brighten_schedule": {"type": "fixed_time", "at": "06:00"},
            },
            "lights": [
                {
                    "entity_id": "light.enabled",
                    "min_brightness_pct": 10,
                    "max_brightness_pct": 80,
                },
                {
                    "entity_id": "light.disabled",
                    "enabled": False,
                    "min_brightness_pct": 10,
                    "max_brightness_pct": 80,
                },
            ],
        }
    )

    assert configs[0].enabled is True
    assert configs[0].apply_on_recovered_on is True
    assert configs[1].enabled is False


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


def test_high_plateau_after_brightening_uses_max_brightness_and_color() -> None:
    """After brightening and before dimming, recovered lights use max state."""
    now = datetime(2026, 5, 5, 12, 0, tzinfo=TZ)

    assert target_for_now(fixed_config(), now, []) == high_plateau_target(
        fixed_config()
    )



def test_civil_event_uses_elevation_crossing() -> None:
    """Civil dusk is estimated from elevation crossing -6 degrees."""
    samples = [
        SunElevationSample(datetime(2026, 5, 4, 21, 0, tzinfo=TZ), -5.0),
        SunElevationSample(datetime(2026, 5, 4, 21, 20, tzinfo=TZ), -7.0),
    ]

    assert civil_event_time(samples, SunEvent.CIVIL_DUSK) == datetime(
        2026, 5, 4, 21, 10, tzinfo=TZ
    )


def test_reconstructs_previous_civil_dusk_when_started_after_dusk() -> None:
    """Startup after civil dusk can still enforce the active dim/plateau target."""
    next_dusk = datetime(2026, 5, 5, 21, 51, tzinfo=TZ)

    assert reconstructed_civil_samples(
        elevation=-11.61,
        next_dawn=None,
        next_dusk=next_dusk.isoformat(),
    ) == [
        SunElevationSample(next_dusk - timedelta(days=1, seconds=1), -5.0),
        SunElevationSample(next_dusk - timedelta(days=1), -6.0),
    ]


def test_reconstructs_previous_civil_dawn_when_started_after_dawn() -> None:
    """Startup after civil dawn can reconstruct an active brighten ramp."""
    next_dawn = datetime(2026, 5, 5, 5, 21, tzinfo=TZ)

    assert reconstructed_civil_samples(
        elevation=2.0,
        next_dawn=next_dawn.isoformat(),
        next_dusk=None,
    ) == [
        SunElevationSample(next_dawn - timedelta(days=1, seconds=1), -7.0),
        SunElevationSample(next_dawn - timedelta(days=1), -6.0),
    ]


def test_reconstructed_civil_dusk_produces_low_plateau_target() -> None:
    """Reconstructed startup samples feed the existing target calculation."""
    next_dusk = datetime(2026, 5, 5, 21, 51, tzinfo=TZ)
    now = datetime(2026, 5, 4, 23, 0, tzinfo=TZ)

    target = target_for_now(
        ResolvedLightConfig(
            **{
                **fixed_config().__dict__,
                "dim_schedule": ScheduleConfig(
                    ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK
                ),
            }
        ),
        now,
        reconstructed_civil_samples(
            elevation=-11.61,
            next_dawn=None,
            next_dusk=next_dusk.isoformat(),
        ),
    )

    assert target is not None
    assert target.brightness_pct == 10


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


def test_manual_override_only_skips_active_ramp() -> None:
    """A manual override should not suppress the post-dim low plateau."""
    window = active_window(fixed_config(), datetime(2026, 5, 4, 22, 30, tzinfo=TZ), [])

    assert window is not None
    assert should_skip_for_manual_override(stood_down=True, window=window) is True
    assert should_skip_for_manual_override(stood_down=True, window=None) is False
    assert should_skip_for_manual_override(stood_down=False, window=window) is False


def test_manual_override_clears_for_new_ramp_window() -> None:
    """A stand-down from one ramp should not suppress the next ramp."""
    dim_window = active_window(
        fixed_config(), datetime(2026, 5, 4, 22, 30, tzinfo=TZ), []
    )
    brighten_window = active_window(
        fixed_config(), datetime(2026, 5, 5, 6, 30, tzinfo=TZ), []
    )

    assert dim_window is not None
    assert brighten_window is not None
    assert should_clear_manual_override_for_window(
        stood_down=True,
        stood_down_window=dim_window,
        window=brighten_window,
    )
    assert not should_clear_manual_override_for_window(
        stood_down=True,
        stood_down_window=dim_window,
        window=dim_window,
    )


def test_split_turn_on_service_data_sends_brightness_last() -> None:
    """Split commands should leave brightness as the final IKEA/TRADFRI update."""
    target = LightTarget(30, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2300))

    assert split_turn_on_service_data("light.test", target) == [
        {"entity_id": "light.test", "color_temp_kelvin": 2300},
        {"entity_id": "light.test", "brightness": 76},
    ]


def test_user_context_is_manual_override() -> None:
    """Frontend/API changes with a user id should stand down Dimsome."""
    context = SimpleNamespace(id="change", parent_id=None, user_id="user")

    assert should_stand_down_for_context(context, {"automation"}) is True


def test_recent_automation_context_is_not_manual_override() -> None:
    """Automation-originated changes should not interrupt an active Dimsome ramp."""
    context = SimpleNamespace(id="automation", parent_id=None, user_id=None)

    assert should_stand_down_for_context(context, {"automation"}) is False


def test_recent_parent_automation_context_is_not_manual_override() -> None:
    """Child changes from an automation/script context should keep Dimsome active."""
    context = SimpleNamespace(id="change", parent_id="automation", user_id=None)

    assert should_stand_down_for_context(context, {"automation"}) is False


def test_unknown_device_context_is_manual_override() -> None:
    """Physical/device-like changes remain manual overrides by default."""
    context = SimpleNamespace(id="device", parent_id=None, user_id=None)

    assert should_stand_down_for_context(context, {"automation"}) is True
