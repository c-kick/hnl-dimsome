"""Tests for Home Assistant coordinator glue."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.dimsome.const import DOMAIN, SERVICE_RESUME
from custom_components.dimsome.models import LightRuntime, LightTarget

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
    runtime.last_target = LightTarget(50)
    runtime.pending_target = LightTarget(60)
    runtime.expected_target = LightTarget(70)
    controller.lights = {"light.test": runtime}

    async def fake_tick() -> None:
        return None

    monkeypatch.setattr(controller, "async_tick", fake_tick)

    asyncio.run(controller.async_resume({"light.test"}))

    assert runtime.stood_down is False
    assert runtime.last_target is None
    assert runtime.pending_target is None
    assert runtime.expected_target is None
