# Dimsome

Dimsome is a custom [Home Assistant](https://www.home-assistant.io/) integration for deterministic adaptive light dimming.

It controls configured lights with two daily ramps:

- dim from the day target to the night target
- brighten from the night target to the day target

By default, Dimsome dims at civil dusk and brightens at a fixed 06:00 time. Each ramp can also be configured with a fixed time. Fixed times take precedence over the civil-sun schedule.

## Features

- Home Assistant config flow setup
- Dimsome sidebar panel for managing global defaults and per-light settings
- Civil dusk and civil dawn schedules derived from `sun.sun`
- Fixed-time dimming and brightening schedules
- Per-light minimum and maximum brightness targets
- Optional `color_temp_kelvin` targets
- Per-light schedule, ramp duration, and override behavior
- Manual override stand-down during an active ramp
- Optional grace-period resume after a manual override
- Per-light enable switches, resume buttons, and diagnostic sensors
- `dimsome.resume` service for automations
- Optional split `light.turn_on` calls for lights that cannot apply brightness and color together

## Installation

Copy or mount the integration directory into Home Assistant:

```text
custom_components/dimsome -> /config/custom_components/dimsome
```

For local development, this repository is intended to be bind-mounted into the Home Assistant container:

```text
./custom_components/dimsome -> /config/custom_components/dimsome:ro
```

Restart Home Assistant after adding or changing integration Python files.

## Setup

1. In Home Assistant, go to **Settings -> Devices & services**.
2. Select **Add integration**.
3. Search for **Dimsome**.
4. Create the Dimsome integration entry.
5. Open the **Dimsome** sidebar panel.
6. Configure global defaults and add the lights Dimsome should control.

Dimsome supports one integration entry.

## How It Works

Dimsome computes the expected light target from the current schedule window.

During a dim ramp, it moves each enabled light from its configured high/day target to its low/night target. During a brighten ramp, it moves each enabled light from its configured low/night target to its high/day target.

Outside active ramps, Dimsome applies the correct plateau target when a controlled light turns on:

- after the dim ramp and before the next brighten ramp, lights are set to the low/night target
- after the brighten ramp and before the next dim ramp, lights are set to the high/day target

If a light is manually changed during an active ramp, Dimsome stands down for that light for the rest of that ramp. Use the resume button or `dimsome.resume` service to hand control back sooner.

Civil dawn and dusk are calculated from Home Assistant's `sun.sun` state and next-event attributes. Dimsome periodically refreshes this state so behavior remains deterministic across restarts and missed events.

## Configuration

Most configuration should be done from the Dimsome sidebar panel.

Global defaults include:

- dim schedule
- brighten schedule
- ramp duration
- manual override resume mode
- override grace period
- split turn-on calls

Each light includes:

- light entity ID
- enabled state
- minimum brightness percentage
- maximum brightness percentage
- optional minimum color temperature
- optional maximum color temperature
- optional per-light overrides for schedule, ramp duration, and override behavior

Brightness values are percentages from `1` to `100`. Dimsome converts them to Home Assistant's `1` to `255` brightness scale internally.

Color support is intentionally limited to `color_temp_kelvin`.

## Entities

Dimsome creates helper entities for configured lights:

- `button.dimsome_resume` resumes Dimsome control for all configured lights.
- Per-light resume buttons resume Dimsome control for one light.
- Per-light `Dimsome enabled` switches enable or pause Dimsome control for one light.
- Per-light diagnostic sensors expose runtime state such as `status`, `active_window`, `next_window_start`, `target`, and manual override state.

During an active ramp, `next_window_start` points to the following ramp. Use `active_window` and `target` to determine whether Dimsome is currently ramping correctly.

## Services

### `dimsome.resume`

Resume Dimsome control for all configured lights, or only selected light entities.

```yaml
service: dimsome.resume
data:
  entity_id:
    - light.living_room
    - light.hallway
```

Omit `entity_id` to resume all configured lights.

## YAML Import

The primary setup path is the Home Assistant UI and Dimsome sidebar panel. YAML remains available as an import path for development and migration.

```yaml
dimsome:
  global:
    dim_schedule:
      type: civil_sun
      event: civil_dusk
    brighten_schedule:
      type: fixed_time
      at: "06:00:00"
    ramp_duration: "01:00:00"
    override_resume_mode: manual_only
    override_grace_period: "00:15:00"
    split_turn_on_calls: false
  lights:
    - entity_id: light.living_room
      enabled: true
      min_brightness_pct: 10
      max_brightness_pct: 80
      min_color:
        mode: color_temp_kelvin
        value: 2200
      max_color:
        mode: color_temp_kelvin
        value: 4000
      split_turn_on_calls: true
    - entity_id: light.hallway
      min_brightness_pct: 20
      max_brightness_pct: 100
      ramp_duration: "00:30:00"
      override_resume_mode: after_grace_period
      override_grace_period: "00:15:00"
      dim_schedule:
        type: fixed_time
        at: "22:30"
```

Supported schedule forms:

```yaml
type: fixed_time
at: "22:30"
```

```yaml
type: civil_sun
event: civil_dusk
```

Civil sun events are `civil_dusk` and `civil_dawn`.

Supported override resume modes are:

- `manual_only`
- `after_grace_period`

## Development

Run tests from the repository root:

```bash
pytest
```

Source code lives in `custom_components/dimsome/`. Tests live in `tests/`.

The integration version is defined in `custom_components/dimsome/const.py`; Home Assistant manifest metadata lives in `custom_components/dimsome/manifest.json`.
