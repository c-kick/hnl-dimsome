"""Helpers for safely updating Dimsome config dictionaries."""

from __future__ import annotations

from copy import deepcopy

from .const import CONF_ENABLED, CONF_ENTITY_ID, CONF_LIGHTS


def config_with_light_enabled(
    config: dict[str, object], entity_id: str, enabled: bool
) -> dict[str, object] | None:
    """Return config with one light's enabled flag changed, preserving all other data."""
    updated = deepcopy(config)
    lights = updated.get(CONF_LIGHTS)
    if not isinstance(lights, list):
        return None
    for light in lights:
        if isinstance(light, dict) and light.get(CONF_ENTITY_ID) == entity_id:
            light[CONF_ENABLED] = enabled
            return updated
    return None
