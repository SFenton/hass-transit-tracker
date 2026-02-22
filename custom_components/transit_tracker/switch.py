"""Switch platform for Transit Tracker route visibility."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    CONF_HIDDEN_ROUTES_ENTITY,
    CONF_ROUTE_NAMES_ENTITY,
    CONF_DEVICE_ID,
)

_LOGGER = logging.getLogger(__name__)


def _parse_hidden_routes(hidden: str) -> set[str]:
    """Parse hidden routes text into a set of route IDs.

    Format: routeId;routeId;...
    """
    if not hidden or hidden in ("unknown", "unavailable"):
        return set()
    return {r.strip() for r in hidden.split(";") if r.strip()}


def _parse_route_names(names_str: str) -> dict[str, str]:
    """Parse route names text_sensor string into a map of route_id -> display name.

    Format: routeId=routeName;routeId=routeName;...
    """
    names: dict[str, str] = {}
    if not names_str or names_str in ("unknown", "unavailable"):
        return names

    for entry in names_str.split(";"):
        entry = entry.strip()
        if "=" in entry:
            route_id, route_name = entry.split("=", 1)
            names[route_id.strip()] = route_name.strip()

    return names


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Transit Tracker route switches from a config entry."""
    config = hass.data[DOMAIN][entry.entry_id]
    hidden_entity_id = config.get(CONF_HIDDEN_ROUTES_ENTITY, "")
    route_names_entity_id = config.get(CONF_ROUTE_NAMES_ENTITY, "")
    device_id = config.get(CONF_DEVICE_ID, "")

    coordinator = RouteCoordinator(
        hass, entry, hidden_entity_id, route_names_entity_id, device_id,
    )

    # Do initial setup
    await coordinator.async_initial_setup(async_add_entities)


class RouteCoordinator:
    """Manages route switch entities, creating/removing as routes change."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        hidden_entity_id: str,
        route_names_entity_id: str,
        device_id: str,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.hidden_entity_id = hidden_entity_id
        self.route_names_entity_id = route_names_entity_id
        self.device_id = device_id
        self._switches: dict[str, TransitRouteSwitch] = {}
        self._async_add_entities: AddEntitiesCallback | None = None

    async def async_initial_setup(
        self, async_add_entities: AddEntitiesCallback
    ) -> None:
        """Perform initial setup: read current state and create switches."""
        self._async_add_entities = async_add_entities

        # Read current states
        hidden_state = self.hass.states.get(self.hidden_entity_id)
        route_names_state = self.hass.states.get(self.route_names_entity_id)

        _LOGGER.debug(
            "Initial states - hidden_entity=%s (%s), route_names_entity=%s (%s)",
            self.hidden_entity_id,
            hidden_state,
            self.route_names_entity_id,
            route_names_state,
        )

        hidden_str = hidden_state.state if hidden_state else ""
        route_names_str = route_names_state.state if route_names_state else ""

        hidden = _parse_hidden_routes(hidden_str)
        route_names = _parse_route_names(route_names_str)

        _LOGGER.debug("Parsed %d routes from route_names: %s", len(route_names), route_names)

        # Create switches from route_names (routeId -> displayName)
        self._create_switches_from_routes(route_names, hidden)

        # Listen for route_names changes (routes appearing/disappearing)
        if self.route_names_entity_id:
            async_track_state_change_event(
                self.hass,
                [self.route_names_entity_id],
                self._handle_route_names_change,
            )

        # Listen for hidden_routes changes (external visibility changes)
        if self.hidden_entity_id:
            async_track_state_change_event(
                self.hass,
                [self.hidden_entity_id],
                self._handle_hidden_change,
            )

    def _create_switches_from_routes(
        self, route_names: dict[str, str], hidden: set[str]
    ) -> None:
        """Create switch entities for routes that don't have one yet."""
        new_switches = []
        for route_id, display_name in route_names.items():
            if route_id not in self._switches:
                switch = TransitRouteSwitch(
                    coordinator=self,
                    route_id=route_id,
                    display_name=display_name,
                    is_hidden=route_id in hidden,
                    entry_id=self.entry.entry_id,
                    device_id=self.device_id,
                )
                self._switches[route_id] = switch
                new_switches.append(switch)
            else:
                # Update display name if changed
                self._switches[route_id].update_display_name(display_name)

        if new_switches and self._async_add_entities:
            _LOGGER.debug("Adding %d new route switches", len(new_switches))
            self._async_add_entities(new_switches)

        # Mark routes not in current route_names as unavailable
        current_route_ids = set(route_names.keys())
        for route_id, switch in self._switches.items():
            if route_id not in current_route_ids:
                switch.set_available(False)
            else:
                switch.set_available(True)

    @callback
    def _handle_route_names_change(self, event) -> None:
        """Handle route_names sensor state changes — create/update switches."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        route_names = _parse_route_names(new_state.state)
        if not route_names:
            return

        _LOGGER.debug("Route names updated: %s", route_names)

        # Get current hidden routes
        hidden_state = self.hass.states.get(self.hidden_entity_id)
        hidden = _parse_hidden_routes(
            hidden_state.state if hidden_state else ""
        )

        self._create_switches_from_routes(route_names, hidden)

    @callback
    def _handle_hidden_change(self, event) -> None:
        """Handle hidden_routes entity changes from external source."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        hidden = _parse_hidden_routes(new_state.state)
        for route_id, switch in self._switches.items():
            should_be_on = route_id not in hidden
            if switch.is_on != should_be_on:
                switch.set_visibility(should_be_on)

    def count_visible_routes(self) -> int:
        """Return the number of currently visible (on) routes."""
        return sum(1 for s in self._switches.values() if s.is_on and s.available)

    async def async_update_hidden_routes(self) -> None:
        """Write the current hidden routes to the firmware entity."""
        if not self.hidden_entity_id:
            _LOGGER.warning("No hidden_routes entity configured, cannot update")
            return

        hidden_ids = [
            route_id
            for route_id, switch in self._switches.items()
            if not switch.is_on
        ]
        hidden_str = ";".join(hidden_ids)

        _LOGGER.debug("Updating hidden routes: %s", hidden_str)

        await self.hass.services.async_call(
            "text",
            "set_value",
            {
                "entity_id": self.hidden_entity_id,
                "value": hidden_str,
            },
        )


