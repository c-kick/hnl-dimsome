"""Tests for Home Assistant coordinator glue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.dimsome.const import DOMAIN, SERVICE_RESUME
from custom_components.dimsome.models import (
    ColorMode,
    ColorTarget,
    LightRuntime,
    LightTarget,
    OverrideResumeMode,
    ResolvedLightConfig,
    ScheduleConfig,
    ScheduleType,
    SunEvent,
)

pytest.importorskip("voluptuous")

from custom_components.dimsome import coordinator


class FakeServices:
    """Minimal service registry for registration tests."""

    def __init__(self) -> None:
        self.handler: Any = None

    def has_service(self, domain: str, service: str) -> bool:
        return False

    def async_register(self, domain: str, service: str, handler: Any, **_: Any) -> None:
        assert domain == DOMAIN
        assert service == SERVICE_RESUME
        self.handler = handler


class FakeHass:
    """Minimal hass object for service registration tests."""

    def __init__(self) -> None:
        self.services = FakeServices()


def test_resume_service_handler_accepts_single_service_call(monkeypatch) -> None:
    """Home Assistant invokes service handlers with only ServiceCall."""
    hass = FakeHass()
    calls = []

    async def fake_resume_service(call_hass: FakeHass, call: object) -> None:
        calls.append((call_hass, call))

    monkeypatch.setattr(coordinator, "async_resume_service", fake_resume_service)
    coordinator.register_services(hass)
    call = object()

    asyncio.run(hass.services.handler(call))

    assert calls == [(hass, call)]


def test_start_registers_periodic_refresh_for_civil_schedules(monkeypatch) -> None:
    """Civil schedules must refresh sun data even when no ramp timer is active."""
    config = ResolvedLightConfig(
        entity_id="light.test",
        enabled=True,
        min_brightness_pct=30,
        max_brightness_pct=80,
        min_color=None,
        max_color=None,
        dim_schedule=ScheduleConfig(ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK),
        brighten_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="06:30:00"),
        ramp_duration=timedelta(hours=1),
        override_resume_mode=OverrideResumeMode.MANUAL_ONLY,
        override_grace_period=None,
        split_turn_on_calls=False,
        apply_on_recovered_on=True,
    )
    controller = coordinator.DimsomeController(
        SimpleNamespace(
            states=SimpleNamespace(get=lambda _: None),
            bus=SimpleNamespace(async_listen=lambda *_: lambda: None),
        ),
        "entry",
        [config],
    )
    intervals = []

    monkeypatch.setattr(
        coordinator,
        "async_track_state_change_event",
        lambda *_: lambda: None,
    )
    monkeypatch.setattr(
        coordinator,
        "async_track_time_interval",
        lambda *args: intervals.append(args) or (lambda: None),
    )
    monkeypatch.setattr(controller, "async_tick", lambda *_: None)

    asyncio.run(controller.async_start())

    assert intervals == [
        (controller.hass, controller.async_tick, coordinator.SUN_REFRESH_INTERVAL)
    ]
    assert controller._sun_refresh_unsub is not None


def test_user_context_is_manual_override() -> None:
    """Frontend/API changes with a user id should stand down Dimsome."""
    context = SimpleNamespace(id="change", parent_id=None, user_id="user")

    assert coordinator.should_stand_down_for_context(context, {"automation"}) is True


def test_recent_automation_context_is_not_manual_override() -> None:
    """Automation-originated changes should not interrupt an active Dimsome ramp."""
    context = SimpleNamespace(id="automation", parent_id=None, user_id=None)

    assert coordinator.should_stand_down_for_context(context, {"automation"}) is False


def test_recent_parent_automation_context_is_not_manual_override() -> None:
    """Child changes from an automation/script context should keep Dimsome active."""
    context = SimpleNamespace(id="change", parent_id="automation", user_id=None)

    assert coordinator.should_stand_down_for_context(context, {"automation"}) is False


def test_unknown_device_context_is_manual_override() -> None:
    """Physical/device-like changes remain manual overrides by default."""
    context = SimpleNamespace(id="device", parent_id=None, user_id=None)

    assert coordinator.should_stand_down_for_context(context, {"automation"}) is True


def test_resume_clears_cached_targets(monkeypatch) -> None:
    """Resume must force a fresh apply even if the previous command was cached."""
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    runtime = LightRuntime(config=SimpleNamespace(entity_id="light.test"))
    runtime.stood_down = True
    runtime.stood_down_window = SimpleNamespace()
    runtime.last_target = LightTarget(50)
    runtime.pending_target = LightTarget(60)
    runtime.expected_target = LightTarget(70)
    controller.lights = {"light.test": runtime}

    async def fake_tick() -> None:
        return None

    monkeypatch.setattr(controller, "async_tick", fake_tick)

    asyncio.run(controller.async_resume({"light.test"}))

    assert runtime.stood_down is False
    assert runtime.stood_down_window is None
    assert runtime.last_target is None
    assert runtime.pending_target is None
    assert runtime.expected_target is None


def test_turn_on_verification_reapplies_mismatched_target(monkeypatch) -> None:
    """Turn-on verification must force a retry when restore timing wins."""
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    runtime = LightRuntime(config=SimpleNamespace(entity_id="light.test"))
    target = LightTarget(50)
    runtime.last_target = target
    controller._sun_samples = []
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(
                state="on", attributes={"brightness": 254}
            )
        )
    )
    calls = []

    async def fake_sleep(_: float) -> None:
        return None

    async def fake_apply(call_runtime: LightRuntime, call_target: LightTarget) -> None:
        calls.append((call_runtime, call_target, call_runtime.last_target))

    monkeypatch.setattr(coordinator.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator, "target_for_now", lambda *_: target)
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)

    asyncio.run(controller._async_verify_turn_on_target(runtime, target))

    assert calls == [(runtime, target, None)]


def test_turn_on_waits_settle_delay_before_computing_target(monkeypatch) -> None:
    """Turn-on handling should wait for device on transitions before targeting."""
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    runtime = LightRuntime(
        config=SimpleNamespace(entity_id="light.test", settle_delay=timedelta(seconds=2))
    )
    controller._sun_samples = []
    calls = []

    async def fake_sleep(delay: float) -> None:
        calls.append(("sleep", delay))

    async def fake_apply(call_runtime: LightRuntime, call_target: LightTarget) -> None:
        calls.append(("apply", call_runtime, call_target))

    async def fake_verify(call_runtime: LightRuntime, call_target: LightTarget) -> None:
        calls.append(("verify", call_runtime, call_target))

    target = LightTarget(50)

    def fake_target_for_now(*_: Any) -> LightTarget:
        calls.append(("target",))
        return target

    monkeypatch.setattr(coordinator.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator, "target_for_now", fake_target_for_now)
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)
    monkeypatch.setattr(controller, "_async_verify_turn_on_target", fake_verify)

    asyncio.run(controller._async_handle_turn_on(runtime))

    assert calls == [
        ("sleep", 2.0),
        ("target",),
        ("apply", runtime, target),
        ("verify", runtime, target),
    ]


def test_initial_on_state_during_ramp_is_not_manual_override(monkeypatch) -> None:
    """State discovery after HA restart should not stand down an active ramp."""
    runtime = LightRuntime(
        config=SimpleNamespace(
            entity_id="light.test",
            enabled=True,
            apply_on_recovered_on=True,
        )
    )
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.test": runtime}
    controller._sun_samples = []
    controller._automation_context_ids = []
    controller.hass = SimpleNamespace(async_create_task=lambda task: task)
    tasks = []

    monkeypatch.setattr(coordinator, "active_window", lambda *_: object())
    monkeypatch.setattr(
        controller,
        "_async_handle_turn_on",
        lambda call_runtime: tasks.append(call_runtime),
    )

    controller._async_light_changed(
        SimpleNamespace(
            data={
                "entity_id": "light.test",
                "old_state": None,
                "new_state": SimpleNamespace(
                    state="on",
                    attributes={},
                    context=SimpleNamespace(id="restore", parent_id=None, user_id=None),
                ),
            }
        )
    )

    assert runtime.stood_down is False
    assert runtime.stood_down_window is None
    assert runtime.last_target is None
    assert tasks == [runtime]


def test_tick_refreshes_civil_sun_samples_from_current_sun_state(monkeypatch) -> None:
    """A missed sun listener update must not leave civil dusk stuck on high."""
    now = datetime(2026, 5, 7, 23, 19, tzinfo=ZoneInfo("Europe/Amsterdam"))
    config = ResolvedLightConfig(
        entity_id="light.test",
        enabled=True,
        min_brightness_pct=30,
        max_brightness_pct=80,
        min_color=ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2300),
        max_color=ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2450),
        dim_schedule=ScheduleConfig(ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK),
        brighten_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="06:30:00"),
        ramp_duration=timedelta(hours=1),
        override_resume_mode=OverrideResumeMode.MANUAL_ONLY,
        override_grace_period=None,
        split_turn_on_calls=False,
        apply_on_recovered_on=True,
    )
    runtime = LightRuntime(config=config)
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.test": runtime}
    controller._sun_samples = []
    controller._ramp_unsub = None
    controller._wake_unsub = None
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(
                state="below_horizon",
                attributes={
                    "elevation": -14.32,
                    "next_dawn": "2026-05-08T03:15:36+00:00",
                    "next_dusk": "2026-05-08T19:57:27+00:00",
                },
            )
            if entity_id == coordinator.SUN_ENTITY_ID
            else SimpleNamespace(state="on", attributes={})
        )
    )
    calls = []

    async def fake_apply(_: LightRuntime, target: LightTarget) -> None:
        calls.append(target)

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: now)
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)
    monkeypatch.setattr(controller, "_schedule_wake_timer", lambda *_: None)

    asyncio.run(controller.async_tick())

    assert calls == [
        LightTarget(
            brightness_pct=30,
            color=ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2300),
        )
    ]


def test_tick_schedules_wake_from_upcoming_civil_dusk(monkeypatch) -> None:
    """Civil dusk must not depend only on catching a sun.sun crossing event."""
    now = datetime(2026, 5, 9, 21, 55, tzinfo=ZoneInfo("Europe/Amsterdam"))
    next_dusk = datetime(2026, 5, 9, 20, 1, tzinfo=ZoneInfo("UTC"))
    config = ResolvedLightConfig(
        entity_id="light.test",
        enabled=True,
        min_brightness_pct=30,
        max_brightness_pct=80,
        min_color=None,
        max_color=None,
        dim_schedule=ScheduleConfig(ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK),
        brighten_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="06:30:00"),
        ramp_duration=timedelta(hours=1),
        override_resume_mode=OverrideResumeMode.MANUAL_ONLY,
        override_grace_period=None,
        split_turn_on_calls=False,
        apply_on_recovered_on=True,
    )
    runtime = LightRuntime(config=config)
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.test": runtime}
    controller._sun_samples = []
    controller._ramp_unsub = None
    controller._wake_unsub = None
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(
                state="above_horizon",
                attributes={
                    "elevation": -5.4,
                    "next_dawn": "2026-05-10T03:11:00+00:00",
                    "next_dusk": next_dusk.isoformat(),
                },
            )
            if entity_id == coordinator.SUN_ENTITY_ID
            else SimpleNamespace(state="on", attributes={})
        )
    )
    wake_calls = []

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: now)
    monkeypatch.setattr(
        controller, "_schedule_wake_timer", lambda *args: wake_calls.append(args)
    )

    asyncio.run(controller.async_tick())

    assert wake_calls == [(now, next_dusk)]


def test_tick_keeps_cached_dusk_when_home_assistant_rolls_next_dusk(monkeypatch) -> None:
    """The active dusk ramp must use Dimsome's stored dusk after HA rolls next_dusk."""
    before_dusk = datetime(2026, 5, 24, 21, 55, tzinfo=ZoneInfo("Europe/Amsterdam"))
    during_dusk = datetime(2026, 5, 24, 22, 34, tzinfo=ZoneInfo("Europe/Amsterdam"))
    today_dusk = datetime(2026, 5, 24, 20, 0, tzinfo=ZoneInfo("UTC"))
    tomorrow_dusk = datetime(2026, 5, 25, 19, 59, tzinfo=ZoneInfo("UTC"))
    config = ResolvedLightConfig(
        entity_id="light.test",
        enabled=True,
        min_brightness_pct=30,
        max_brightness_pct=80,
        min_color=None,
        max_color=None,
        dim_schedule=ScheduleConfig(ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK),
        brighten_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="06:30:00"),
        ramp_duration=timedelta(hours=1),
        override_resume_mode=OverrideResumeMode.MANUAL_ONLY,
        override_grace_period=None,
        split_turn_on_calls=False,
        apply_on_recovered_on=True,
    )
    runtime = LightRuntime(config=config)
    sun_attrs = {
        "elevation": -5.4,
        "next_dawn": "2026-05-25T03:00:00+00:00",
        "next_dusk": today_dusk.isoformat(),
    }
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.test": runtime}
    controller._sun_samples = []
    controller._civil_event_cache = {}
    controller._ramp_unsub = None
    controller._wake_unsub = None
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(
                state="below_horizon" if entity_id == coordinator.SUN_ENTITY_ID else "on",
                attributes=sun_attrs if entity_id == coordinator.SUN_ENTITY_ID else {},
            )
        )
    )
    calls = []

    async def fake_apply(_: LightRuntime, target: LightTarget) -> None:
        calls.append(target)

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: before_dusk)
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)
    monkeypatch.setattr(controller, "_schedule_wake_timer", lambda *_: None)

    asyncio.run(controller.async_tick())

    sun_attrs.update({"elevation": -6.5, "next_dusk": tomorrow_dusk.isoformat()})
    monkeypatch.setattr(coordinator.dt_util, "now", lambda: during_dusk)

    asyncio.run(controller.async_tick())

    assert calls == [LightTarget(52)]


