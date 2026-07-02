"""Tests for Dimsome config update helpers."""

from __future__ import annotations

import ast
from pathlib import Path

from custom_components.dimsome.config_helpers import (
    config_with_light_enabled,
    config_with_current_light_enabled,
)


def test_default_config_uses_civil_sun_for_both_ramps() -> None:
    """New Dimsome entries should default to civil dusk and civil dawn."""
    config_flow = ast.parse(Path("custom_components/dimsome/config_flow.py").read_text())
    default_config = next(
        ast.literal_eval(node.value)
        for node in config_flow.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "DEFAULT_CONFIG"
    )

    assert default_config["global"]["dim_schedule"] == {
        "type": "civil_sun",
        "event": "civil_dusk",
    }
    assert default_config["global"]["brighten_schedule"] == {
        "type": "civil_sun",
        "event": "civil_dawn",
    }


def test_switch_enabled_update_preserves_full_config() -> None:
    """Persisting switch state must not replace the integration config."""
    config = {
        "global": {
            "dim_schedule": {"type": "civil_sun", "event": "civil_dusk"},
            "brighten_schedule": {"type": "fixed_time", "at": "06:00:00"},
        },
        "lights": [
            {
                "entity_id": "light.one",
                "min_brightness_pct": 20,
                "max_brightness_pct": 100,
            },
            {
                "entity_id": "light.two",
                "min_brightness_pct": 10,
                "max_brightness_pct": 80,
            },
        ],
    }

    updated = config_with_light_enabled(config, "light.two", False)

    assert updated is not None
    assert updated["global"] == config["global"]
    assert updated["lights"][0] == config["lights"][0]
    assert updated["lights"][1] == {
        "entity_id": "light.two",
        "enabled": False,
        "min_brightness_pct": 10,
        "max_brightness_pct": 80,
    }
    assert "enabled" not in config["lights"][1]


def test_switch_enabled_update_refuses_missing_light() -> None:
    """A stale switch entity must not write an empty or unrelated config."""
    config = {"global": {}, "lights": []}

    assert config_with_light_enabled(config, "light.missing", False) is None


def test_panel_save_preserves_current_enabled_flags() -> None:
    """Panel saves must not revert enabled changes made after panel load."""
    stale_panel_config = {
        "global": {},
        "lights": [
            {
                "entity_id": "light.one",
                "enabled": True,
                "min_brightness_pct": 20,
                "max_brightness_pct": 70,
            },
            {
                "entity_id": "light.new",
                "enabled": True,
                "min_brightness_pct": 10,
                "max_brightness_pct": 80,
            },
        ],
    }
    current_config = {
        "global": {},
        "lights": [
            {
                "entity_id": "light.one",
                "enabled": False,
                "min_brightness_pct": 20,
                "max_brightness_pct": 100,
            }
        ],
    }

    updated = config_with_current_light_enabled(stale_panel_config, current_config)

    assert updated["lights"][0] == {
        "entity_id": "light.one",
        "enabled": False,
        "min_brightness_pct": 20,
        "max_brightness_pct": 70,
    }
    assert updated["lights"][1]["enabled"] is True
    assert stale_panel_config["lights"][0]["enabled"] is True
