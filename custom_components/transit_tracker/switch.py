"""Switch platform for Transit Tracker route visibility."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    CONF_SCHEDULE_ENTITY,
    CONF_HIDDEN_ROUTES_ENTITY,
    CONF_ROUTE_STYLES_ENTITY,
    CONF_ROUTE_NAMES_ENTITY,
)

_LOGGER = logging.getLogger(__name__)


def _parse_schedule_string(schedule: str) -> list[dict[str, str]]:
    """Parse a schedule config string into route-stop pairs.

    Format: routeId,stopId,timeOffsetSeconds;routeId,stopId,timeOffsetSeconds;...
    """
    routes = []
    if not schedule:
        return routes

    for entry in schedule.split(";"):
        parts = entry.strip().split(",")
        if len(parts) >= 2:
            routes.append(
                {
                    "route_id": parts[0].strip(),
                    "stop_id": parts[1].strip(),
                    "time_offset": parts[2].strip() if len(parts) > 2 else "0",
                }
            )
    return routes


def _parse_route_styles(styles: str) -> dict[str, str]:
    """Parse route styles text into a map of route_id -> display name.

    Format: routeId;name;colorHex (one per line)
    """
    names: dict[str, str] = {}
    if not styles:
        return names

    for line in styles.split("\n"):
        parts = line.strip().split(";")
        if len(parts) >= 2:
            names[parts[0].strip()] = parts[1].strip()

    return names


def _parse_hidden_routes(hidden: str) -> set[str]:
    """Parse hidden routes text into a set of route IDs.

    Format: routeId;routeId;...
    """
    if not hidden:
        return set()
    return {r.strip() for r in hidden.split(";") if r.strip()}


def _parse_route_names(names_str: str) -> dict[str, str]:
    """Parse route names text_sensor string into a map of route_id -> display name.

    Format: routeId=routeName;routeId=routeName;...
    """
    names: dict[str, str] = {}
    if not names_str:
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
    schedule_entity_id = config[CONF_SCHEDULE_ENTITY]
    hidden_entity_id = config[CONF_HIDDEN_ROUTES_ENTITY]
    styles_entity_id = config[CONF_ROUTE_STYLES_ENTITY]
    route_names_entity_id = config.get(CONF_ROUTE_NAMES_ENTITY, "")

    coordinator = RouteCoordinator(
        hass, entry, schedule_entity_id, hidden_entity_id, styles_entity_id,
        route_names_entity_id,
    )

    # Do initial setup
    await coordinator.async_initial_setup(async_add_entities)


class RouteCoordinator:
    """Manages route switch entities, creating/removing as the schedule changes."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        schedule_entity_id: str,
        hidden_entity_id: str,
        styles_entity_id: str,
        route_names_entity_id: str,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.schedule_entity_id = schedule_entity_id
        self.hidden_entity_id = hidden_entity_id
        self.styles_entity_id = styles_entity_id
        self.route_names_entity_id = route_names_entity_id
        self._switches: dict[str, TransitRouteSwitch] = {}
        self._async_add_entities: AddEntitiesCallback | None = None

    async def async_initial_setup(
        self, async_add_entities: AddEntitiesCallback
    ) -> None:
        """Perform initial setup: read current state and create switches."""
        self._async_add_entities = async_add_entities

        # Read current states
        schedule_state = self.hass.states.get(self.schedule_entity_id)
        hidden_state = self.hass.states.get(self.hidden_entity_id)
        styles_state = self.hass.states.get(self.styles_entity_id)
        route_names_state = self.hass.states.get(self.route_names_entity_id)

        schedule_str = schedule_state.state if schedule_state else ""
        hidden_str = hidden_state.state if hidden_state else ""
        styles_str = styles_state.state if styles_state else ""
        route_names_str = route_names_state.state if route_names_state else ""

        routes = _parse_schedule_string(schedule_str)
        hidden = _parse_hidden_routes(hidden_str)
        # Prefer route_names text_sensor over route_styles for display names
        route_names = _parse_route_names(route_names_str)
        if not route_names:
            route_names = _parse_route_styles(styles_str)

        # Create initial switches
        switches = []
        for route_info in routes:
            route_id = route_info["route_id"]
            if route_id not in self._switches:
                display_name = route_names.get(route_id, route_id)
                switch = TransitRouteSwitch(
                    coordinator=self,
                    route_id=route_id,
                    stop_id=route_info["stop_id"],
                    display_name=display_name,
                    is_hidden=route_id in hidden,
                    entry_id=self.entry.entry_id,
                )
                self._switches[route_id] = switch
                switches.append(switch)

        if switches:
            async_add_entities(switches)

        # Listen for schedule changes to add/remove routes dynamically
        async_track_state_change_event(
            self.hass,
            [self.schedule_entity_id],
            self._handle_schedule_change,
        )

        # Listen for style changes to update display names
        async_track_state_change_event(
            self.hass,
            [self.styles_entity_id],
            self._handle_styles_change,
        )

        # Listen for route_names text_sensor changes to update display names
        if self.route_names_entity_id:
            async_track_state_change_event(
                self.hass,
                [self.route_names_entity_id],
                self._handle_route_names_change,
            )

    @callback
    def _handle_schedule_change(self, event) -> None:
        """Handle schedule_config entity state changes."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        schedule_str = new_state.state
        routes = _parse_schedule_string(schedule_str)

        # Get current hidden routes
        hidden_state = self.hass.states.get(self.hidden_entity_id)
        hidden = _parse_hidden_routes(
            hidden_state.state if hidden_state else ""
        )

        # Get route names - prefer route_names text_sensor over styles
        route_names_state = self.hass.states.get(self.route_names_entity_id)
        route_names = _parse_route_names(
            route_names_state.state if route_names_state else ""
        )
        if not route_names:
            styles_state = self.hass.states.get(self.styles_entity_id)
            route_names = _parse_route_styles(
                styles_state.state if styles_state else ""
            )

        # Find new routes that need switches
        current_route_ids = {r["route_id"] for r in routes}
        new_switches = []

        for route_info in routes:
            route_id = route_info["route_id"]
            if route_id not in self._switches:
                display_name = route_names.get(route_id, route_id)
                switch = TransitRouteSwitch(
                    coordinator=self,
                    route_id=route_id,
                    stop_id=route_info["stop_id"],
                    display_name=display_name,
                    is_hidden=route_id in hidden,
                    entry_id=self.entry.entry_id,
                )
                self._switches[route_id] = switch
                new_switches.append(switch)

        if new_switches and self._async_add_entities:
            self._async_add_entities(new_switches)

        # Mark removed routes as unavailable
        for route_id, switch in self._switches.items():
            if route_id not in current_route_ids:
                switch.set_available(False)
            else:
                switch.set_available(True)

    @callback
    def _handle_styles_change(self, event) -> None:
        """Handle route_styles_config changes to update display names."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        # Only use styles if route_names is not available
        route_names_state = self.hass.states.get(self.route_names_entity_id)
        if route_names_state and route_names_state.state:
            return  # route_names takes priority

        route_names = _parse_route_styles(new_state.state)
        for route_id, switch in self._switches.items():
            new_name = route_names.get(route_id)
            if new_name:
                switch.update_display_name(new_name)

    @callback
    def _handle_route_names_change(self, event) -> None:
        """Handle route_names text_sensor changes to update display names."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        route_names = _parse_route_names(new_state.state)
        for route_id, switch in self._switches.items():
            new_name = route_names.get(route_id)
            if new_name:
                switch.update_display_name(new_name)

    async def async_update_hidden_routes(self) -> None:
        """Write the current hidden routes to the firmware entity."""
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
        stop_id: str,
        display_name: str,
        is_hidden: bool,
        entry_id: str,
    ) -> None:
        self._coordinator = coordinator
        self._route_id = route_id
        self._stop_id = stop_id
        self._display_name = display_name
        self._is_on = not is_hidden
        self._available = True

        self._attr_unique_id = f"{entry_id}_route_{route_id}"
        self._attr_name = f"Route {display_name}"
        self._attr_icon = "mdi:bus"

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
        self._is_on = False
        self.async_write_ha_state()
        await self._coordinator.async_update_hidden_routes()

    @callback
    def set_available(self, available: bool) -> None:
        """Set the availability of this switch."""
        self._available = available
        self.async_write_ha_state()

    @callback
    def update_display_name(self, name: str) -> None:
        """Update the display name from route styles."""
        self._display_name = name
        self._attr_name = f"Route {name}"
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == "on"
