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


def config_with_current_light_enabled(
    config: dict[str, object], current_config: dict[str, object]
) -> dict[str, object]:
    """Return config with current per-light enabled flags merged by entity id."""
    updated = deepcopy(config)
    current_lights = current_config.get(CONF_LIGHTS)
    updated_lights = updated.get(CONF_LIGHTS)
    if not isinstance(current_lights, list) or not isinstance(updated_lights, list):
        return updated
    enabled_by_entity = {
        light[CONF_ENTITY_ID]: light[CONF_ENABLED]
        for light in current_lights
        if (
            isinstance(light, dict)
            and isinstance(light.get(CONF_ENTITY_ID), str)
            and CONF_ENABLED in light
        )
    }
    for light in updated_lights:
        if not isinstance(light, dict):
            continue
        entity_id = light.get(CONF_ENTITY_ID)
        if entity_id in enabled_by_entity:
            light[CONF_ENABLED] = enabled_by_entity[entity_id]
    return updated
