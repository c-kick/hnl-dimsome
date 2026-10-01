# DimSome

<img width="2172" height="724" alt="DimSome - Deterministic adaptive light dimming for Home Assistant" src="https://github.com/user-attachments/assets/b36cd847-272b-4998-a4fa-354c1fffacba" />

DimSome is a custom [Home Assistant](https://www.home-assistant.io/) integration for deterministic adaptive light dimming. In short: use it to dim your lights down in the evenening, and the reverse (brighten them) in the morning.

It drives configured lights with two daily ramps:

- a **dim** ramp from the day target down to the night target
- a **brighten** ramp from the night target up to the day target

By default the dim ramp starts at civil dusk and the brighten ramp at civil dawn, taken directly from Home Assistant's astral data for the current date. Either ramp can instead use a fixed clock time, which takes precedence over the civil-sun schedule. A civil-sun schedule can also be bounded by a clock time: brighten at civil dawn but no later than 07:00, or dim at civil dusk but no earlier than 21:00.

Between ramps, DimSome holds the plateau: when a controlled light turns on after the dim ramp it is set to the night target, and after the brighten ramp to the day target. If a light is changed by hand during an active ramp, DimSome stands down for that light until the ramp ends (or sooner, via the resume button or `dimsome.resume`). Saving settings in the panel keeps these overrides; restarting Home Assistant clears them.

<img width="1101" height="392" src="https://github.com/user-attachments/assets/818263b9-fc44-4ae6-b4c0-66ffc9bf7025" alt="DimSome panel overview: today's sun elevation curve with the brighten ramp at 07:08 at civil dawn, and the dim ramp at 19:51, at civil dusk - the blue line is the 'now' line (at ~15:13)"/>

The panel overview plots today's sun elevation with both ramps. Open dots mark civil dawn and dusk, so you can see when a start bound moves a ramp away from the sun event.

## Installation

### HACS

1. In HACS, open the menu (⋮) and choose **Custom repositories**.
2. Add `https://github.com/c-kick/hnl-dimsome` with type **Integration**.
3. Search for **DimSome**, download it, and restart Home Assistant.

### Manual

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
2. Select **Add integration** and search for **DimSome**.
3. Create the entry (DimSome supports a single integration entry).
4. Open the **DimSome** sidebar panel and configure global defaults and the lights to control.

## Configuration

Configuration is done from the DimSome sidebar panel. Per-light settings fall back to the global defaults unless overridden.

### Global settings

- **Dimming** / **Brightening** schedule — civil sun (`civil_dusk` / `civil_dawn`) or a fixed time. A civil-sun dimming schedule takes an optional *start no earlier than* time; a brightening schedule takes an optional *start no later than* time. The per-light brightness profile marks today's civil dawn and dusk so the effect of a bound is visible.
- **Ramp Duration** — how long each transition takes.
- **Override Resume** — how control returns after a manual change: `Manual Only` or `After Grace Period`.
- **Grace Period** — delay before automatic resume (used by `After Grace Period`).
- **Split Brightness & Color Calls** — send brightness and color as separate `light.turn_on` calls, for lights that reject combined updates.
- **Apply On Recovery** — re-apply the current target when a light comes back online while already on.
- **Native Users** — comma-separated Home Assistant user IDs whose light changes are treated as automations rather than manual overrides (useful for Node-RED or other token-based integrations).

<img width="800" alt="Schedule and defaults section: dim at civil dusk but no earlier than 21:00, brighten at civil dawn but no later than 06:40" src="https://github.com/user-attachments/assets/923dd0e7-6a45-4053-810a-6a9c39c35baa" />

### Per-light settings

- **Light Entity** — the light to control.
- **Minimum / Maximum Brightness** — night and day targets, as a percentage from `1` to `100` (converted internally to Home Assistant's `1`–`255` scale).
- **Adjust Color Temperature** — optional minimum/maximum `color_temp_kelvin` targets (color support is intentionally limited to color temperature).
- **Split Brightness & Color Calls**, **Apply On Recovery** — per-light overrides of the global toggles.
- **Settle Delay** — wait after a light turns on before applying its target.
- **Schedule / Ramp Duration / Override Resume / Grace Period overrides** — per-light overrides of the global schedule and resume behavior.

Per-light **enable/pause** is handled by the `DimSome enabled` switch entity.

Each light card shows a 24-hour brightness profile with today's civil dawn and dusk marked. With color temperature configured, the line is tinted by the target color over the day:

<img width="800" alt="Light card with a brightness profile from 40% at night to 80% by day, tinted from warm to cooler white" src="https://github.com/user-attachments/assets/c83b347f-7842-4d6e-8f97-98abb7ec7b1c" />

A light can override the global timing. Here it brightens by 06:15, ahead of civil dawn at 07:08, while still dimming no earlier than 21:00:

<img width="800" alt="Light card with a custom schedule: brighten at civil dawn but no later than 06:15, dim at civil dusk but no earlier than 21:00" src="https://github.com/user-attachments/assets/10df42c6-872d-413f-8c38-a2c135090ece" />


## Entities

- `button.dimsome_resume` — resume DimSome control for all configured lights.
- Per-light resume buttons — resume one light.
- Per-light `DimSome enabled` switches — enable or pause control for one light.
- Per-light diagnostic sensors — expose runtime state via attributes such as `status`, `active_window`, `next_window_start`, `target`, and manual-override state. During an active ramp `next_window_start` points to the *following* ramp; use `active_window` and `target` to confirm DimSome is ramping correctly.

## Service: `dimsome.resume`

Resume DimSome control for all configured lights, or only the listed entities.

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

A civil-sun schedule may add `not_later_than: "HH:MM"` and/or `not_earlier_than: "HH:MM"`. The ramp then starts at the earlier of the civil event and `not_later_than`, and at the later of the civil event and `not_earlier_than`. A bound more than 12 hours away from the civil event refers to the neighbouring day, so `not_earlier_than: "00:30"` on a dusk schedule dims at half past midnight that night rather than being ignored. On a day without the civil event (polar regions), the bound itself is the start. The panel offers `not_later_than` for brightening and `not_earlier_than` for dimming:

```yaml
brighten_schedule: { type: civil_sun, event: civil_dawn, not_later_than: "07:00" }
dim_schedule: { type: civil_sun, event: civil_dusk, not_earlier_than: "21:00" }
```

## Development

```bash
pytest
```

Source lives in `custom_components/dimsome/`; tests in `tests/`. The integration version is in `custom_components/dimsome/const.py`, and Home Assistant manifest metadata in `custom_components/dimsome/manifest.json`.
