"""Coordinator for Meshtastic Tracker integration."""

import asyncio
import logging
import json
from typing import Any, Optional

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.components import mqtt
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, CONF_FRIENDLY_NAME, CONF_PDOP_MIN_THRESHOLD, CONF_PDOP_MAX_THRESHOLD
from . import pb_data

_LOGGER = logging.getLogger(__name__)


class MeshtasticTrackerCoordinator(DataUpdateCoordinator):
    """Coordinator for handling MQTT messages and Meshtastic node updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        base_topic: str,
        channel_name: str,
        tracked_nodes: list[str] | None = None,
        friendly_name: str | None = None,
        encryption_key: Optional[str] = None,
        debounce_ms: int = 100,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.hass = hass
        self.entry = entry
        self.mqtt_topic = base_topic
        self.tracked_nodes = [str(n).strip() for n in (tracked_nodes or []) if n]
        self.friendly_name = friendly_name or "Meshtastic Tracker"
        self.pdop_min_threshold = float(entry.options.get(CONF_PDOP_MIN_THRESHOLD, 0.5))
        self.pdop_max_threshold = float(entry.options.get(CONF_PDOP_MAX_THRESHOLD, 8.0))
        self.base_topic = base_topic
        self.channel_name = channel_name
        self.encryption_key = encryption_key
        self._mqtt_unsub_proto = None
        self._mqtt_unsub_json = None
        self.latest: dict[str, Any] = {}
        self._debounce_task: asyncio.Task | None = None
        self._debounce_delay = debounce_ms / 1000.0  # convert to seconds

        _LOGGER.debug(
            "Coordinator initialized: topic=%s, nodes=%s, friendly_name=%s",
            self.mqtt_topic,
            self.tracked_nodes,
            self.friendly_name,
        )

    # ------------------------------------------------------------------
    async def async_update_friendly_name(self, new_name: str):
        """Update, persist, and notify listeners when friendly name changes."""
        if self.friendly_name != new_name:
            self.friendly_name = new_name
            _LOGGER.debug("Friendly name updated to: %s", new_name)

            # Persist new name
            new_options = {**self.entry.options, CONF_FRIENDLY_NAME: new_name}
            self.hass.config_entries.async_update_entry(self.entry, options=new_options)

            # Notify all listeners (sensors, trackers)
            self.async_update_listeners()

    # ------------------------------------------------------------------
    async def async_start(self) -> None:
        """Subscribe to MQTT topic when MQTT is ready."""
        for _ in range(10):
            if "mqtt" in self.hass.data:
                break
            await asyncio.sleep(5)
        else:
            raise UpdateFailed("MQTT not initialized after waiting 50s")

        mqtt_proto = f"{self.base_topic}/2/e/{self.channel_name}/#"
        mqtt_json = f"{self.base_topic}/2/json/#"

        try:
            _LOGGER.debug("Subscribing to MQTT proto topic: %s", mqtt_proto)
            self._mqtt_unsub_proto = await mqtt.async_subscribe(
                self.hass,
                mqtt_proto,
                self._mqtt_message_callback,
                qos=0,
                encoding=None,
            )
            _LOGGER.info("Subscribed to MQTT proto topic: %s", mqtt_proto)

            _LOGGER.debug("Subscribing to MQTT json topic: %s", mqtt_json)
            self._mqtt_unsub_json = await mqtt.async_subscribe(
                self.hass,
                mqtt_json,
                self._mqtt_message_callback_json,
                qos=0,
            )
            _LOGGER.info("Subscribed to MQTT json topic: %s", mqtt_json)
        except Exception as exc:
            _LOGGER.exception("Failed to subscribe to MQTT topic: %s", exc)
            raise UpdateFailed(f"MQTT subscribe failed: {exc}") from exc

    # ------------------------------------------------------------------
    async def async_stop(self) -> None:
        """Unsubscribe from MQTT."""
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        if self._mqtt_unsub_json:
            self._mqtt_unsub_json()
            self._mqtt_unsub_json = None

        if self._mqtt_unsub_proto:
            self._mqtt_unsub_proto()
            self._mqtt_unsub_proto = None

        _LOGGER.info("Meshtastic Tracker stopped")

    # ------------------------------------------------------------------
    async def _mqtt_message_callback_json(self, msg):
        """Handle incoming MQTT JSON messages."""
        try:
            payload = msg.payload
            _LOGGER.debug(
                "Received MQTT message on %s (%d bytes)", msg.topic, len(payload)
            )
            json_data = json.loads(payload)
            node_id = f"!{json_data.get('from'):08x}"
            if self.tracked_nodes and node_id not in self.tracked_nodes:
                _LOGGER.debug("Ignoring message from untracked node: %s", node_id)
                return

            payload_data = json_data.get("payload", {})
            text = payload_data.get("text")

            if not text:
                return

            data = {
                "text": text,
                "txt_time": json_data.get("timestamp"),
                "text_to": f"!{json_data.get('to'):08x}",
                "topic": msg.topic.split("/")[-2],
            }

            self._queue_update(node_id, data)

        except Exception as exc:
            _LOGGER.exception("Error processing MQTT message: %s", exc)

    async def _mqtt_message_callback(self, msg):
        """Handle incoming MQTT messages."""
        try:
            payload = msg.payload
            _LOGGER.debug(
                "Received MQTT message on %s (%d bytes)", msg.topic, len(payload)
            )

            data = pb_data.packet_receive(payload, self.encryption_key)
            if not data:
                _LOGGER.debug("Decoded payload was empty or invalid")
                return

            node_id = f"!{data.get('from'):08x}"
            if self.tracked_nodes and node_id not in self.tracked_nodes:
                _LOGGER.debug("Ignoring message from untracked node: %s", node_id)
                return

            self._queue_update(node_id, data)

        except Exception as exc:
            _LOGGER.exception("Error processing MQTT message: %s", exc)

    async def _async_update_data(self):
        """Return current cached data."""
        return self.latest

    async def _debounce_commit(self):
        """Wait briefly, then push all pending updates to Home Assistant."""
        try:
            await asyncio.sleep(self._debounce_delay)
            self.async_set_updated_data(dict(self.latest))
            _LOGGER.debug("Debounced update pushed to Home Assistant")
        except asyncio.CancelledError:
            # Timer reset — new message arrived
            pass

    def _queue_update(self, node_id: str, new_data: dict[str, Any]):
        """Queue updates and debounce async_set_updated_data()."""
        # Merge data into pending updates
        existing = self.latest.get(node_id, {})
        self.latest[node_id] = {**existing, **new_data}

        # Cancel any running debounce timer
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        # Start new timer
        self._debounce_task = asyncio.create_task(self._debounce_commit())
