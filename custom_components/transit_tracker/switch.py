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
    CONF_HIDDEN_ROUTES_ENTITY,
    CONF_ROUTE_NAMES_ENTITY,
)


def _parse_route_entry(value: str) -> tuple[str, str]:
    """Parse a route value that may contain a pipe-separated headsign.

    Input:  'routeName|headsign'  or  'routeName'
    Output: (route_name, headsign)
    """
    if "|" in value:
        name, headsign = value.split("|", 1)
        return name.strip(), headsign.strip()
    return value.strip(), ""

_LOGGER = logging.getLogger(__name__)


def _parse_hidden_routes(hidden: str) -> set[str]:
    """Parse hidden routes text into a set of composite keys.

    Format: compositeKey;compositeKey;...
    Where compositeKey is routeId:headsign[:stopId]
    """
    if not hidden or hidden in ("unknown", "unavailable"):
        return set()
    return {r.strip() for r in hidden.split(";") if r.strip()}


def _parse_route_names(names_str: str) -> dict[str, tuple[str, str]]:
    """Parse route names text_sensor string (legacy single-string format).

    Format: compositeKey=routeName|headsign;...

    Returns: dict of composite_key -> (route_name, headsign)
    """
    names: dict[str, tuple[str, str]] = {}
    if not names_str or names_str in ("unknown", "unavailable"):
        return names

    for entry in names_str.split(";"):
        entry = entry.strip()
        if "=" in entry:
            composite_key, value = entry.split("=", 1)
            composite_key = composite_key.strip()
            route_name, headsign = _parse_route_entry(value)
            names[composite_key] = (route_name, headsign)

    return names


def _parse_single_route(state_str: str) -> tuple[str, str, str] | None:
    """Parse a single route update from the text_sensor.

    Format: compositeKey=routeName|headsign
    Returns: (composite_key, route_name, headsign) or None
    """
    if not state_str or state_str in ("unknown", "unavailable"):
        return None

    if "=" not in state_str:
        return None

    composite_key, value = state_str.split("=", 1)
    composite_key = composite_key.strip()
    route_name, headsign = _parse_route_entry(value)

    return composite_key, route_name, headsign


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Transit Tracker route switches from a config entry."""
    config = hass.data[DOMAIN][entry.entry_id]
    hidden_entity_id = config.get(CONF_HIDDEN_ROUTES_ENTITY, "")
    route_names_entity_id = config.get(CONF_ROUTE_NAMES_ENTITY, "")

    _LOGGER.debug(
        "Setting up switches: hidden=%s, route_names=%s",
        hidden_entity_id, route_names_entity_id,
    )

    coordinator = RouteCoordinator(
        hass, entry, hidden_entity_id, route_names_entity_id,
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
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.hidden_entity_id = hidden_entity_id
        self.route_names_entity_id = route_names_entity_id
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

        # Try single-route format first
        parsed = _parse_single_route(route_names_str)
        if parsed is not None:
            composite_key, route_name, headsign = parsed
            self._upsert_switch(composite_key, route_name, headsign, hidden)
        else:
            # Legacy multi-route format
            route_names = _parse_route_names(route_names_str)
            _LOGGER.debug(
                "Parsed %d routes from route_names: %s",
                len(route_names), route_names,
            )
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
        self, route_names: dict[str, tuple[str, str]], hidden: set[str]
    ) -> None:
        """Create switch entities for routes that don't have one yet."""
        new_switches = []
        for composite_key, (route_name, headsign) in route_names.items():
            if composite_key not in self._switches:
                switch = TransitRouteSwitch(
                    coordinator=self,
                    composite_key=composite_key,
                    route_name=route_name,
                    headsign=headsign,
                    is_hidden=composite_key in hidden,
                    entry_id=self.entry.entry_id,
                )
                self._switches[composite_key] = switch
                new_switches.append(switch)
            else:
                # Update display name if changed
                self._switches[composite_key].update_display_name(route_name, headsign)

        if new_switches and self._async_add_entities:
            _LOGGER.debug("Adding %d new route switches", len(new_switches))
            self._async_add_entities(new_switches)

        # Mark routes not in current route_names as unavailable
        current_keys = set(route_names.keys())
        for key, switch in self._switches.items():
            if key not in current_keys:
                switch.set_available(False)
            else:
                switch.set_available(True)

    def _upsert_switch(
        self,
        composite_key: str,
        route_name: str,
        headsign: str,
        hidden: set[str],
    ) -> None:
        """Create or update a single switch for a route."""
        if composite_key in self._switches:
            self._switches[composite_key].update_display_name(route_name, headsign)
            self._switches[composite_key].set_available(True)
        else:
            switch = TransitRouteSwitch(
                coordinator=self,
                composite_key=composite_key,
                route_name=route_name,
                headsign=headsign,
                is_hidden=composite_key in hidden,
                entry_id=self.entry.entry_id,
            )
            self._switches[composite_key] = switch
            if self._async_add_entities:
                _LOGGER.debug("Adding route switch: %s", composite_key)
                self._async_add_entities([switch])

    @callback
    def _handle_route_names_change(self, event) -> None:
        """Handle route_names sensor state changes — create/update switches."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return

        state_str = new_state.state

        # Single-route format: compositeKey=routeName|headsign
        parsed = _parse_single_route(state_str)
        if parsed is not None:
            composite_key, route_name, headsign = parsed
            hidden_state = self.hass.states.get(self.hidden_entity_id)
            hidden = _parse_hidden_routes(
                hidden_state.state if hidden_state else ""
            )
            self._upsert_switch(composite_key, route_name, headsign, hidden)
            return

        # Legacy multi-route format fallback
        route_names = _parse_route_names(state_str)
        if not route_names:
            return

        _LOGGER.debug("Route names updated (legacy): %s", route_names)

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
        for composite_key, switch in self._switches.items():
            should_be_on = composite_key not in hidden
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
            composite_key
            for composite_key, switch in self._switches.items()
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
        composite_key: str,
        route_name: str,
        headsign: str,
        is_hidden: bool,
        entry_id: str,
    ) -> None:
        self._coordinator = coordinator
        self._composite_key = composite_key
        self._route_name = route_name
        self._headsign = headsign
        self._is_on = not is_hidden
        self._available = True

        # Build a slug-safe unique_id from the composite key
        slug = composite_key.replace(":", "_").replace(" ", "_").lower()
        self._attr_unique_id = f"{entry_id}_route_{slug}"
        self._update_name()
        self._attr_icon = "mdi:bus"

    def _update_name(self) -> None:
        """Set the entity name from route_name and headsign."""
        if self._headsign:
            self._attr_name = f"{self._route_name} - {self._headsign}"
        else:
            self._attr_name = self._route_name

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
                self._composite_key,
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
    def update_display_name(self, route_name: str, headsign: str) -> None:
        """Update the display name."""
        if self._route_name != route_name or self._headsign != headsign:
            self._route_name = route_name
            self._headsign = headsign
            self._update_name()
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._is_on = last_state.state == "on"
