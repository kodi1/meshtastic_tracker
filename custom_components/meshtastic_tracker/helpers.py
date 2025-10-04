import re
from homeassistant.exceptions import HomeAssistantError

_NODE_ID_PATTERN = re.compile(r"^![A-Fa-f0-9]{8}$")


def validate_meshtastic_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not _NODE_ID_PATTERN.match(value):
        raise HomeAssistantError(
            f"{field} must be in format !XXXXXXXX (8 hex characters, e.g. !BABACECA). Got: {value}"
        )
    return value


def validate_channel(value) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise HomeAssistantError(f"channel must be an integer 0–6 (got {value})")
    if ivalue < 0 or ivalue > 6:
        raise HomeAssistantError(f"channel must be between 0 and 6 (got {ivalue})")
    return ivalue


def validate_hops(value) -> int:
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise HomeAssistantError(f"hops must be an integer 1–5 (got {value})")
    if ivalue < 1 or ivalue > 5:
        raise HomeAssistantError(f"hops must be between 1 and 5 (got {ivalue})")
    return ivalue
