"""Dimsome integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .const import DOMAIN, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    type DimsomeConfigEntry = ConfigEntry[dict[str, object]]
else:
    type DimsomeConfigEntry = Any

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up Dimsome from YAML, when present."""
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
    from .coordinator import DimsomeController, register_services
    from .models import resolve_light_configs

    raw_config = {**entry.data, **entry.options}
    try:
        light_configs = resolve_light_configs(raw_config)
    except ValueError as err:
        _LOGGER.error("Invalid Dimsome configuration: %s", err)
        return False

    existing_lights = {
        entity_id
        for controller in hass.data.get(DOMAIN, {}).values()
        for entity_id in controller.lights
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
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    register_services(hass)
    await controller.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DimsomeConfigEntry) -> bool:
    """Unload a Dimsome config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    controller = hass.data[DOMAIN].pop(entry.entry_id, None)
    if controller is not None:
        await controller.async_stop()
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True
