"""Button entities for Dimsome."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DimsomeController

type DimsomeConfigEntry = ConfigEntry[DimsomeController]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimsomeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dimsome button entities."""
    controller = entry.runtime_data
    async_add_entities(
        [DimsomeResumeButton(controller, entry.entry_id)]
        + [
            DimsomeLightResumeButton(controller, entry.entry_id, entity_id)
            for entity_id in controller.lights
        ]
    )


class DimsomeResumeButton(ButtonEntity):
    """Resume all lights controlled by one Dimsome config entry."""

    _attr_has_entity_name = True
    _attr_translation_key = "resume"

    def __init__(self, controller: DimsomeController, entry_id: str) -> None:
        """Initialize the button."""
        self._controller = controller
        self._attr_unique_id = f"{entry_id}_resume"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Dimsome",
        }

    async def async_press(self) -> None:
        """Resume all Dimsome-controlled lights in this entry."""
        await self._controller.async_resume()


class DimsomeLightResumeButton(ButtonEntity):
    """Resume one light controlled by Dimsome."""

    _attr_translation_key = "resume"

    def __init__(
        self, controller: DimsomeController, entry_id: str, entity_id: str
    ) -> None:
        """Initialize the button."""
        self._controller = controller
        self._entity_id = entity_id
        self._attr_name = f"{entity_id} Resume"
        self._attr_unique_id = f"{entry_id}_{_entity_slug(entity_id)}_resume"
        self._attr_device_info = _light_device_info(entry_id, entity_id)

    async def async_press(self) -> None:
        """Resume Dimsome control for this light."""
        await self._controller.async_resume({self._entity_id})


def _entity_slug(entity_id: str) -> str:
    """Return a stable unique-id fragment for an entity id."""
    return entity_id.replace(".", "_")


def _light_device_info(entry_id: str, entity_id: str) -> dict[str, Any]:
    """Return device metadata for one Dimsome-controlled light."""
    return {
        "identifiers": {(DOMAIN, entry_id, entity_id)},
        "name": entity_id,
        "via_device": (DOMAIN, entry_id),
    }
