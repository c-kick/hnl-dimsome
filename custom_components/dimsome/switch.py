"""Switch entities for Dimsome."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_helpers import config_with_light_enabled
from .const import DOMAIN
from .coordinator import DimsomeController

type DimsomeConfigEntry = ConfigEntry[DimsomeController]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimsomeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dimsome switch entities."""
    entity_registry = er.async_get(hass)
    async_add_entities(
        [
            DimsomeLightEnabledSwitch(
                entry.runtime_data,
                entry,
                entity_id,
                _enabled_switch_unique_id(entry.entry_id, entity_id, entity_registry),
            )
            for entity_id in entry.runtime_data.lights
        ]
    )


class DimsomeLightEnabledSwitch(SwitchEntity):
    """Enable or indefinitely pause Dimsome for one light."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "enabled"

    def __init__(
        self,
        controller: DimsomeController,
        entry: DimsomeConfigEntry,
        entity_id: str,
        unique_id: str,
    ) -> None:
        """Initialize the switch."""
        self._controller = controller
        self._entry = entry
        self._entity_id = entity_id
        self._attr_name = f"{entity_id} Dimsome enabled"
        self._attr_unique_id = unique_id
        self._attr_device_info = _light_device_info(entry.entry_id, entity_id)

    @property
    def is_on(self) -> bool:
        """Return whether Dimsome control is enabled for this light."""
        return self._controller.lights[self._entity_id].config.enabled

    async def async_turn_on(self, **_: object) -> None:
        """Enable Dimsome control for this light."""
        await self._async_set_enabled(True)

    async def async_turn_off(self, **_: object) -> None:
        """Indefinitely pause Dimsome control for this light."""
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled: bool) -> None:
        """Persist and apply the enabled state."""
        config = config_with_light_enabled(
            {**self._entry.data, **self._entry.options}, self._entity_id, enabled
        )
        if config is None:
            return
        self.hass.config_entries.async_update_entry(self._entry, data=config, options={})
        await self._controller.async_set_enabled(self._entity_id, enabled)
        self.async_write_ha_state()


def _entity_slug(entity_id: str) -> str:
    """Return a stable unique-id fragment for an entity id."""
    return entity_id.replace(".", "_")


def _enabled_switch_unique_id(
    entry_id: str, entity_id: str, entity_registry: er.EntityRegistry
) -> str:
    """Return the stable unique ID for a per-light enabled switch."""
    slug = _entity_slug(entity_id)
    current_unique_id = f"{entry_id}_{slug}_enabled"
    legacy_unique_id = f"{entry_id}_{slug}_dimsum_enabled"
    if entity_registry.async_get_entity_id("switch", DOMAIN, legacy_unique_id):
        if current_entity_id := entity_registry.async_get_entity_id(
            "switch", DOMAIN, current_unique_id
        ):
            entity_registry.async_remove(current_entity_id)
        return legacy_unique_id
    return current_unique_id


def _light_device_info(entry_id: str, entity_id: str) -> dict[str, Any]:
    """Return device metadata for one Dimsome-controlled light."""
    return {
        "identifiers": {(DOMAIN, entry_id, entity_id)},
        "name": entity_id,
        "via_device": (DOMAIN, entry_id),
    }