def test_sun_sample_refresh_preserves_exact_civil_crossing_marker(monkeypatch) -> None:
    """A raw below-threshold refresh must not erase the ramp-start marker."""
    now = datetime(2026, 5, 18, 22, 5, tzinfo=ZoneInfo("Europe/Amsterdam"))
    config = ResolvedLightConfig(
        entity_id="light.test",
        enabled=True,
        min_brightness_pct=30,
        max_brightness_pct=80,
        min_color=None,
        max_color=None,
        dim_schedule=ScheduleConfig(ScheduleType.CIVIL_SUN, event=SunEvent.CIVIL_DUSK),
        brighten_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="06:30:00"),
        ramp_duration=timedelta(hours=1),
        override_resume_mode=OverrideResumeMode.MANUAL_ONLY,
        override_grace_period=None,
        split_turn_on_calls=False,
        apply_on_recovered_on=True,
    )
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller._sun_samples = [
        coordinator.SunElevationSample(now, coordinator.CIVIL_ELEVATION)
    ]

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: now)

    controller._record_sun_sample(
        SimpleNamespace(
            attributes={
                "elevation": -6.01,
                "next_dawn": "2026-05-19T03:00:00+00:00",
                "next_dusk": None,
            }
        )
    )

    assert controller._sun_samples == [
        coordinator.SunElevationSample(now, coordinator.CIVIL_ELEVATION)
    ]
    assert coordinator.target_for_now(config, now, controller._sun_samples) == (
        LightTarget(80)
    )


def test_tick_records_last_decision_for_diagnostics(monkeypatch) -> None:
    """Runtime diagnostics must explain why a light did or did not dim."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
    runtime = LightRuntime(
        config=ResolvedLightConfig(
            entity_id="light.test",
            enabled=True,
            min_brightness_pct=10,
            max_brightness_pct=80,
            min_color=None,
            max_color=None,
            dim_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="22:00"),
            brighten_schedule=ScheduleConfig(ScheduleType.FIXED_TIME, at="06:00"),
            ramp_duration=timedelta(hours=1),
            override_resume_mode=OverrideResumeMode.MANUAL_ONLY,
            override_grace_period=None,
            split_turn_on_calls=False,
            apply_on_recovered_on=True,
        )
    )
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.test": runtime}
    controller._sun_samples = []
    controller._ramp_unsub = object()
    controller._wake_unsub = None
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(state="off", attributes={})
        )
    )

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: now)

    asyncio.run(controller.async_tick())

    assert runtime.last_decision == "skipped_state_off"
    assert runtime.last_decision_at == now
