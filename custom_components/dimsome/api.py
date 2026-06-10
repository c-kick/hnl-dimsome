"""WebSocket API for the Dimsome management panel."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .models import resolve_light_configs


def _get_entry(hass: HomeAssistant):
    """Return the Dimsome config entry, if any."""
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _current_config(entry) -> dict[str, Any]:
    """Return current config data plus options."""
    return deepcopy({**entry.data, **entry.options})


def _validate_config(config: dict[str, Any]) -> str | None:
    """Return an error string if config is invalid."""
    try:
        resolve_light_configs(config)
    except (KeyError, TypeError, ValueError) as err:
        return str(err)
    return None


def _light_states(hass: HomeAssistant, config: dict[str, Any]) -> dict[str, Any]:
    """Return basic state for configured lights."""
    states = {}
    for light in config.get("lights", []):
        entity_id = light.get("entity_id")
        state = hass.states.get(entity_id) if entity_id else None
        states[entity_id] = {
            "state": state.state if state else None,
            "attributes": dict(state.attributes) if state else {},
        }
    return states


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/config"})
@websocket_api.async_response
async def ws_config(hass: HomeAssistant, connection, msg) -> None:
    """Return current Dimsome configuration."""
    entry = _get_entry(hass)
    if entry is None:
        connection.send_result(
            msg["id"],
            {
                "configured": False,
                "config": {"global": {}, "lights": []},
                "light_states": {},
                "runtime": {},
            },
        )
        return

    config = _current_config(entry)
    runtime = (
        entry.runtime_data.runtime_status()
        if entry.state is ConfigEntryState.LOADED
        else {}
    )
    connection.send_result(
        msg["id"],
        {
            "configured": True,
            "entry_id": entry.entry_id,
            "loaded": entry.state is ConfigEntryState.LOADED,
            "config": config,
            "light_states": _light_states(hass, config),
            "runtime": runtime,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/save_config",
        vol.Required("config"): dict,
    }
)
@websocket_api.async_response
async def ws_save_config(hass: HomeAssistant, connection, msg) -> None:
    """Validate and save Dimsome configuration."""
    entry = _get_entry(hass)
    if entry is None:
        connection.send_error(msg["id"], "not_configured", "Dimsome is not set up")
        return

    config = deepcopy(msg["config"])
    config.setdefault("global", {})
    config.setdefault("lights", [])
    error = _validate_config(config)
    if error is not None:
        connection.send_error(msg["id"], "invalid_config", error)
        return

    hass.config_entries.async_update_entry(entry, data=config, options={})
    await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(msg["id"], {"ok": True})


def register_ws_api(hass: HomeAssistant) -> None:
    """Register Dimsome WebSocket commands."""
    websocket_api.async_register_command(hass, ws_config)
    websocket_api.async_register_command(hass, ws_save_config)
