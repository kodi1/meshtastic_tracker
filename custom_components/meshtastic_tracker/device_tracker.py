"""Device Tracker platform for Meshtastic Tracker integration."""

import logging
from datetime import datetime
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Meshtastic device trackers based on the coordinator."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        MeshtasticDeviceTracker(coordinator, node_id)
        for node_id in coordinator.tracked_nodes or []
    ]

    if not entities:
        _LOGGER.warning(
            "No tracked nodes defined for Meshtastic Tracker '%s'",
            coordinator.friendly_name,
        )

    async_add_entities(entities)


class MeshtasticDeviceTracker(CoordinatorEntity, TrackerEntity, RestoreEntity):
    """Representation of a Meshtastic node as a device tracker."""

    _attr_icon = "mdi:radio-tower"
    _attr_should_poll = False

    def __init__(self, coordinator, node_id):
        """Initialize the device tracker entity."""
        super().__init__(coordinator)
        self.node_id = node_id

        # Unique ID and friendly name
        self._attr_unique_id = (
            f"meshtastic_tracker_{coordinator.entry.entry_id}_{node_id}"
        )

        # Device metadata
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node_id)},
            name=f"{coordinator.friendly_name} {node_id}",
            manufacturer="Meshtastic",
            model="Mesh Node",
        )

        _LOGGER.debug("Created device tracker for node %s", node_id)

    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Return dynamic tracker name reflecting current friendly name and node."""
        return f"{self.coordinator.friendly_name}"

    @property
    def latitude(self):
        """Return the current latitude."""
        return self._get_node_value(ATTR_LATITUDE)

    @property
    def longitude(self):
        """Return the current longitude."""
        return self._get_node_value(ATTR_LONGITUDE)

    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        data = self.coordinator.latest.get(self.node_id, {})
        rx_time = data.get("rx_time")
        if isinstance(rx_time, (int, float)):
            dt = datetime.fromtimestamp(rx_time)
            # Example: "October 4, 2025 at 12:34:42"
            rx_time = dt.strftime("%B %-d, %Y at %H:%M:%S")
        prec_bits = data.get("precision_bits", 13)
        return {
            "sender": data.get("sender"),
            "rx_time": rx_time,
            "altitude": data.get("altitude"),
            "ground_speed": data.get("ground_speed"),
            "sats_in_view": data.get("sats_in_view"),
            "PDOP": data.get("PDOP"),
            "ground_track": data.get("ground_track"),
            "gps_accuracy": 127420 * (180 / (2 ** prec_bits)),
        }

    # ------------------------------------------------------------------

    async def async_added_to_hass(self):
        """Restore last known location and ensure coordinator listener is registered."""
        await super().async_added_to_hass()

        # Restore last location if available
        if (last_state := await self.async_get_last_state()) is not None:
            data = self.coordinator.latest.setdefault(self.node_id, {})
            if ATTR_LATITUDE not in data or ATTR_LONGITUDE not in data:
                try:
                    lat = float(last_state.attributes.get(ATTR_LATITUDE))
                    lon = float(last_state.attributes.get(ATTR_LONGITUDE))
                except (TypeError, ValueError):
                    lat = lon = None

                if lat is not None and lon is not None:
                    data[ATTR_LATITUDE] = lat
                    data[ATTR_LONGITUDE] = lon
                    _LOGGER.debug(
                        "Restored previous location for %s: lat=%.6f lon=%.6f",
                        self.node_id,
                        lat,
                        lon,
                    )
                    self.async_write_ha_state()

        # ✅ Ensure tracker is always registered for coordinator updates
        remove_listener = self.coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        self.async_on_remove(remove_listener)
        _LOGGER.debug("Tracker %s registered listener with coordinator", self.node_id)

    # ------------------------------------------------------------------

    def _get_node_value(self, key):
        """Helper to safely extract a coordinate value."""
        return self.coordinator.latest.get(self.node_id, {}).get(key)

    def _handle_coordinator_update(self):
        """Handle updated data from the coordinator with PDOP filtering (min/max)."""
        data = self.coordinator.latest.get(self.node_id, {})
        lat = data.get("latitude_i")
        lon = data.get("longitude_i")
        pdop = data.get("PDOP")

        # Convert scaled ints if needed
        if isinstance(lat, int) and isinstance(lon, int):
            lat /= 1e7
            lon /= 1e7

        # Load thresholds from coordinator (with defaults)
        pdop_min = getattr(self.coordinator, "pdop_min_threshold", 0.5)
        pdop_max = getattr(self.coordinator, "pdop_max_threshold", 8.0)

        update_position = True
        if pdop is not None:
            try:
                pdop_val = float(pdop)
                if pdop_val < pdop_min or pdop_val > pdop_max:
                    update_position = False
                    _LOGGER.debug(
                        "Skipping location update for %s due to PDOP out of range (%.2f not in [%.2f–%.2f])",
                        self.node_id,
                        pdop_val,
                        pdop_min,
                        pdop_max,
                    )
            except (ValueError, TypeError):
                pass

        # Update lat/lon only if PDOP is acceptable
        if update_position and lat is not None and lon is not None:
            data[ATTR_LATITUDE] = lat
            data[ATTR_LONGITUDE] = lon

        # Always refresh attributes even if position is skipped
        self.async_write_ha_state()
