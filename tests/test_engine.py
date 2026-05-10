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
    low_plateau_target,
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
    upcoming_civil_samples,
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


def test_fixed_brighten_time_overrides_civil_dawn() -> None:
    """A configured brighten time completely ignores civil dawn."""
    config = ResolvedLightConfig(
        **{
            **fixed_config().__dict__,
            "dim_schedule": ScheduleConfig(
                ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK
            ),
            "brighten_schedule": ScheduleConfig(
                ScheduleType.FIXED_TIME, at="06:30:00"
            ),
        }
    )
    samples = [
        SunElevationSample(datetime(2026, 5, 4, 21, 0, tzinfo=TZ), -5.0),
        SunElevationSample(datetime(2026, 5, 4, 21, 10, tzinfo=TZ), -7.0),
        SunElevationSample(datetime(2026, 5, 5, 5, 0, tzinfo=TZ), -7.0),
        SunElevationSample(datetime(2026, 5, 5, 5, 10, tzinfo=TZ), -5.0),
        SunElevationSample(datetime(2026, 5, 5, 21, 0, tzinfo=TZ), -5.0),
        SunElevationSample(datetime(2026, 5, 5, 21, 10, tzinfo=TZ), -7.0),
    ]

    assert target_for_now(config, datetime(2026, 5, 5, 6, 0, tzinfo=TZ), samples) == (
        low_plateau_target(config)
    )
    assert target_for_now(config, datetime(2026, 5, 5, 7, 0, tzinfo=TZ), samples) == (
        LightTarget(45, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 3100))
    )


def test_fixed_dim_time_overrides_civil_dusk() -> None:
    """A configured dim time completely ignores civil dusk."""
    config = ResolvedLightConfig(
        **{
            **fixed_config().__dict__,
            "dim_schedule": ScheduleConfig(ScheduleType.FIXED_TIME, at="20:00:00"),
            "brighten_schedule": ScheduleConfig(
                ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DAWN
            ),
        }
    )
    samples = [
        SunElevationSample(datetime(2026, 5, 5, 5, 0, tzinfo=TZ), -7.0),
        SunElevationSample(datetime(2026, 5, 5, 5, 10, tzinfo=TZ), -5.0),
        SunElevationSample(datetime(2026, 5, 5, 22, 0, tzinfo=TZ), -5.0),
        SunElevationSample(datetime(2026, 5, 5, 22, 10, tzinfo=TZ), -7.0),
    ]

    assert target_for_now(config, datetime(2026, 5, 5, 20, 30, tzinfo=TZ), samples) == (
        LightTarget(45, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 3100))
    )
    assert target_for_now(config, datetime(2026, 5, 5, 21, 30, tzinfo=TZ), samples) == (
        low_plateau_target(config)
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


def test_reconstructed_civil_dusk_matches_live_after_midnight_case() -> None:
    """After midnight, next_dusk still identifies the previous evening plateau."""
    now = datetime(2026, 5, 10, 1, 54, 20, tzinfo=TZ)
    config = ResolvedLightConfig(
        **{
            **fixed_config().__dict__,
            "min_brightness_pct": 30,
            "max_brightness_pct": 80,
            "min_color": ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2300),
            "max_color": ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2450),
            "dim_schedule": ScheduleConfig(
                ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK
            ),
            "brighten_schedule": ScheduleConfig(
                ScheduleType.FIXED_TIME, at="06:30:00"
            ),
        }
    )

    target = target_for_now(
        config,
        now,
        reconstructed_civil_samples(
            elevation=-20.25,
            next_dawn="2026-05-10T03:11:41.391141+00:00",
            next_dusk="2026-05-10T20:01:11.496936+00:00",
        ),
    )

    assert target == LightTarget(
        brightness_pct=30,
        color=ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2300),
    )


