"""Regressions for manual overrides, asynchronous writes, and shutdown."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.dimsome import coordinator
from custom_components.dimsome.models import (
    ColorMode,
    ColorTarget,
    LightTarget,
    OverrideResumeMode,
    ResolvedLightConfig,
    ScheduleConfig,
    ScheduleType,
)

UTC = ZoneInfo("UTC")


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    """Keep every event in the same ramp unless a test advances the clock."""
    monkeypatch.setattr(
        coordinator.dt_util, "now", lambda: datetime(2026, 9, 5, 22, 30, tzinfo=UTC)
    )
    monkeypatch.setattr(coordinator, "async_call_later", lambda *args: lambda: None)
    monkeypatch.setattr(
        coordinator, "async_track_time_interval", lambda *args: lambda: None
    )


def _config(entity_id="light.test", **overrides):
    values = dict(
        entity_id=entity_id,
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
        settle_delay=timedelta(0),
    )
    values.update(overrides)
    return ResolvedLightConfig(**values)


def _controller(configs=None):
    calls = []

    async def call(domain, service, data, **kwargs):
        calls.append(data)

    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda _: SimpleNamespace(state="on", attributes={"brightness": 128})
        ),
        services=SimpleNamespace(async_call=call),
        async_create_task=asyncio.create_task,
    )
    controller = coordinator.DimsomeController(hass, "entry", configs or [_config()])
    return controller, calls


def _event(old, new, context_id="manual", user="user"):
    return SimpleNamespace(
        data={
            "entity_id": "light.test",
            "old_state": SimpleNamespace(state=old),
            "new_state": SimpleNamespace(
                state=new,
                attributes={"brightness": 200},
                context=SimpleNamespace(id=context_id, parent_id=None, user_id=user),
            ),
        }
    )


def test_automation_turn_on_preserves_manual_override():
    controller, _ = _controller()
    runtime = controller.lights["light.test"]
    controller._async_light_changed(_event("on", "off"))
    assert runtime.stood_down
    queued = []
    controller._automation_context_ids = ["automation"]
    controller._create_turn_on_task = queued.append

    controller._async_light_changed(_event("off", "on", "automation", None))

    assert runtime.stood_down
    assert queued == []


def test_recovery_preserves_manual_override():
    controller, _ = _controller()
    runtime = controller.lights["light.test"]
    controller._async_light_changed(_event("on", "on"))
    assert runtime.stood_down
    queued = []
    controller._create_turn_on_task = queued.append
    controller._async_light_changed(_event("on", "unavailable", "offline", None))
    controller._async_light_changed(_event("unavailable", "on", "recovery", None))

    assert runtime.stood_down
    assert queued == []


@pytest.mark.parametrize("offline_state", ["unavailable", "unknown"])
def test_recovery_without_manual_action_continues_ramp(offline_state):
    controller, _ = _controller()
    runtime = controller.lights["light.test"]
    queued = []
    controller._create_turn_on_task = queued.append
    controller._async_light_changed(_event("on", offline_state, "offline", None))
    controller._async_light_changed(_event(offline_state, "on", "recovery", None))

    assert not runtime.stood_down
    assert queued == [runtime]


@pytest.mark.parametrize("action", ["disable", "manual"])
def test_settle_handler_rechecks_control_after_wait(monkeypatch, action):
    controller, calls = _controller([_config(settle_delay=timedelta(seconds=0.5))])
    runtime = controller.lights["light.test"]

    async def sleep(_):
        if action == "disable":
            await controller.async_set_enabled("light.test", False)
        else:
            controller._async_light_changed(_event("on", "on"))

    monkeypatch.setattr(coordinator.asyncio, "sleep", sleep)
    asyncio.run(controller._async_handle_turn_on(runtime))

    assert calls == []


def test_previous_ramp_override_does_not_block_next_ramp(monkeypatch):
    controller, calls = _controller()
    runtime = controller.lights["light.test"]
    controller._async_light_changed(_event("on", "on"))
    assert runtime.stood_down
    monkeypatch.setattr(
        coordinator.dt_util, "now", lambda: datetime(2026, 9, 6, 6, 30, tzinfo=UTC)
    )

    asyncio.run(controller._async_apply_target(runtime, LightTarget(45)))

    assert len(calls) == 1


@pytest.mark.parametrize("action", ["manual_off", "disable", "automation_off", "none"])
def test_split_write_rechecks_control_and_light_state(monkeypatch, action):
    controller, calls = _controller([_config(split_turn_on_calls=True)])
    runtime = controller.lights["light.test"]
    target = LightTarget(45, ColorTarget(ColorMode.COLOR_TEMP_KELVIN, 3100))

    async def sleep(_):
        if action == "manual_off":
            controller._async_light_changed(_event("on", "off"))
        elif action == "disable":
            await controller.async_set_enabled("light.test", False)
        elif action == "automation_off":
            controller.hass.states.get = lambda _: SimpleNamespace(state="off")

    monkeypatch.setattr(coordinator.asyncio, "sleep", sleep)
    asyncio.run(controller._async_apply_target(runtime, target))

    assert calls[0] == {"entity_id": "light.test", "color_temp_kelvin": 3100}
    if action == "none":
        assert calls[1:] == [{"entity_id": "light.test", "brightness": 115}]
        assert runtime.last_target == target
    else:
        assert len(calls) == 1
        assert runtime.last_target is None


@pytest.mark.parametrize("manual", [False, True])
def test_queued_target_is_sent_only_while_control_remains_active(manual):
    controller, _ = _controller()
    runtime = controller.lights["light.test"]
    calls = []

    async def send(call_runtime, target, context):
        calls.append(target)
        if len(calls) == 1:
            await controller._async_apply_target(call_runtime, LightTarget(44))
            if manual:
                controller._async_light_changed(_event("on", "on"))
        return True

    controller._async_call_light = send
    asyncio.run(controller._async_apply_target(runtime, LightTarget(45)))

    assert calls == ([LightTarget(45)] if manual else [LightTarget(45), LightTarget(44)])
    assert runtime.pending_target is None


def test_stop_prevents_tick_continuation(monkeypatch):
    controller, calls = _controller([_config("light.first"), _config("light.second")])
    intervals = []
    monkeypatch.setattr(
        coordinator, "async_track_time_interval",
        lambda *args: intervals.append(args) or (lambda: None),
    )

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def send(domain, service, data, **kwargs):
            calls.append(data)
            started.set()
            await release.wait()

        controller.hass.services.async_call = send
        task = asyncio.create_task(controller.async_tick())
        await started.wait()
        await controller.async_stop()
        release.set()
        await task
        await controller.async_tick()

    asyncio.run(scenario())

    assert len(calls) == 1
    assert intervals == []


def test_stop_cancels_and_awaits_turn_on_tasks():
    controller, calls = _controller([_config(settle_delay=timedelta(hours=1))])

    async def scenario():
        controller._create_turn_on_task(controller.lights["light.test"])
        task = next(iter(controller._turn_on_tasks))
        await asyncio.sleep(0)
        await controller.async_stop()
        assert task.cancelled()
        assert not controller._turn_on_tasks

    asyncio.run(scenario())

    assert calls == []


@pytest.mark.parametrize(
    ("now", "start", "expected_delay"),
    [
        ((2026, 10, 25, 2, 55), (2026, 10, 25, 3, 0), 3900),
        ((2026, 3, 29, 1, 58), (2026, 3, 29, 3, 0), 120),
    ],
)
def test_dst_wake_timer_uses_elapsed_time(monkeypatch, now, start, expected_delay):
    controller, _ = _controller()
    delays = []
    monkeypatch.setattr(
        coordinator, "async_call_later",
        lambda hass, delay, callback: delays.append(delay) or (lambda: None),
    )
    tz = ZoneInfo("Europe/Amsterdam")

    controller._schedule_wake_timer(datetime(*now, tzinfo=tz), datetime(*start, tzinfo=tz))

    assert delays == [expected_delay]
