"""Button entities for Dimsome."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DimsomeController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dimsome button entities."""
    controller: DimsomeController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DimsomeResumeButton(controller, entry.entry_id)])


class DimsomeResumeButton(ButtonEntity):
    """Resume all lights controlled by one Dimsome config entry."""

    _attr_has_entity_name = True
    _attr_name = "Resume"

    def __init__(self, controller: DimsomeController, entry_id: str) -> None:
        """Initialize the button."""
        self._controller = controller
        self._attr_unique_id = f"{entry_id}_resume"

    async def async_press(self) -> None:
        """Resume all Dimsome-controlled lights in this entry."""
        await self._controller.async_resume()