def test_live_schedule_transitions_through_dawn_and_next_dusk() -> None:
    """Live civil-dusk/fixed-dawn schedule stays coherent through the next cycle."""
    config = ResolvedLightConfig(
        **{
            **fixed_config().__dict__,
            "min_brightness_pct": 30,
            "max_brightness_pct": 80,
            "min_color": ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2300),
            "max_color": ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2450),
            "dim_schedule": ScheduleConfig(
                ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK
            ),
            "brighten_schedule": ScheduleConfig(
                ScheduleType.FIXED_TIME, at="06:30:00"
            ),
        }
    )
    samples = [
        *reconstructed_civil_samples(
            elevation=-20.25,
            next_dawn="2026-05-10T03:11:41.391141+00:00",
            next_dusk="2026-05-10T20:01:11.496936+00:00",
        ),
        *upcoming_civil_samples(
            next_dawn="2026-05-10T03:11:41.391141+00:00",
            next_dusk="2026-05-10T20:01:11.496936+00:00",
        ),
    ]

    assert target_for_now(
        config, datetime(2026, 5, 10, 6, 15, tzinfo=TZ), samples
    ) == LightTarget(30, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2300))
    assert target_for_now(
        config, datetime(2026, 5, 10, 7, 0, tzinfo=TZ), samples
    ) == LightTarget(55, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2375))
    assert target_for_now(
        config, datetime(2026, 5, 10, 12, 0, tzinfo=TZ), samples
    ) == LightTarget(80, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2450))
    assert next_window_start(
        config, datetime(2026, 5, 10, 12, 0, tzinfo=TZ), samples
    ) == datetime(2026, 5, 10, 20, 1, 11, 496936, tzinfo=ZoneInfo("UTC"))
    assert target_for_now(
        config, datetime(2026, 5, 10, 22, 31, 11, 496936, tzinfo=TZ), samples
    ) == LightTarget(55, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2375))
    assert target_for_now(
        config, datetime(2026, 5, 10, 23, 30, tzinfo=TZ), samples
    ) == LightTarget(30, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2300))


def test_civil_schedule_handles_out_of_order_reconstructed_samples() -> None:
    """Synthetic dusk samples may be appended after later sun updates."""
    next_dusk = datetime(2026, 5, 8, 19, 57, tzinfo=ZoneInfo("UTC"))
    now = datetime(2026, 5, 7, 23, 19, tzinfo=TZ)
    samples = [
        SunElevationSample(datetime(2026, 5, 7, 21, 13, tzinfo=ZoneInfo("UTC")), -14.32),
        *reconstructed_civil_samples(
            elevation=-14.32,
            next_dawn="2026-05-08T03:15:36+00:00",
            next_dusk=next_dusk.isoformat(),
        ),
    ]

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
        samples,
    )

    assert target is not None
    assert target.brightness_pct == 10


def test_upcoming_civil_dusk_produces_next_ramp_start() -> None:
    """The runtime can schedule civil dusk before the elevation crossing happens."""
    next_dusk = datetime(2026, 5, 9, 20, 1, tzinfo=ZoneInfo("UTC"))
    now = datetime(2026, 5, 9, 21, 55, tzinfo=TZ)
    config = ResolvedLightConfig(
        **{
            **fixed_config().__dict__,
            "dim_schedule": ScheduleConfig(
                ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK
            ),
        }
    )

    assert next_window_start(
        config,
        now,
        upcoming_civil_samples(next_dawn=None, next_dusk=next_dusk.isoformat()),
    ) == next_dusk


def test_upcoming_civil_dusk_produces_active_ramp_target() -> None:
    """A one-shot wake at civil dusk can start ramping without a sun event."""
    next_dusk = datetime(2026, 5, 9, 20, 1, tzinfo=ZoneInfo("UTC"))
    now = datetime(2026, 5, 9, 22, 31, tzinfo=TZ)
    config = ResolvedLightConfig(
        **{
            **fixed_config().__dict__,
            "dim_schedule": ScheduleConfig(
                ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK
            ),
        }
    )

    target = target_for_now(
        config,
        now,
        upcoming_civil_samples(next_dawn=None, next_dusk=next_dusk.isoformat()),
    )

    assert target is not None
    assert target.brightness_pct == 45


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


def test_target_matching_tolerates_missing_kelvin_report() -> None:
    """Some lights accept kelvin commands but only report brightness back."""
    target = LightTarget(45, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 3100))

    assert target_matches_state(
        target,
        {
            "brightness": brightness_pct_to_ha(45),
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
