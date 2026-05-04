# Dimsome

Custom Home Assistant integration for adaptive light dimming.

## Development

- Source integration path: `custom_components/dimsome/`
- Live Home Assistant container target: `/config/custom_components/dimsome`
- Development deployment should use a Portainer/Docker bind mount from `./custom_components/dimsome` to `/config/custom_components/dimsome:ro`.
- Keep changes minimal until the Node-RED Adaptive Lighting behavior is fully specified.

