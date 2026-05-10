# Dimsome

Custom Home Assistant integration for adaptive light dimming.

## Development

- Source integration path: `custom_components/dimsome/`
- Live Home Assistant container target: `/config/custom_components/dimsome`
- Development deployment should use a Portainer/Docker bind mount from `./custom_components/dimsome` to `/config/custom_components/dimsome:ro`.
- Keep changes minimal and preserve the deterministic Dimsome behavior contract below.

## Behavior Contract

Dimsome is a simple two-ramp controller:

- Dim at civil dusk by default.
- Brighten at civil dawn by default.
- A configured fixed time for dimming completely overrides civil dusk; dimming always starts at that time.
- A configured fixed time for brightening completely overrides civil dawn; brightening always starts at that time.
- If a light is manually touched during a dim or brighten ramp, Dimsome must not touch that light again for the remainder of that same ramp.
- If a light turns on after the end of a dusk ramp and before the start of the next dawn/brighten ramp, Dimsome must apply the low/night target.
- If a light turns on after the end of a dawn/brighten ramp and before the start of the next dusk/dim ramp, Dimsome must apply the high/day target.
- Civil-sun schedules must be deterministic from current Home Assistant `sun.sun` state and configured overrides; do not rely only on catching a single sun elevation event.
- During an active ramp, `next_window_start` pointing to the following ramp is expected; use `active_window` and `target` to determine whether Dimsome is currently ramping correctly.

