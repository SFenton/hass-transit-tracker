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
    CONF_SCHEDULE_ENTITY,
    CONF_HIDDEN_ROUTES_ENTITY,
    CONF_ROUTE_STYLES_ENTITY,
    CONF_ROUTE_NAMES_ENTITY,
)

_LOGGER = logging.getLogger(__name__)


def _find_transit_tracker_devices(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Find ESPHome Transit Tracker devices by looking for schedule_config entities."""
    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    devices: dict[str, dict[str, str]] = {}

    for entity in registry.entities.values():
        if (
            entity.entity_id.endswith("_schedule_config")
            and entity.domain == "text"
        ):
            prefix = entity.entity_id.replace("text.", "").replace(
                "_schedule_config", ""
            )

            # Use device name if available, otherwise prefix
            device_name = prefix.replace("_", " ").title()

            # Look up sibling entities by device_id for accurate entity IDs
            hidden_entity = ""
            styles_entity = ""
            route_names_entity = ""

            if entity.device_id:
                device = dev_reg.async_get(entity.device_id)
                if device and device.name:
                    device_name = device.name

                # Find sibling entities on the same device
                for sibling in registry.entities.values():
                    if sibling.device_id != entity.device_id:
                        continue
                    eid = sibling.entity_id
                    # Match by unique_id suffix or entity_id patterns
                    if "hidden_route" in eid and sibling.domain == "text":
                        hidden_entity = eid
                    elif "route_styles" in eid and sibling.domain == "text":
                        styles_entity = eid
                    elif "route_names" in eid and sibling.domain == "sensor":
                        route_names_entity = eid

            # Fallback to derivation if device lookup didn't find them
            if not hidden_entity:
                hidden_entity = f"text.{prefix}_hidden_routes_config"
            if not styles_entity:
                styles_entity = f"text.{prefix}_route_styles_config"
            if not route_names_entity:
                route_names_entity = f"sensor.{prefix}_route_names"

            _LOGGER.debug(
                "Discovered device %s: schedule=%s, hidden=%s, styles=%s, names=%s",
                device_name, entity.entity_id, hidden_entity, styles_entity,
                route_names_entity,
            )

            devices[prefix] = {
                "name": device_name,
                CONF_SCHEDULE_ENTITY: entity.entity_id,
                CONF_HIDDEN_ROUTES_ENTITY: hidden_entity,
                CONF_ROUTE_STYLES_ENTITY: styles_entity,
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
                    CONF_SCHEDULE_ENTITY: device_info[CONF_SCHEDULE_ENTITY],
                    CONF_HIDDEN_ROUTES_ENTITY: device_info[CONF_HIDDEN_ROUTES_ENTITY],
                    CONF_ROUTE_STYLES_ENTITY: device_info[CONF_ROUTE_STYLES_ENTITY],
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
            schedule_entity = user_input[CONF_SCHEDULE_ENTITY]

            # Validate the entity exists
            state = self.hass.states.get(schedule_entity)
            if state is None:
                errors[CONF_SCHEDULE_ENTITY] = "entity_not_found"
            else:
                # Look up entity in registry to find sibling entities
                ent_reg = er.async_get(self.hass)
                ent_entry = ent_reg.async_get(schedule_entity)

                prefix = (
                    schedule_entity.replace("text.", "").replace(
                        "_schedule_config", ""
                    )
                )

                hidden_entity = ""
                styles_entity = ""
                route_names_entity = ""

                if ent_entry and ent_entry.device_id:
                    for sibling in ent_reg.entities.values():
                        if sibling.device_id != ent_entry.device_id:
                            continue
                        eid = sibling.entity_id
                        if "hidden_route" in eid and sibling.domain == "text":
                            hidden_entity = eid
                        elif "route_styles" in eid and sibling.domain == "text":
                            styles_entity = eid
                        elif "route_names" in eid and sibling.domain == "sensor":
                            route_names_entity = eid

                if not hidden_entity:
                    hidden_entity = f"text.{prefix}_hidden_routes_config"
                if not styles_entity:
                    styles_entity = f"text.{prefix}_route_styles_config"
                if not route_names_entity:
                    route_names_entity = f"sensor.{prefix}_route_names"

                return self.async_create_entry(
                    title=prefix.replace("_", " ").title(),
                    data={
                        CONF_SCHEDULE_ENTITY: schedule_entity,
                        CONF_HIDDEN_ROUTES_ENTITY: hidden_entity,
                        CONF_ROUTE_STYLES_ENTITY: styles_entity,
                        CONF_ROUTE_NAMES_ENTITY: route_names_entity,
                    },
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCHEDULE_ENTITY): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "example": "text.transit_tracker_abcdef_schedule_config"
            },
        )
