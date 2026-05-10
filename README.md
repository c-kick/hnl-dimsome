# Dimsome

Dimsome is a small custom Home Assistant integration for adaptive light dimming.

The name is a play on dimming and dim sum. The first implementation will be based on the existing Node-RED Adaptive Lighting flow once its behavior is mapped into this integration.

## Development Install

This repository is intended to be bind-mounted into Home Assistant, matching the Juice Patrol development setup:

```bash
./custom_components/dimsome -> /config/custom_components/dimsome:ro
```

Because Home Assistant loads custom integrations from `/config/custom_components`, source edits here are immediately present in the Home Assistant container. Python integration changes still require reloading the integration or restarting Home Assistant depending on the changed module.

## Current State

The integration currently provides the first runtime implementation:

- typed global defaults with per-light overrides
- fixed-time dim/brighten ramps
- civil dawn/dusk ramps from Home Assistant `sun.sun`
- per-light min/max brightness in percent
- optional `color_temp_kelvin` interpolation
- manual override stand-down with tolerance for Dimsome's own updates
- optional per-light grace-period resume
- `button.dimsome_resume` and `dimsome.resume` service
- split `light.turn_on` calls for lights that cannot apply brightness and color together
- persistent Dimsome sidebar panel for global defaults plus add/edit/remove light configuration

Create Dimsome from Home Assistant: Settings -> Devices & services -> Add integration -> Dimsome. Then manage lights and schedules from the Dimsome sidebar panel. YAML import is still supported for development/migration, but it is no longer the primary setup path.

## Behavior Contract

Dimsome is intended to behave as a deterministic two-ramp controller:

- Dimming starts at civil dusk unless a fixed dim time is configured.
- Brightening starts at civil dawn unless a fixed brighten time is configured.
- A fixed dim time completely ignores civil dusk for dimming.
- A fixed brighten time completely ignores civil dawn for brightening.
- During a dim ramp, lights move from max/day target to min/night target.
- During a brighten ramp, lights move from min/night target to max/day target.
- After the dusk ramp ends and before the next dawn/brighten ramp starts, lights that turn on are set to the low/night target.
- After the dawn/brighten ramp ends and before the next dusk/dim ramp starts, lights that turn on are set to the high/day target.
- If a light is manually touched during a ramp, Dimsome stands down for that light until the next ramp window.

Civil dawn/dusk uses the `sun.sun` elevation crossing of `-6.0` degrees, plus Home Assistant's `next_dawn` and `next_dusk` attributes to avoid missed-event or restart timing failures.

## GUI

The setup flow only creates the integration entry. The Dimsome sidebar panel manages:

- Global dimming and brightening defaults
- One or more controlled lights
- Optional per-light overrides for schedules, ramp duration, and manual override behavior
- Resume controls for all lights or a single light

## YAML Example

YAML remains available as an import path, but normal configuration should use the GUI.

```yaml
dimsome:
  global:
    ramp_duration: "01:00:00"
    override_resume_mode: manual_only
    dim_schedule:
      type: civil_sun
      event: civil_dusk
    brighten_schedule:
      type: fixed_time
      at: "06:30"
  lights:
    - entity_id: light.living_room
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

## Notes

- Brightness config is percent-based (`1` to `100`) and converted to Home Assistant's `1` to `255` brightness scale internally.
- Color support intentionally starts with `color_temp_kelvin` only. Other color modes need explicit support instead of ambiguous "color" handling.
- Civil dawn/dusk is based on `sun.sun` elevation crossing `-6.0`, refreshed periodically and reconstructed from `sun.sun` next-event attributes.
- Manual overrides are treated as any external state change during a ramp. Home Assistant does not reliably distinguish a human from an automation, so automations should call `dimsome.resume` when they want to hand control back.
