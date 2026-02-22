"""Config flow for Transit Tracker integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    CONF_HIDDEN_ROUTES_ENTITY,
    CONF_ROUTE_NAMES_ENTITY,
)

_LOGGER = logging.getLogger(__name__)


def _find_transit_tracker_devices(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Find ESPHome Transit Tracker devices by looking for known entity patterns.

    Most config text entities (schedule_config, route_styles_config) are internal
    in ESPHome and not exposed to HA. We discover devices by looking for entities
    that ARE exposed: hidden_routes (text) and route_names (sensor).
    """
    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    devices: dict[str, dict[str, str]] = {}

    # First pass: find devices by any recognizable Transit Tracker entity
    device_ids: dict[str, str] = {}  # device_id -> prefix
    for entity in registry.entities.values():
        eid = entity.entity_id
        # Look for hidden_routes text entity or route_names sensor
        if (
            ("hidden_route" in eid and entity.domain == "text")
            or ("route_names" in eid and entity.domain == "sensor")
            or (eid.endswith("_schedule_config") and entity.domain == "text")
        ):
            if entity.device_id and entity.device_id not in device_ids:
                # Derive a prefix for display
                name_part = eid.split(".", 1)[1] if "." in eid else eid
                # Strip known suffixes to get the device prefix
                for suffix in (
                    "_hidden_routes_config", "_hidden_routes",
                    "_route_names", "_schedule_config",
                ):
                    if name_part.endswith(suffix):
                        name_part = name_part[: -len(suffix)]
                        break
                device_ids[entity.device_id] = name_part

    # Second pass: for each device, find all relevant sibling entities
    for device_id, prefix in device_ids.items():
        device = dev_reg.async_get(device_id)
        device_name = device.name if device and device.name else prefix.replace("_", " ").title()

        hidden_entity = ""
        route_names_entity = ""

        for entity in registry.entities.values():
            if entity.device_id != device_id:
                continue
            eid = entity.entity_id
            if "hidden_route" in eid and entity.domain == "text":
                hidden_entity = eid
            elif "route_names" in eid and entity.domain == "sensor":
                route_names_entity = eid

        if not route_names_entity and not hidden_entity:
            continue  # Need at least one usable entity

        _LOGGER.debug(
            "Discovered device %s: hidden=%s, route_names=%s",
            device_name, hidden_entity, route_names_entity,
        )

        devices[prefix] = {
            "name": device_name,
            CONF_HIDDEN_ROUTES_ENTITY: hidden_entity,
            CONF_ROUTE_NAMES_ENTITY: route_names_entity,
        }

    return devices


class TransitTrackerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Transit Tracker."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step."""
        # Try auto-discovery (called directly, not via executor)
        devices = _find_transit_tracker_devices(self.hass)

        if devices:
            return await self.async_step_select_device(devices=devices)

        # Fall back to manual entry
        return await self.async_step_manual()

    async def async_step_select_device(
        self,
        user_input: dict[str, Any] | None = None,
        devices: dict[str, dict[str, str]] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Let the user select a discovered device."""
        if devices is None:
            devices = _find_transit_tracker_devices(self.hass)

        if user_input is not None:
            selected = user_input["device"]
            device_info = devices[selected]
            return self.async_create_entry(
                title=device_info["name"],
                data={
                    CONF_HIDDEN_ROUTES_ENTITY: device_info[CONF_HIDDEN_ROUTES_ENTITY],
                    CONF_ROUTE_NAMES_ENTITY: device_info[CONF_ROUTE_NAMES_ENTITY],
                },
            )

        device_options = {
            prefix: info["name"] for prefix, info in devices.items()
        }

        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema(
                {vol.Required("device"): vol.In(device_options)}
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle manual entity ID entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            route_names_entity = user_input[CONF_ROUTE_NAMES_ENTITY]

            # Validate the entity exists
            state = self.hass.states.get(route_names_entity)
            if state is None:
                errors[CONF_ROUTE_NAMES_ENTITY] = "entity_not_found"
            else:
                # Try to find hidden_routes entity on same device
                ent_reg = er.async_get(self.hass)
                ent_entry = ent_reg.async_get(route_names_entity)

                hidden_entity = ""
                if ent_entry and ent_entry.device_id:
                    for sibling in ent_reg.entities.values():
                        if sibling.device_id != ent_entry.device_id:
                            continue
                        if "hidden_route" in sibling.entity_id and sibling.domain == "text":
                            hidden_entity = sibling.entity_id
                            break

                # Derive name from entity ID
                name_part = route_names_entity.split(".", 1)[1] if "." in route_names_entity else route_names_entity
                for suffix in ("_route_names",):
                    if name_part.endswith(suffix):
                        name_part = name_part[: -len(suffix)]
                        break

                return self.async_create_entry(
                    title=name_part.replace("_", " ").title(),
                    data={
                        CONF_HIDDEN_ROUTES_ENTITY: hidden_entity,
                        CONF_ROUTE_NAMES_ENTITY: route_names_entity,
                    },
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROUTE_NAMES_ENTITY): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "example": "sensor.transit_tracker_2783a0_route_names"
            },
        )
