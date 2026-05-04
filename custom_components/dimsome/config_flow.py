"""Config flow for Dimsome."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries


from .const import DOMAIN


class DimsomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Dimsome config flow."""

    VERSION = 1


    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="Dimsome", data={})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
        )

    async def async_step_import(
        self, user_input: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Import Dimsome YAML configuration."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(updates=user_input)
        return self.async_create_entry(title="Dimsome", data=user_input)
