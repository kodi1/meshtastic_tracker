import logging
import re
from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from .const import (
    DOMAIN,
    CONF_TRACKED_NODES,
    CONF_ENCRYPTION_KEY,
    CONF_BASE_TOPIC,
    CONF_CHANNEL_NAME,
    CONF_DEBOUNCE_MS,
    DEFAULT_BASE_TOPIC,
    DEFAULT_CHANNEL_NAME,
    DEFAULT_KEY,
    DEFAULT_DEBOUNCE_MS,
)


_LOGGER = logging.getLogger(__name__)

# Node IDs must be 9 symbols total, starting with "!"
NODE_ID_PATTERN = re.compile(r"^![A-Za-z0-9]{8}$")


class MeshtasticConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Meshtastic Tracker integration."""

    VERSION = 3
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}

        if user_input is not None:
            # Normalize and validate node IDs
            raw_nodes = [
                n.strip()
                for n in user_input[CONF_TRACKED_NODES].split(",")
                if n.strip()
            ]
            invalid_nodes = [n for n in raw_nodes if not NODE_ID_PATTERN.match(n)]

            # Default encryption key if blank
            encryption_key = user_input.get(CONF_ENCRYPTION_KEY, "").strip()
            if not encryption_key:
                encryption_key = DEFAULT_KEY
            user_input[CONF_ENCRYPTION_KEY] = encryption_key

            if invalid_nodes:
                errors["base"] = "invalid_node_id"
                _LOGGER.warning("Invalid node IDs entered: %s", invalid_nodes)
            else:
                user_input[CONF_TRACKED_NODES] = raw_nodes
                return self.async_create_entry(
                    title="Meshtastic Tracker", data=user_input
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_TRACKED_NODES): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=False)
                ),
                vol.Required(
                    CONF_BASE_TOPIC, default=DEFAULT_BASE_TOPIC
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=False)),
                vol.Required(
                    CONF_CHANNEL_NAME, default=DEFAULT_CHANNEL_NAME
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=False)),
                vol.Optional(
                    CONF_ENCRYPTION_KEY, default=DEFAULT_KEY
                ): selector.TextSelector(selector.TextSelectorConfig(multiline=False)),
                vol.Optional(
                    CONF_DEBOUNCE_MS, default=DEFAULT_DEBOUNCE_MS
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=2000,
                        unit_of_measurement="ms",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(data_schema, {}),
            errors=errors,
            description_placeholders={
                "CONF_TRACK_NODES": "Comma-separated node IDs (e.g. !ABCDEFGH,!12345678)",
                "CONF_BASE_TOPIC": "Base MQTT topic (e.g. msh/EU_868)",
                "CONF_CHANNEL_NAME": "Channel name (e.g. LongFast)",
                "CONF_ENCRYPTION_KEY": "Optional encryption key (Base64, default applied)",
            },
        )
