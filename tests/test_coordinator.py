"""Tests for Home Assistant coordinator glue."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from homeassistant.const import ATTR_ENTITY_ID

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

TZ = ZoneInfo("Europe/Amsterdam")
UTC = ZoneInfo("UTC")


def _civil_config(**overrides: Any) -> ResolvedLightConfig:
    """A civil-dusk / fixed-dawn config used across coordinator tests."""
    base = dict(
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
    base.update(overrides)
    return ResolvedLightConfig(**base)


def _dusk_lookup(local_dusk: time):
    """A civil lookup returning a fixed local dusk per date; dawn unused."""

    def lookup(event: SunEvent, day) -> datetime | None:
        if event is SunEvent.CIVIL_DUSK:
            return datetime.combine(day, local_dusk, tzinfo=TZ)
        return None

    return lookup


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


def test_start_registers_periodic_refresh(monkeypatch) -> None:
    """A periodic refresh tick must run even when no ramp timer is active."""
    controller = coordinator.DimsomeController(
        SimpleNamespace(
            states=SimpleNamespace(get=lambda _: None),
            bus=SimpleNamespace(async_listen=lambda *_: lambda: None),
        ),
        "entry",
        [_civil_config()],
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

    async def fake_tick(*_: Any) -> None:
        return None

    monkeypatch.setattr(controller, "async_tick", fake_tick)

    asyncio.run(controller.async_start())

    assert intervals == [
        (controller.hass, controller.async_tick, coordinator.REFRESH_INTERVAL)
    ]
    assert controller._refresh_unsub is not None


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


def test_resume_with_empty_selection_does_not_resume_all(monkeypatch) -> None:
    """An explicit empty selection is a no-op, not an alias for every light."""
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    runtime = LightRuntime(config=SimpleNamespace(entity_id="light.test"))
    runtime.stood_down = True
    controller.lights = {"light.test": runtime}
    tick_calls = []

    async def fake_tick() -> None:
        tick_calls.append(None)

    monkeypatch.setattr(controller, "async_tick", fake_tick)

    asyncio.run(controller.async_resume([]))

    assert runtime.stood_down is True
    assert tick_calls == []


def test_resume_service_empty_entity_list_is_noop() -> None:
    """Service calls with entity_id: [] must not resume every light."""
    calls = []

    class _FakeController:
        lights = {"light.test": object()}

        async def async_resume(self, selected: object) -> None:
            calls.append(selected)

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [
                SimpleNamespace(
                    state=coordinator.ConfigEntryState.LOADED,
                    runtime_data=_FakeController(),
                )
            ]
        )
    )
    call = SimpleNamespace(data={ATTR_ENTITY_ID: []})

    asyncio.run(coordinator.async_resume_service(hass, call))

    assert calls == []


def test_turn_on_verification_reapplies_mismatched_target(monkeypatch) -> None:
    """Turn-on verification must force a retry when restore timing wins."""
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    runtime = LightRuntime(config=SimpleNamespace(entity_id="light.test"))
    target = LightTarget(50)
    runtime.last_target = target
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
    monkeypatch.setattr(coordinator, "active_window", lambda *_: object())
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)

    asyncio.run(controller._async_verify_turn_on_target(runtime))

    assert calls == [(runtime, target, None)]


def test_turn_on_verification_does_not_revert_manual_plateau_change(
    monkeypatch,
) -> None:
    """Plateau-time manual changes after turn-on must not be defended."""
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    runtime = LightRuntime(config=SimpleNamespace(entity_id="light.test"))
    target = LightTarget(30)
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(
                state="on",
                attributes={"brightness": 255},
                context=SimpleNamespace(id="manual", parent_id=None, user_id="user"),
            )
        )
    )
    controller._automation_context_ids = []
    controller._native_user_ids = frozenset()
    calls = []

    async def fake_sleep(_: float) -> None:
        return None

    async def fake_apply(call_runtime: LightRuntime, call_target: LightTarget) -> None:
        calls.append((call_runtime, call_target))

    monkeypatch.setattr(coordinator.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator, "target_for_now", lambda *_: target)
    monkeypatch.setattr(coordinator, "active_window", lambda *_: None)
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)

    asyncio.run(controller._async_verify_turn_on_target(runtime))

    assert calls == []


def test_turn_on_waits_settle_delay_before_computing_target(monkeypatch) -> None:
    """Turn-on handling should wait for device on transitions before targeting."""
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    runtime = LightRuntime(
        config=SimpleNamespace(entity_id="light.test", settle_delay=timedelta(seconds=2))
    )
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(state="on", attributes={})
        )
    )
    calls = []

    async def fake_sleep(delay: float) -> None:
        calls.append(("sleep", delay))

    async def fake_apply(call_runtime: LightRuntime, call_target: LightTarget) -> None:
        calls.append(("apply", call_runtime, call_target))

    async def fake_verify(call_runtime: LightRuntime) -> None:
        calls.append(("verify", call_runtime))

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
        ("verify", runtime),
    ]


def test_turn_on_handler_does_not_relight_light_turned_off_during_settle_delay(
    monkeypatch,
) -> None:
    """A light switched off during settle delay must stay off."""
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    runtime = LightRuntime(
        config=SimpleNamespace(entity_id="light.test", settle_delay=timedelta(seconds=2))
    )
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(state="off", attributes={})
        )
    )
    calls = []

    async def fake_sleep(delay: float) -> None:
        calls.append(("sleep", delay))

    async def fake_apply(call_runtime: LightRuntime, call_target: LightTarget) -> None:
        calls.append(("apply", call_runtime, call_target))

    async def fake_verify(call_runtime: LightRuntime) -> None:
        calls.append(("verify", call_runtime))

    monkeypatch.setattr(coordinator.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator, "target_for_now", lambda *_: LightTarget(50))
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)
    monkeypatch.setattr(controller, "_async_verify_turn_on_target", fake_verify)

    asyncio.run(controller._async_handle_turn_on(runtime))

    assert calls == [("sleep", 2.0)]


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
    controller._automation_context_ids = []
    controller._turn_on_tasks = set()

    class _FakeTask:
        def add_done_callback(self, _cb: Any) -> None:
            return None

        def cancel(self) -> None:
            return None

    controller.hass = SimpleNamespace(async_create_task=lambda task: _FakeTask())
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


def test_own_clamped_service_echo_does_not_stand_down_ramp(monkeypatch) -> None:
    """A device echo from Dimsome's own context is not a manual override."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)
    runtime = LightRuntime(
        config=SimpleNamespace(
            entity_id="light.test",
            enabled=True,
            apply_on_recovered_on=True,
        )
    )
    runtime.expected_target = LightTarget(1)
    runtime.ignore_updates_until = now + timedelta(seconds=10)
    runtime.last_apply_context_id = "dimsome-call"
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.test": runtime}
    controller._automation_context_ids = []
    controller._native_user_ids = frozenset()

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: now)
    monkeypatch.setattr(coordinator, "active_window", lambda *_: object())
    monkeypatch.setattr(controller, "_schedule_grace_resume", lambda _: None)

    controller._async_light_changed(
        SimpleNamespace(
            data={
                "entity_id": "light.test",
                "old_state": SimpleNamespace(state="on"),
                "new_state": SimpleNamespace(
                    state="on",
                    attributes={"brightness": 26},
                    context=SimpleNamespace(
                        id="dimsome-call", parent_id=None, user_id=None
                    ),
                ),
            }
        )
    )

    assert runtime.stood_down is False
    assert runtime.stood_down_window is None


