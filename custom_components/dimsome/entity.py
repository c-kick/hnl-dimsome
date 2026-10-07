"""Device and unique-id naming shared by Dimsome's per-light entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.helpers.entity_registry import EntityRegistry


def light_unique_id(entry_id: str, entity_id: str, suffix: str) -> str:
    """Return the unique id of a per-light entity, e.g. suffix "resume"."""
    return f"{entry_id}_{entity_id.replace('.', '_')}_{suffix}"


def light_device_info(entry_id: str, entity_id: str) -> dict[str, Any]:
    """Return device metadata for one Dimsome-controlled light."""
    return {
        "identifiers": {(DOMAIN, entry_id, entity_id)},
        "name": entity_id,
    }


def enabled_switch_unique_id(
    entity_registry: EntityRegistry, entry_id: str, entity_id: str
) -> str:
    """Return the enabled switch's unique id, keeping a registered legacy id.

    Older releases used a "dimsum_enabled" suffix. When that entity exists it is
    kept, and any duplicate under the current id is removed from the registry.
    """
    current_unique_id = light_unique_id(entry_id, entity_id, "enabled")
    legacy_unique_id = light_unique_id(entry_id, entity_id, "dimsum_enabled")
    if entity_registry.async_get_entity_id("switch", DOMAIN, legacy_unique_id):
        if current_entity_id := entity_registry.async_get_entity_id(
            "switch", DOMAIN, current_unique_id
        ):
            entity_registry.async_remove(current_entity_id)
        return legacy_unique_id
    return current_unique_id