class TransitRouteSwitch(SwitchEntity, RestoreEntity):
    """Switch entity representing a transit route's visibility."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RouteCoordinator,
        route_id: str,
        display_name: str,
        is_hidden: bool,
        entry_id: str,
        device_id: str,
    ) -> None:
        self._coordinator = coordinator
        self._route_id = route_id
        self._display_name = display_name
        self._is_on = not is_hidden
        self._available = True
        self._device_id = device_id

        self._attr_unique_id = f"{entry_id}_route_{route_id}"
        self._attr_name = f"Route {display_name}"
        self._attr_icon = "mdi:bus"

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info to link this switch to the ESPHome device."""
        if not self._device_id:
            return None
        from homeassistant.helpers import device_registry as dr
        dev_reg = dr.async_get(self._coordinator.hass)
        device = dev_reg.async_get(self._device_id)
        if device and device.identifiers:
            return DeviceInfo(
                identifiers=device.identifiers,
            )
        return None

    @property
    def is_on(self) -> bool:
        """Return true if the route is visible (not hidden)."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return true if the route is in the current schedule."""
        return self._available

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Show this route on the display."""
        self._is_on = True
        self.async_write_ha_state()
        await self._coordinator.async_update_hidden_routes()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hide this route from the display."""
        # Enforce at least one route must remain visible
        if self._coordinator.count_visible_routes() <= 1:
            _LOGGER.warning(
                "Cannot hide route %s — at least one route must remain visible",
                self._route_id,
            )
            return
        self._is_on = False
        self.async_write_ha_state()
        await self._coordinator.async_update_hidden_routes()

    @callback
    def set_available(self, available: bool) -> None:
        """Set the availability of this switch."""
        if self._available != available:
            self._available = available
            self.async_write_ha_state()

    @callback
    def set_visibility(self, visible: bool) -> None:
        """Set visibility from external hidden_routes change."""
        if self._is_on != visible:
            self._is_on = visible
            self.async_write_ha_state()

    @callback
    def update_display_name(self, name: str) -> None:
        """Update the display name."""
        if self._display_name != name:
            self._display_name = name
            self._attr_name = f"Route {name}"
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == "on"