def test_tick_applies_dusk_ramp_from_civil_lookup(monkeypatch) -> None:
    """At civil dusk the controller starts the dim ramp at max, never snaps to min."""
    now = datetime(2026, 5, 31, 22, 36, 17, tzinfo=TZ)  # exactly civil dusk
    config = _civil_config()
    runtime = LightRuntime(config=config)
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.test": runtime}
    controller._ramp_unsub = None
    controller._wake_unsub = None
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda _: SimpleNamespace(state="on", attributes={})
        )
    )
    controller._civil_lookup = _dusk_lookup(time(22, 36, 17))
    calls = []

    async def fake_apply(_: LightRuntime, target: LightTarget) -> None:
        calls.append(target)

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: now)
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)
    monkeypatch.setattr(controller, "_schedule_wake_timer", lambda *_: None)
    monkeypatch.setattr(
        coordinator, "async_track_time_interval", lambda *_: (lambda: None)
    )

    asyncio.run(controller.async_tick())

    assert calls == [
        LightTarget(
            brightness_pct=80,
            color=ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 2450),
        )
    ]


def test_tick_schedules_wake_from_upcoming_civil_dusk(monkeypatch) -> None:
    """Civil dusk must drive a one-shot wake without catching a sun event."""
    now = datetime(2026, 5, 31, 21, 0, tzinfo=TZ)
    dusk = datetime(2026, 5, 31, 22, 36, 17, tzinfo=TZ)
    config = _civil_config(min_color=None, max_color=None)
    runtime = LightRuntime(config=config)
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.test": runtime}
    controller._ramp_unsub = None
    controller._wake_unsub = None
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda _: SimpleNamespace(state="on", attributes={})
        )
    )
    controller._civil_lookup = _dusk_lookup(time(22, 36, 17))
    wake_calls = []

    async def fake_apply(_: LightRuntime, target: LightTarget) -> None:
        return None

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: now)
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)
    monkeypatch.setattr(
        controller, "_schedule_wake_timer", lambda *args: wake_calls.append(args)
    )

    asyncio.run(controller.async_tick())

    assert wake_calls == [(now, dusk)]


