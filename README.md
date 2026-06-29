# Dimsome

<img width="1048" height="242" alt="image" src="https://github.com/user-attachments/assets/45ee9bda-f054-4f88-b1e6-e0d03d36f0ab" />

Dimsome is a custom [Home Assistant](https://www.home-assistant.io/) integration for deterministic adaptive light dimming.

> **Beta.** Dimsome is still in active development. Behavior and configuration may change between releases.

It drives configured lights with two daily ramps:

- a **dim** ramp from the day target down to the night target
- a **brighten** ramp from the night target up to the day target

By default the dim ramp starts at civil dusk and the brighten ramp at civil dawn, taken directly from Home Assistant's astral data for the current date. Either ramp can instead use a fixed clock time, which takes precedence over the civil-sun schedule.

Between ramps, Dimsome holds the plateau: when a controlled light turns on after the dim ramp it is set to the night target, and after the brighten ramp to the day target. If a light is changed by hand during an active ramp, Dimsome stands down for that light until the ramp ends (or sooner, via the resume button or `dimsome.resume`).

## Installation

Clone (or update) this repository, then copy the integration into your Home Assistant `custom_components` directory:

```bash
# first time
git clone https://github.com/c-kick/hnl-dimsome.git
cd hnl-dimsome

# to update later
git pull

# copy the integration into Home Assistant's config folder
cp -r custom_components/dimsome /config/custom_components/dimsome
```

The integration must end up here:

```text
custom_components/dimsome -> /config/custom_components/dimsome
```

For local development, bind-mount this repository into the Home Assistant container instead of copying:

```text
./custom_components/dimsome -> /config/custom_components/dimsome:ro
```

Restart Home Assistant after adding or changing integration Python files.

## Setup

1. In Home Assistant, go to **Settings → Devices & services**.
2. Select **Add integration** and search for **Dimsome**.
3. Create the entry (Dimsome supports a single integration entry).
4. Open the **Dimsome** sidebar panel and configure global defaults and the lights to control.

## Configuration

Configuration is done from the Dimsome sidebar panel. Per-light settings fall back to the global defaults unless overridden.

### Global settings

- **Dimming** / **Brightening** schedule — civil sun (`civil_dusk` / `civil_dawn`) or a fixed time.
- **Ramp Duration** — how long each transition takes.
- **Override Resume** — how control returns after a manual change: `Manual Only` or `After Grace Period`.
- **Grace Period** — delay before automatic resume (used by `After Grace Period`).
- **Split Brightness & Color Calls** — send brightness and color as separate `light.turn_on` calls, for lights that reject combined updates.
- **Apply On Recovery** — re-apply the current target when a light comes back online while already on.
- **Native Users** — comma-separated Home Assistant user IDs whose light changes are treated as automations rather than manual overrides (useful for Node-RED or other token-based integrations).

### Per-light settings

- **Light Entity** — the light to control.
- **Minimum / Maximum Brightness** — night and day targets, as a percentage from `1` to `100` (converted internally to Home Assistant's `1`–`255` scale).
- **Adjust Color Temperature** — optional minimum/maximum `color_temp_kelvin` targets (color support is intentionally limited to color temperature).
- **Split Brightness & Color Calls**, **Apply On Recovery** — per-light overrides of the global toggles.
- **Settle Delay** — wait after a light turns on before applying its target.
- **Schedule / Ramp Duration / Override Resume / Grace Period overrides** — per-light overrides of the global schedule and resume behavior.

Per-light **enable/pause** is handled by the `Dimsome enabled` switch entity.

## Entities

- `button.dimsome_resume` — resume Dimsome control for all configured lights.
- Per-light resume buttons — resume one light.
- Per-light `Dimsome enabled` switches — enable or pause control for one light.
- Per-light diagnostic sensors — expose runtime state via attributes such as `status`, `active_window`, `next_window_start`, `target`, and manual-override state. During an active ramp `next_window_start` points to the *following* ramp; use `active_window` and `target` to confirm Dimsome is ramping correctly.

## Service: `dimsome.resume`

Resume Dimsome control for all configured lights, or only the listed entities.

```yaml
service: dimsome.resume
data:
  entity_id:
    - light.living_room
    - light.hallway
```

Omit `entity_id` to resume all configured lights.

## YAML import

The UI and panel are the primary setup path. YAML remains available as an import path for development and migration; it is imported into the same single entry.

```yaml
dimsome:
  global:
    dim_schedule:
      type: civil_sun
      event: civil_dusk
    brighten_schedule:
      type: civil_sun
      event: civil_dawn
    ramp_duration: "01:00:00"
    override_resume_mode: manual_only      # or after_grace_period
    override_grace_period: "00:15:00"
    split_turn_on_calls: false
    apply_on_recovered_on: true
    native_user_ids:
      - "abcdef0123456789abcdef0123456789"  # e.g. the Node-RED user
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
      settle_delay: 0.5
      dim_schedule:
        type: fixed_time
        at: "22:30"
```

A schedule is either `{ type: fixed_time, at: "HH:MM" }` or `{ type: civil_sun, event: civil_dawn | civil_dusk }`.

## Development

```bash
pytest
```

Source lives in `custom_components/dimsome/`; tests in `tests/`. The integration version is in `custom_components/dimsome/const.py`, and Home Assistant manifest metadata in `custom_components/dimsome/manifest.json`.
