"""Transit Tracker integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, CONF_DEVICE_ID

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Transit Tracker from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    device_id = entry.data.get(CONF_DEVICE_ID, "")
    device_identifiers: set[tuple[str, str]] | None = None

    if device_id:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(device_id)
        if device and device.identifiers:
            device_identifiers = set(device.identifiers)
            _LOGGER.debug(
                "Linking config entry %s to device %s (identifiers=%s)",
                entry.entry_id, device.name, device_identifiers,
            )
            # Register our config entry with the existing ESPHome device.
            # This is required for our entities' device_info to link correctly.
            dev_reg.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers=device_identifiers,
            )
        else:
            _LOGGER.warning(
                "Device ID %s not found or has no identifiers", device_id
            )

    hass.data[DOMAIN][entry.entry_id] = {
        **entry.data,
        "device_identifiers": device_identifiers,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
