"""Diagnostic sensor entities for Dimsome."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DimsomeController

type DimsomeConfigEntry = ConfigEntry[DimsomeController]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimsomeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dimsome diagnostic sensors."""
    async_add_entities(
        [
            DimsomeLightStatusSensor(entry.runtime_data, entry.entry_id, entity_id)
            for entity_id in entry.runtime_data.lights
        ]
    )


class DimsomeLightStatusSensor(SensorEntity):
    """Expose Dimsome runtime state for one controlled light."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, controller: DimsomeController, entry_id: str, entity_id: str
    ) -> None:
        """Initialize the sensor."""
        self._controller = controller
        self._entity_id = entity_id
        self._attr_name = f"{entity_id} Dimsome status"
        self._attr_unique_id = f"{entry_id}_{_entity_slug(entity_id)}_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id, entity_id)},
            "name": entity_id,
            "via_device": (DOMAIN, entry_id),
        }

    @property
    def native_value(self) -> str:
        """Return the concise Dimsome status."""
        return self._runtime_status()["status"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed runtime diagnostics."""
        return self._runtime_status()

    def _runtime_status(self) -> dict[str, Any]:
        """Return current runtime diagnostics for this light."""
        return self._controller.runtime_status(self._entity_id)[self._entity_id]


def _entity_slug(entity_id: str) -> str:
    """Return a stable unique-id fragment for an entity id."""
    return entity_id.replace(".", "_")