def test_tick_records_last_decision_for_diagnostics(monkeypatch) -> None:
    """Runtime diagnostics must explain why a light did or did not dim."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)
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


def test_one_failing_light_does_not_starve_later_lights_or_timers(monkeypatch) -> None:
    """A per-light apply failure must not abort the whole tick."""
    now = datetime(2026, 5, 4, 22, 30, tzinfo=TZ)
    dim_schedule = ScheduleConfig(ScheduleType.FIXED_TIME, at="22:00")
    broken = LightRuntime(
        config=_civil_config(entity_id="light.broken", dim_schedule=dim_schedule)
    )
    fine = LightRuntime(
        config=_civil_config(entity_id="light.fine", dim_schedule=dim_schedule)
    )
    controller = coordinator.DimsomeController.__new__(coordinator.DimsomeController)
    controller.lights = {"light.broken": broken, "light.fine": fine}
    controller._ramp_unsub = None
    controller._wake_unsub = None
    controller.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity_id: SimpleNamespace(state="on", attributes={})
        )
    )
    controller._civil_lookup = lambda event, day: None
    calls = []
    intervals = []

    async def fake_apply(runtime: LightRuntime, target: LightTarget) -> None:
        calls.append(runtime.config.entity_id)
        if runtime.config.entity_id == "light.broken":
            raise RuntimeError("simulated light integration failure")

    monkeypatch.setattr(coordinator.dt_util, "now", lambda: now)
    monkeypatch.setattr(controller, "_async_apply_target", fake_apply)
    monkeypatch.setattr(
        coordinator,
        "async_track_time_interval",
        lambda *args: intervals.append(args) or (lambda: None),
    )

    asyncio.run(controller.async_tick())

    assert calls == ["light.broken", "light.fine"]
    assert broken.last_decision == "apply_failed"
    assert fine.last_decision == "applied_target"
    assert intervals == [(controller.hass, controller.async_tick, coordinator.RAMP_INTERVAL)]
