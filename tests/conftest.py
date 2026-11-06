"""Shared test setup."""

# Home Assistant aliases voluptuous to Probatio on import, as it does in
# production. That only works if it happens before anything imports voluptuous.
import homeassistant  # noqa: F401
