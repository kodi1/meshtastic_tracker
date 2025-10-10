"""Constants for the Meshtastic Tracker integration."""

DOMAIN = "meshtastic_tracker"

# Configuration keys
CONF_ENCRYPTION_KEY = "encryption_key"
CONF_TRACKED_NODES = "tracked_nodes"
CONF_FRIENDLY_NAME = "friendly_name"
CONF_BASE_TOPIC = "base_topic"
CONF_CHANNEL_NAME = "channel_name"
CONF_DEBOUNCE_MS = "debounce_ms"
CONF_PDOP_MIN_THRESHOLD = "pdop_min_threshold"
CONF_PDOP_MAX_THRESHOLD = "pdop_max_threshold"

# Default values
DEFAULT_BASE_TOPIC = "msh/EU_868"
DEFAULT_CHANNEL_NAME = "LongFast"
DEFAULT_FRIENDLY_NAME = "Meshtastic Tracker"
DEFAULT_DEBOUNCE_MS = 500  # milliseconds
DEFAULT_KEY = "1PG7OiApB1nwvP+rz05pAQ=="

# Entity attributes
ATTR_LAST_RECEIVER = "last_receiver"
ATTR_HOPS_TAKEN = "hops_taken"
ATTR_SATS_IN_VIEW = "sats_in_view"
ATTR_GROUND_SPEED = "ground_speed"
ATTR_PRECISION_BITS = "precision_bits"
ATTR_PRESSURE = "pressure"
ATTR_BATTERY_VOLTAGE = "voltage"

# Supported platforms
PLATFORMS = ["sensor", "device_tracker"]
