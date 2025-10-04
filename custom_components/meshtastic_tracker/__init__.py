"""Initialize the Meshtastic Tracker integration."""

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components import mqtt

from .const import (
    DOMAIN,
    CONF_BASE_TOPIC,
    CONF_CHANNEL_NAME,
    CONF_TRACKED_NODES,
    CONF_FRIENDLY_NAME,
    CONF_ENCRYPTION_KEY,
    CONF_DEBOUNCE_MS,
    DEFAULT_DEBOUNCE_MS,
)

from .coordinator import MeshtasticTrackerCoordinator
from .pb_data import packet_send
from .helpers import (
    validate_meshtastic_id,
    validate_channel,
    validate_hops,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS: list[str] = ["sensor", "device_tracker"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Meshtastic Tracker from a config entry."""
    data = entry.data
    options = entry.options

    base_topic = options.get(CONF_BASE_TOPIC, data.get(CONF_BASE_TOPIC, "msh/EU_868"))
    channel_name = options.get(
        CONF_CHANNEL_NAME, data.get(CONF_CHANNEL_NAME, "LongFast")
    )

    tracked_nodes = options.get(CONF_TRACKED_NODES, data.get(CONF_TRACKED_NODES, []))
    friendly_name = (
        entry.title
        or options.get(CONF_FRIENDLY_NAME)
        or data.get(CONF_FRIENDLY_NAME, "Meshtastic Tracker")
    )
    encryption_key = options.get(CONF_ENCRYPTION_KEY, data.get(CONF_ENCRYPTION_KEY))
    debounce_ms = options.get(
        CONF_DEBOUNCE_MS, data.get(CONF_DEBOUNCE_MS, DEFAULT_DEBOUNCE_MS)
    )
    coordinator = MeshtasticTrackerCoordinator(
        hass=hass,
        entry=entry,
        base_topic=base_topic,
        channel_name=channel_name,
        tracked_nodes=tracked_nodes,
        friendly_name=friendly_name,
        encryption_key=encryption_key,
        debounce_ms=debounce_ms,
    )

    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Register global send_packet service once
    if not hass.services.has_service(DOMAIN, "send_packet"):

        async def handle_send_packet(call: ServiceCall):
            """Handle meshtastic_tracker.send_packet service call."""
            data = call.data
            text = data.get("text")
            if not text:
                raise HomeAssistantError("Missing required field: text")

            from_id_str = validate_meshtastic_id(data.get("from_id"), "from_id")
            to_id_str = validate_meshtastic_id(data.get("to_id"), "to_id")

            from_id = int(from_id_str[1:], 16)
            to_id = int(to_id_str[1:], 16)
            mqtt_topic = f"{coordinator.base_topic}/2/json/mqtt/!{from_id:08x}"
            channel = validate_channel(data.get("channel", 0))

            _LOGGER.debug(
                "Preparing Meshtastic json: text=%s, from=0x%X, to=0x%X, ch=%d",
                text,
                from_id,
                to_id,
                channel,
            )

            payload = packet_send(text, to_id, from_id, channel)
            if not payload:
                raise HomeAssistantError("Failed to generate payload")

            await mqtt.async_publish(hass, mqtt_topic, payload, qos=0, retain=False)
            _LOGGER.debug(
                "Meshtastic json message '%s' published to %s (%d bytes)",
                text,
                mqtt_topic,
                len(payload),
            )

        hass.services.async_register(DOMAIN, "send_packet", handle_send_packet)
        _LOGGER.debug("Service meshtastic_tracker.send_packet registered globally")

    entry.async_on_unload(entry.add_update_listener(reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug(
        "Meshtastic Tracker setup complete — topic=%s, nodes=%s, name=%s",
        base_topic,
        tracked_nodes,
        friendly_name,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and its platforms."""
    _LOGGER.debug("Unloading Meshtastic Tracker integration for %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    coordinator: MeshtasticTrackerCoordinator | None = hass.data[DOMAIN].pop(
        entry.entry_id, None
    )
    if coordinator:
        await coordinator.async_stop()

    _LOGGER.info("Meshtastic Tracker unloaded (ok=%s)", unload_ok)
    return unload_ok


async def reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config options or friendly name updates."""
    from .const import (
        DEFAULT_BASE_TOPIC,
        CONF_TRACKED_NODES,
        CONF_ENCRYPTION_KEY,
        CONF_FRIENDLY_NAME,
    )

    coordinator: MeshtasticTrackerCoordinator | None = hass.data[DOMAIN].get(
        entry.entry_id
    )

    if coordinator is None:
        _LOGGER.debug("Coordinator not found — performing full reload")
        await hass.config_entries.async_reload(entry.entry_id)
        return

    # ✅ Prefer entry.title (UI rename) over options
    new_name = entry.title or entry.options.get(CONF_FRIENDLY_NAME)
    new_topic = entry.options.get(CONF_BASE_TOPIC, coordinator.base_topic)
    new_nodes = entry.options.get(CONF_TRACKED_NODES, coordinator.tracked_nodes)
    new_key = entry.options.get(CONF_ENCRYPTION_KEY, coordinator.encryption_key)
    new_debounce = entry.options.get(
        CONF_DEBOUNCE_MS, coordinator._debounce_delay * 1000
    )

    if new_name != coordinator.friendly_name:
        _LOGGER.debug(
            "Detected rename: '%s' → '%s' — updating coordinator name dynamically",
            coordinator.friendly_name,
            new_name,
        )
        await coordinator.async_update_friendly_name(new_name)
        return

    if (
        new_topic != coordinator.mqtt_topic
        or new_nodes != coordinator.tracked_nodes
        or new_key != coordinator.encryption_key
        or abs(new_debounce - coordinator._debounce_delay * 1000) > 1
    ):
        _LOGGER.debug("Detected config change beyond name — reloading integration")
        await hass.config_entries.async_reload(entry.entry_id)
    else:
        _LOGGER.debug("No meaningful config changes detected — skipping reload")
