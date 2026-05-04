"""Sidebar panel registration for Dimsome."""

from __future__ import annotations

from homeassistant.components.frontend import async_register_built_in_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, VERSION

PANEL_URL = f"/api/{DOMAIN}/panel"
PANEL_URL_VERSIONED = f"{PANEL_URL}?v={VERSION}"
PANEL_FILENAME = "frontend/dimsome_panel.js"


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the Dimsome sidebar panel."""
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_URL,
                hass.config.path(f"custom_components/{DOMAIN}/{PANEL_FILENAME}"),
                True,
            )
        ]
    )

    async_register_built_in_panel(
        hass=hass,
        component_name="custom",
        sidebar_title="Dimsome",
        sidebar_icon="mdi:brightness-6",
        frontend_url_path=DOMAIN,
        require_admin=False,
        config={
            "_panel_custom": {
                "name": "dimsome-panel",
                "module_url": PANEL_URL_VERSIONED,
            }
        },
    )
