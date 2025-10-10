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
    CONF_PDOP_MIN_THRESHOLD,
    CONF_PDOP_MAX_THRESHOLD,
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

    @staticmethod
    def async_get_options_flow(config_entry):
        return MeshtasticOptionsFlowHandler(config_entry)


class MeshtasticOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for Meshtastic Tracker integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}

        # Handle form submission
        if user_input is not None:
            min_pdop = float(user_input.get(CONF_PDOP_MIN_THRESHOLD, 0.5))
            max_pdop = float(user_input.get(CONF_PDOP_MAX_THRESHOLD, 8.0))

            if min_pdop >= max_pdop:
                errors["base"] = "invalid_pdop_range"
                _LOGGER.warning(
                    "Invalid PDOP range entered: min %.2f >= max %.2f", min_pdop, max_pdop
                )
            else:
                # ✅ Valid input
                return self.async_create_entry(title="", data=user_input)

        # Current saved values or defaults
        current = self.config_entry.options
        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PDOP_MIN_THRESHOLD,
                    default=current.get(CONF_PDOP_MIN_THRESHOLD, 0.5),
                ): vol.Coerce(float),
                vol.Optional(
                    CONF_PDOP_MAX_THRESHOLD,
                    default=current.get(CONF_PDOP_MAX_THRESHOLD, 8.0),
                ): vol.Coerce(float),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "CONF_PDOP_MIN_THRESHOLD": "Minimum acceptable PDOP value",
                "CONF_PDOP_MAX_THRESHOLD": "Maximum acceptable PDOP value",
            },
        )

