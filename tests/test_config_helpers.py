"""Tests for Dimsome config update helpers."""

from __future__ import annotations

from custom_components.dimsome.config_helpers import config_with_light_enabled


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
