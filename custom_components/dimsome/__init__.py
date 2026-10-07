"""Dimsome integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from .const import CONF_GLOBAL, CONF_LIGHTS, DOMAIN, PLATFORMS
from .entity import enabled_switch_unique_id, light_unique_id

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .coordinator import DimsomeController

    type DimsomeConfigEntry = ConfigEntry[DimsomeController]
else:
    type DimsomeConfigEntry = Any

_LOGGER = logging.getLogger(__name__)

# Only the outer shape: models.py validates the contents when the entry is set up.
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({vol.Optional(CONF_GLOBAL): dict, vol.Optional(CONF_LIGHTS): list})},
    extra=vol.ALLOW_EXTRA,
)


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
    from .models import resolve_light_configs, resolve_native_user_ids

    raw_config = {**entry.data, **entry.options}
    try:
        light_configs = resolve_light_configs(raw_config)
        native_user_ids = resolve_native_user_ids(raw_config)
    except (KeyError, TypeError, ValueError) as err:
        _LOGGER.error("Invalid DimSome configuration: %s", err)
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
            "Lights can only be controlled by one DimSome entry: %s",
            ", ".join(sorted(duplicate_lights)),
        )
        return False

    controller = DimsomeController(
        hass,
        entry.entry_id,
        light_configs,
        native_user_ids=native_user_ids,
    )
    controller.restore_manual_overrides(_take_manual_overrides(hass, entry.entry_id))
    entry.runtime_data = controller
    await controller.async_start()
    _migrate_per_light_entities(hass, entry.entry_id, controller.lights)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No update listener on purpose: the only config writers (panel save and
    # the enable switch) reload explicitly or update the running controller
    # in place. A listener here would double-reload on every save.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DimsomeConfigEntry) -> bool:
    """Unload a Dimsome config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    await entry.runtime_data.async_stop()
    _stash_manual_overrides(hass, entry)
    return True


# Manual overrides live only in memory. A reload (every panel save) hands them
# to the next controller through hass.data; a restart still clears them.
_OVERRIDE_HANDOVER = "override_handover"


def _stash_manual_overrides(hass: HomeAssistant, entry: DimsomeConfigEntry) -> None:
    """Keep the stopping controller's manual overrides for the next setup."""
    handover = hass.data.setdefault(DOMAIN, {}).setdefault(_OVERRIDE_HANDOVER, {})
    handover[entry.entry_id] = entry.runtime_data.manual_overrides()


def _take_manual_overrides(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Return and forget the overrides stashed for entry_id."""
    return hass.data.get(DOMAIN, {}).get(_OVERRIDE_HANDOVER, {}).pop(entry_id, {})


def _migrate_per_light_entities(
    hass: HomeAssistant, entry_id: str, lights: dict[str, Any]
) -> None:
    """Register per-light devices under the hub device and attach their entities."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, entry_id)},
        name="DimSome",
    )
    for entity_id in lights:
        light_device = device_registry.async_get_or_create(
            config_entry_id=entry_id,
            identifiers={(DOMAIN, entry_id, entity_id)},
            name=entity_id,
        )
        # async_get_or_create only accepts via_device_id from HA 2026.8;
        # async_update_device accepts it on every supported version.
        device_registry.async_update_device(light_device.id, via_device_id=hub_device.id)
        _move_entity_to_device(
            entity_registry,
            "button",
            light_unique_id(entry_id, entity_id, "resume"),
            light_device.id,
        )
        _move_entity_to_device(
            entity_registry,
            "sensor",
            light_unique_id(entry_id, entity_id, "status"),
            light_device.id,
        )
        _move_entity_to_device(
            entity_registry,
            "switch",
            enabled_switch_unique_id(entity_registry, entry_id, entity_id),
            light_device.id,
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
