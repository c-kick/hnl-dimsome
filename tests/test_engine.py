"""Tests for Dimsome's pure engine."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.dimsome.engine import (
    active_window,
    brightness_pct_to_ha,
    high_plateau_target,
    is_high_plateau,
    is_low_plateau,
    low_plateau_target,
    next_window_start,
    should_clear_manual_override_for_window,
    should_ignore_state_change,
    should_skip_for_manual_override,
    should_stand_down_for_context,
    split_turn_on_service_data,
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
    parse_schedule,
    resolve_light_configs,
    resolve_native_user_ids,
)

TZ = ZoneInfo("Europe/Amsterdam")


def civil_lookup(dawn: time | None = time(5, 0), dusk: time | None = time(22, 0)):
    """Return a deterministic civil lookup with a fixed clock time per date."""
    clocks = {SunEvent.CIVIL_DAWN: dawn, SunEvent.CIVIL_DUSK: dusk}

    def lookup(event: SunEvent, day: date) -> datetime | None:
        clock = clocks[event]
        if clock is None:
            return None
        return datetime.combine(day, clock, tzinfo=TZ)

    return lookup


def no_civil(event: SunEvent, day: date) -> datetime | None:
    """Civil lookup for fixed-only configs; must never be consulted."""
    raise AssertionError("fixed schedules must not consult the civil lookup")


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


def civil_config(**overrides) -> ResolvedLightConfig:
    """A config that dims at civil dusk and brightens at civil dawn."""
    return ResolvedLightConfig(
        **{
            **fixed_config().__dict__,
            "dim_schedule": ScheduleConfig(
                ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK
            ),
            "brighten_schedule": ScheduleConfig(
                ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DAWN
            ),
            **overrides,
        }
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


def test_resolves_turn_on_settle_delay_default_and_per_light_override() -> None:
    """Settle delay defaults to 500 ms unless overridden per light."""
    configs = resolve_light_configs(
        {
            "global": {
                "dim_schedule": {"type": "fixed_time", "at": "21:00"},
                "brighten_schedule": {"type": "fixed_time", "at": "06:00"},
            },
            "lights": [
                {
                    "entity_id": "light.default",
                    "min_brightness_pct": 10,
                    "max_brightness_pct": 80,
                },
                {
                    "entity_id": "light.override",
                    "min_brightness_pct": 10,
                    "max_brightness_pct": 80,
                    "settle_delay": 0.25,
                },
            ],
        }
    )

    assert configs[0].settle_delay == timedelta(milliseconds=500)
    assert configs[1].settle_delay == timedelta(milliseconds=250)


def test_turn_on_settle_delay_defaults_to_500ms() -> None:
    """New configs default to a short settle delay for device on transitions."""
    configs = resolve_light_configs(
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
            ],
        }
    )

    assert configs[0].settle_delay == timedelta(milliseconds=500)


def test_settle_delay_accepts_fractional_duration_strings() -> None:
    """Sub-second settle delays should also work in duration string config."""
    configs = resolve_light_configs(
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
                    "settle_delay": "00:00:00.5",
                },
            ],
        }
    )

    assert configs[0].settle_delay == timedelta(milliseconds=500)


def test_global_settle_delay_is_ignored() -> None:
    """Settle delay is light-specific; global config cannot override the default."""
    configs = resolve_light_configs(
        {
            "global": {
                "dim_schedule": {"type": "fixed_time", "at": "21:00"},
                "brighten_schedule": {"type": "fixed_time", "at": "06:00"},
                "settle_delay": 3,
            },
            "lights": [
                {
                    "entity_id": "light.test",
                    "min_brightness_pct": 10,
                    "max_brightness_pct": 80,
                },
            ],
        }
    )

    assert configs[0].settle_delay == timedelta(milliseconds=500)


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
    window = active_window(fixed_config(), now, no_civil)

    assert window is not None
    assert window.sequence is SequenceKind.DIM
    assert target_for_window(fixed_config(), window, now).brightness_pct == 45


def test_fixed_schedule_finds_next_ramp_start() -> None:
    """The runtime can schedule a wake-up before the next fixed ramp."""
    now = datetime(2026, 5, 4, 21, 55, tzinfo=TZ)

    assert next_window_start(fixed_config(), now, no_civil) == datetime(
        2026, 5, 4, 22, 0, tzinfo=TZ
    )


def test_low_plateau_across_midnight() -> None:
    """After dimming and before brightening works across midnight."""
    now = datetime(2026, 5, 5, 1, 0, tzinfo=TZ)

    assert is_low_plateau(fixed_config(), now, no_civil) is True
    assert target_for_now(fixed_config(), now, no_civil).brightness_pct == 10


def test_high_plateau_after_brightening_uses_max_brightness_and_color() -> None:
    """After brightening and before dimming, recovered lights use max state."""
    now = datetime(2026, 5, 5, 12, 0, tzinfo=TZ)

    assert target_for_now(fixed_config(), now, no_civil) == high_plateau_target(
        fixed_config()
    )


def test_civil_dusk_dim_ramps_down_instead_of_snapping_to_minimum() -> None:
    """Regression: the dim ramp must start at max and descend, never snap to min.

    The previous elevation-sampling engine could mislocate the dusk window, so
    at civil dusk Dimsome found no active ramp and the civil-night plateau
    branch slammed the lights straight to minimum.  With deterministic civil
    times the dusk window is always active at dusk and the ramp descends.
    """
    config = civil_config()
    lookup = civil_lookup(dusk=time(22, 0))

    start = datetime(2026, 5, 31, 22, 0, tzinfo=TZ)
    midpoint = datetime(2026, 5, 31, 22, 30, tzinfo=TZ)
    end = datetime(2026, 5, 31, 23, 0, tzinfo=TZ)
    after = datetime(2026, 5, 31, 23, 30, tzinfo=TZ)

    assert active_window(config, start, lookup).sequence is SequenceKind.DIM
    assert target_for_now(config, start, lookup).brightness_pct == 80
    assert target_for_now(config, midpoint, lookup).brightness_pct == 45
    assert target_for_now(config, end, lookup).brightness_pct == 10
    assert target_for_now(config, after, lookup) == low_plateau_target(config)


def test_civil_dawn_brighten_ramps_up_from_minimum() -> None:
    """The dawn ramp starts at minimum and rises to maximum."""
    config = civil_config()
    lookup = civil_lookup(dawn=time(5, 0))

    assert target_for_now(
        config, datetime(2026, 5, 31, 5, 0, tzinfo=TZ), lookup
    ).brightness_pct == 10
    assert target_for_now(
        config, datetime(2026, 5, 31, 5, 30, tzinfo=TZ), lookup
    ).brightness_pct == 45
    assert target_for_now(
        config, datetime(2026, 5, 31, 6, 0, tzinfo=TZ), lookup
    ).brightness_pct == 80


def test_civil_day_is_high_plateau_after_dawn_ramp() -> None:
    """Between the dawn ramp and the dusk ramp Dimsome holds the day target."""
    config = civil_config()
    lookup = civil_lookup()
    now = datetime(2026, 5, 31, 12, 0, tzinfo=TZ)

    assert is_high_plateau(config, now, lookup) is True
    assert target_for_now(config, now, lookup) == high_plateau_target(config)


def test_civil_night_is_low_plateau_across_midnight() -> None:
    """After the dusk ramp, including past midnight, Dimsome holds the night target."""
    config = civil_config()
    lookup = civil_lookup()

    assert target_for_now(
        config, datetime(2026, 6, 1, 2, 0, tzinfo=TZ), lookup
    ) == low_plateau_target(config)


def test_civil_dusk_window_start_drives_next_wake() -> None:
    """The next ramp start is the upcoming civil dusk."""
    config = civil_config()
    lookup = civil_lookup(dusk=time(22, 0))
    now = datetime(2026, 5, 31, 21, 0, tzinfo=TZ)

    assert next_window_start(config, now, lookup) == datetime(
        2026, 5, 31, 22, 0, tzinfo=TZ
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
    lookup = civil_lookup(dusk=time(22, 0))

    assert target_for_now(config, datetime(2026, 5, 5, 6, 0, tzinfo=TZ), lookup) == (
        low_plateau_target(config)
    )
    assert target_for_now(config, datetime(2026, 5, 5, 7, 0, tzinfo=TZ), lookup) == (
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
    lookup = civil_lookup(dawn=time(5, 0))

    assert target_for_now(config, datetime(2026, 5, 5, 20, 30, tzinfo=TZ), lookup) == (
        LightTarget(45, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 3100))
    )
    assert target_for_now(config, datetime(2026, 5, 5, 21, 30, tzinfo=TZ), lookup) == (
        low_plateau_target(config)
    )


def test_missing_civil_time_yields_no_target() -> None:
    """If astral cannot resolve civil dawn/dusk, Dimsome stays hands-off."""
    config = civil_config()
    lookup = civil_lookup(dawn=None, dusk=None)
    now = datetime(2026, 5, 31, 22, 30, tzinfo=TZ)

    assert active_window(config, now, lookup) is None
    assert target_for_now(config, now, lookup) is None


def test_target_matching_uses_tolerance() -> None:
    """Expected reports tolerate HA/device quantization."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)
    window = active_window(fixed_config(), now, no_civil)
    assert window is not None
    target = target_for_window(fixed_config(), window, now)

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


