"""Switch entities for Dimsome."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    async_add_entities(
        [
            DimsomeLightEnabledSwitch(entry.runtime_data, entry, entity_id)
            for entity_id in entry.runtime_data.lights
        ]
    )


class DimsomeLightEnabledSwitch(SwitchEntity):
    """Enable or indefinitely pause Dimsome for one light."""

    _attr_translation_key = "enabled"

    def __init__(
        self, controller: DimsomeController, entry: DimsomeConfigEntry, entity_id: str
    ) -> None:
        """Initialize the switch."""
        self._controller = controller
        self._entry = entry
        self._entity_id = entity_id
        self._attr_name = f"{entity_id} Dimsome enabled"
        self._attr_unique_id = f"{entry.entry_id}_{_entity_slug(self._entity_id)}_enabled"

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
