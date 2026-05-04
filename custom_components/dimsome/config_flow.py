"""Config flow for Dimsome."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN


DEFAULT_CONFIG: dict[str, Any] = {
    "global": {
        "dim_schedule": {"type": "civil_sun", "event": "civil_dusk"},
        "brighten_schedule": {"type": "fixed_time", "at": "06:00:00"},
        "ramp_duration": "01:00:00",
        "override_resume_mode": "manual_only",
        "override_grace_period": "00:15:00",
        "split_turn_on_calls": False,
    },
    "lights": [],
}


class DimsomeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Dimsome config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Create the Dimsome entry; detailed setup happens in the panel."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(title="Dimsome", data=deepcopy(DEFAULT_CONFIG))

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