def test_incomplete_update_during_ignore_window_is_not_manual_override() -> None:
    """Some lights report on/color state before brightness settles."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)

    assert should_ignore_state_change(
        in_flight=False,
        now=now,
        ignore_updates_until=now + timedelta(seconds=10),
        expected_target=LightTarget(59, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2372)),
        attrs={"color_temp_kelvin": 2372},
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
    window = active_window(fixed_config(), datetime(2026, 5, 4, 22, 30, tzinfo=TZ), no_civil)

    assert window is not None
    assert should_skip_for_manual_override(stood_down=True, window=window) is True
    assert should_skip_for_manual_override(stood_down=True, window=None) is False
    assert should_skip_for_manual_override(stood_down=False, window=window) is False


def test_manual_override_clears_for_new_ramp_window() -> None:
    """A stand-down from one ramp should not suppress the next ramp."""
    dim_window = active_window(
        fixed_config(), datetime(2026, 5, 4, 22, 30, tzinfo=TZ), no_civil
    )
    brighten_window = active_window(
        fixed_config(), datetime(2026, 5, 5, 6, 30, tzinfo=TZ), no_civil
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


def test_native_user_context_is_not_manual_override() -> None:
    """Allowlisted user ids (e.g. Node-RED) behave like automations."""
    context = SimpleNamespace(id="change", parent_id=None, user_id="nodered")

    assert (
        should_stand_down_for_context(context, {"automation"}, frozenset({"nodered"}))
        is False
    )
    assert (
        should_stand_down_for_context(context, {"automation"}, frozenset({"other"}))
        is True
    )


def test_parse_schedule_rejects_out_of_range_fixed_time() -> None:
    """Out-of-range fixed times must fail validation, not brick the tick loop."""
    with pytest.raises(ValueError, match="25:99"):
        parse_schedule({"type": "fixed_time", "at": "25:99"})


def test_parse_schedule_rejects_non_numeric_fixed_time() -> None:
    """Non-numeric fixed times must fail validation."""
    with pytest.raises(ValueError, match="ab:cd"):
        parse_schedule({"type": "fixed_time", "at": "ab:cd"})


def test_rejects_nonpositive_ramp_duration() -> None:
    """Zero or negative ramp durations must fail validation."""
    def config_with_ramp(ramp: object) -> dict:
        return {
            "global": {
                "dim_schedule": {"type": "fixed_time", "at": "21:00"},
                "brighten_schedule": {"type": "fixed_time", "at": "06:00"},
                "ramp_duration": ramp,
            },
            "lights": [
                {
                    "entity_id": "light.test",
                    "min_brightness_pct": 10,
                    "max_brightness_pct": 80,
                }
            ],
        }

    with pytest.raises(ValueError, match="ramp_duration"):
        resolve_light_configs(config_with_ramp(0))
    with pytest.raises(ValueError, match="ramp_duration"):
        resolve_light_configs(config_with_ramp(-3600))


def test_overlapping_windows_prefer_latest_started_ramp() -> None:
    """When ramps overlap, the most recently started window wins."""
    config = ResolvedLightConfig(
        **{
            **fixed_config().__dict__,
            "dim_schedule": ScheduleConfig(ScheduleType.FIXED_TIME, at="17:00"),
            "brighten_schedule": ScheduleConfig(ScheduleType.FIXED_TIME, at="04:00"),
            "ramp_duration": timedelta(hours=12),
        }
    )
    # Yesterday's dim window (17:00 + 12h) is still open at 04:30, but the
    # brighten ramp that started at 04:00 must take precedence.
    now = datetime(2026, 6, 10, 4, 30, tzinfo=TZ)

    window = active_window(config, now, no_civil)

    assert window is not None
    assert window.sequence is SequenceKind.BRIGHTEN
    assert window.start == datetime(2026, 6, 10, 4, 0, tzinfo=TZ)


def test_late_state_report_matching_expected_target_is_ignored() -> None:
    """Slow devices reporting Dimsome's own value must not stand themselves down."""
    now = datetime(2026, 6, 10, 22, 30, tzinfo=TZ)

    assert should_ignore_state_change(
        in_flight=False,
        now=now,
        ignore_updates_until=now - timedelta(minutes=1),
        expected_target=LightTarget(50, None),
        attrs={"brightness": 128},
    ) is True
    # A genuinely different external value is still treated as manual.
    assert should_ignore_state_change(
        in_flight=False,
        now=now,
        ignore_updates_until=now - timedelta(minutes=1),
        expected_target=LightTarget(50, None),
        attrs={"brightness": 255},
    ) is False


def test_resolve_native_user_ids_accepts_list_and_csv() -> None:
    """The allowlist accepts a list of ids or a comma-separated string."""
    assert resolve_native_user_ids(
        {"global": {"native_user_ids": ["abc", " def "]}}
    ) == frozenset({"abc", "def"})
    assert resolve_native_user_ids(
        {"global": {"native_user_ids": "abc, def"}}
    ) == frozenset({"abc", "def"})
    assert resolve_native_user_ids({"global": {}}) == frozenset()


def test_rejects_invalid_native_user_ids() -> None:
    """Non-string allowlist entries must fail validation."""
    with pytest.raises(ValueError, match="native_user_ids"):
        resolve_native_user_ids({"global": {"native_user_ids": [123]}})
    with pytest.raises(ValueError, match="native_user_ids"):
        resolve_light_configs(
            {
                "global": {
                    "dim_schedule": {"type": "fixed_time", "at": "21:00"},
                    "brighten_schedule": {"type": "fixed_time", "at": "06:00"},
                    "native_user_ids": 42,
                },
                "lights": [
                    {
                        "entity_id": "light.test",
                        "min_brightness_pct": 10,
                        "max_brightness_pct": 80,
                    }
                ],
            }
        )
