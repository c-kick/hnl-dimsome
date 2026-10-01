"""Switch entities for Dimsome."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DimsomeConfigEntry
from .config_helpers import config_with_light_enabled
from .coordinator import DimsomeController
from .entity import enabled_switch_unique_id, light_device_info


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
                enabled_switch_unique_id(entity_registry, entry.entry_id, entity_id),
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
        self._attr_name = f"{entity_id} DimSome enabled"
        self._attr_unique_id = unique_id
        self._attr_device_info = light_device_info(entry.entry_id, entity_id)

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
