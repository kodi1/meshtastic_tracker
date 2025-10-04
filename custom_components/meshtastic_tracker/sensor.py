from __future__ import annotations
from typing import Any
from datetime import datetime

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, ATTR_BATTERY_VOLTAGE

# Each sensor: key, name, device_class, unit, state_class
SENSOR_DEFS = [
    (
        "temperature",
        "Temperature",
        SensorDeviceClass.TEMPERATURE,
        "°C",
        SensorStateClass.MEASUREMENT,
    ),
    (
        "relative_humidity",
        "Humidity",
        SensorDeviceClass.HUMIDITY,
        "%",
        SensorStateClass.MEASUREMENT,
    ),
    (
        "barometric_pressure",
        "Pressure",
        SensorDeviceClass.PRESSURE,
        "hPa",
        SensorStateClass.MEASUREMENT,
    ),
    (
        "battery_level",
        "Battery Level",
        SensorDeviceClass.BATTERY,
        "%",
        SensorStateClass.MEASUREMENT,
    ),
    (
        "rx_rssi",
        "RSSI",
        SensorDeviceClass.SIGNAL_STRENGTH,
        "dBm",
        SensorStateClass.MEASUREMENT,
    ),
    (
        "rx_snr",
        "SNR",
        SensorDeviceClass.SIGNAL_STRENGTH,
        "dB",
        SensorStateClass.MEASUREMENT,
    ),
    # text sensor (plain string, not numeric)
    ("text", "text", None, None, None),
]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Meshtastic sensors from config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for node in coordinator.tracked_nodes:
        for key, name, device_class, unit, state_class in SENSOR_DEFS:
            entities.append(
                MeshtasticNodeSensor(
                    coordinator, node, key, name, device_class, unit, state_class
                )
            )
    async_add_entities(entities, True)


class MeshtasticNodeSensor(CoordinatorEntity, RestoreEntity, SensorEntity):
    """Representation of a Meshtastic sensor."""

    def __init__(
        self,
        coordinator,
        node_id: str,
        key: str,
        name: str,
        device_class: SensorDeviceClass | None,
        unit: str | None,
        state_class: SensorStateClass | None,
    ):
        super().__init__(coordinator)
        self.node_id = node_id
        self._key = key
        # store display name passed in definitions (e.g., "Temperature")
        self._display_name = name

        # Home Assistant standard attributes
        self._attr_unique_id = (
            f"meshtastic_{coordinator.entry.entry_id}_{node_id}_{key}"
        )
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_entity_category = None

        # Only numeric sensors get precision
        if state_class is not None:
            self._attr_suggested_display_precision = 1
        else:
            self._attr_suggested_display_precision = None

    @property
    def native_value(self) -> Any | None:
        """Return the current value, rounded if numeric."""
        value = self.coordinator.latest.get(self.node_id, {}).get(self._key)
        if isinstance(value, (int, float)):
            return round(value, 2)
        return value

    @property
    def available(self) -> bool:
        """Return True if data for this node exists."""
        return self.node_id in self.coordinator.latest

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        data = self.coordinator.latest.get(self.node_id, {})
        attrs: dict[str, Any] = {}

        # Include voltage attribute for battery
        if self._key == "battery_level" and ATTR_BATTERY_VOLTAGE in data:
            voltage = data[ATTR_BATTERY_VOLTAGE]
            attrs[ATTR_BATTERY_VOLTAGE] = (
                round(voltage, 2) if isinstance(voltage, (int, float)) else voltage
            )

        # Include timestamp only for "msg" sensor
        if self._key == "text":
            txt_time = data.get("txt_time")
            if isinstance(txt_time, (int, float)):
                dt = datetime.fromtimestamp(txt_time)
                # Example: "October 4, 2025 at 12:34:42"
                attrs["timestamp"] = dt.strftime("%B %-d, %Y at %H:%M:%S")
            # ✅ Include MQTT topic if available
            if "topic" in data:
                attrs["topic"] = data["topic"]
            if "text_to" in data:
                attrs["to"] = data["text_to"]
            if "hops_taken" in data:
                attrs["hops_taken"] = data["hops_taken"]
            if "type" in data:
                attrs["type"] = data.get("type")
        return attrs

    @property
    def name(self) -> str:
        """Return dynamic name that updates when device is renamed."""
        # include node id so each sensor per-node remains unique and readable
        return f"{self.coordinator.friendly_name} {self._display_name}"

    async def async_added_to_hass(self):
        """Handle entity addition and restore previous state."""
        await super().async_added_to_hass()

        # Restore last state if available
        if (last_state := await self.async_get_last_state()) is not None:
            if self.node_id not in self.coordinator.latest:
                self.coordinator.latest[self.node_id] = {}
            if self._key not in self.coordinator.latest[self.node_id]:
                try:
                    # attempt numeric restore
                    value = (
                        float(last_state.state)
                        if last_state.state not in (None, "unknown", "unavailable")
                        else None
                    )
                except ValueError:
                    # text fallback
                    value = last_state.state
                self.coordinator.latest[self.node_id][self._key] = value
                self.async_write_ha_state()
