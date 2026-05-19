"""Dimsome integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .const import DOMAIN, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import DimsomeController

    type DimsomeConfigEntry = ConfigEntry[DimsomeController]
else:
    type DimsomeConfigEntry = Any

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up Dimsome panel, WebSocket API, and optional YAML import."""
    from .api import register_ws_api
    from .coordinator import register_services
    from .panel import async_setup_panel

    await async_setup_panel(hass)
    register_ws_api(hass)
    register_services(hass)

    if DOMAIN not in config:
        return True
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "import"},
            data=config[DOMAIN],
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DimsomeConfigEntry) -> bool:
    """Set up Dimsome from a config entry."""
    from .coordinator import DimsomeController
    from .models import resolve_light_configs

    raw_config = {**entry.data, **entry.options}
    try:
        light_configs = resolve_light_configs(raw_config)
    except (KeyError, TypeError, ValueError) as err:
        _LOGGER.error("Invalid Dimsome configuration: %s", err)
        return False

    existing_lights = {
        entity_id
        for loaded_entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if loaded_entry.entry_id != entry.entry_id
        for entity_id in loaded_entry.runtime_data.lights
    }
    duplicate_lights = {
        config.entity_id for config in light_configs if config.entity_id in existing_lights
    }
    if duplicate_lights:
        _LOGGER.error(
            "Lights can only be controlled by one Dimsome entry: %s",
            ", ".join(sorted(duplicate_lights)),
        )
        return False

    controller = DimsomeController(hass, entry.entry_id, light_configs)
    entry.runtime_data = controller
    await controller.async_start()
    _migrate_per_light_entities(hass, entry.entry_id, controller.lights)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DimsomeConfigEntry) -> bool:
    """Unload a Dimsome config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    await entry.runtime_data.async_stop()
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: DimsomeConfigEntry
) -> None:
    """Reload Dimsome when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _migrate_per_light_entities(
    hass: HomeAssistant, entry_id: str, lights: dict[str, Any]
) -> None:
    """Attach existing per-light entities to their per-light devices."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for entity_id in lights:
        light_device = device_registry.async_get_or_create(
            config_entry_id=entry_id,
            identifiers={(DOMAIN, entry_id, entity_id)},
            name=entity_id,
            via_device=(DOMAIN, entry_id),
        )
        slug = entity_id.replace(".", "_")
        legacy_switch_unique_id = f"{entry_id}_{slug}_dimsum_enabled"
        current_switch_unique_id = f"{entry_id}_{slug}_enabled"
        _move_entity_to_device(
            entity_registry, "button", f"{entry_id}_{slug}_resume", light_device.id
        )
        _move_entity_to_device(
            entity_registry, "sensor", f"{entry_id}_{slug}_status", light_device.id
        )
        if entity_registry.async_get_entity_id(
            "switch", DOMAIN, legacy_switch_unique_id
        ):
            if current_entity_id := entity_registry.async_get_entity_id(
                "switch", DOMAIN, current_switch_unique_id
            ):
                entity_registry.async_remove(current_entity_id)
            _move_entity_to_device(
                entity_registry, "switch", legacy_switch_unique_id, light_device.id
            )
        else:
            _move_entity_to_device(
                entity_registry, "switch", current_switch_unique_id, light_device.id
            )


def _move_entity_to_device(
    entity_registry: Any,
    domain: str,
    unique_id: str,
    device_id: str,
) -> None:
    """Move one existing Dimsome entity registry entry to a device."""
    if entity_id := entity_registry.async_get_entity_id(domain, DOMAIN, unique_id):
        entity_registry.async_update_entity(entity_id, device_id=device_id)
