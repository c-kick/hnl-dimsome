"""Tests for Home Assistant coordinator glue."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.dimsome.const import DOMAIN, SERVICE_RESUME

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
