"""
proto.py — Meshtastic protobuf and encryption helpers
Builds and parses ServiceEnvelope messages for MQTT communication.
https://github.com/kvj/hass_Mtastic_MQTT/blob/main/custom_components/mtastic_mqtt/proto.py
"""

import base64
import logging
import time
import os
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2, telemetry_pb2

_LOGGER = logging.getLogger(__name__)

# Default encryption key (used when channel PSK is "AQ==")
DEFAULT_ENC_KEY = "1PG7OiApB1nwvP+rz05pAQ=="

# ----------------------------------------------------------------------------
# Internal conversion helpers (used for decoding incoming envelopes)
# ----------------------------------------------------------------------------


def _as_position(obj, envelope):
    """Convert Position protobuf message into simplified dict."""
    # Normalize values and apply scaling where needed
    ground_track = None
    if hasattr(obj, "ground_track"):
        # Some firmware encodes ground_track as millidegrees (° * 1e3)
        gt = obj.ground_track
        if gt > 1000:  # likely scaled
            ground_track = gt / 1000.0
        else:
            ground_track = gt

    pdop = None
    if hasattr(obj, "PDOP"):
        # PDOP should be a small float, but some firmwares send it *10 or as int
        val = obj.PDOP
        if val > 50:  # e.g., 297 → 29.7
            pdop = val / 10.0
        else:
            pdop = val

    return (
        "position",
        {
            "latitude_i": getattr(obj, "latitude_i", None),
            "longitude_i": getattr(obj, "longitude_i", None),
            "altitude": getattr(obj, "altitude", None),
            "ground_speed": getattr(obj, "ground_speed", None),
            "sats_in_view": getattr(obj, "sats_in_view", None),
            "precision_bits": getattr(obj, "precision_bits", None),
            "ground_track": ground_track,
            "PDOP": pdop,
        },
    )


def _as_telemetry(obj, envelope):
    """Convert Telemetry messages into structured JSON."""
    type_ = obj.WhichOneof("variant")
    _LOGGER.debug(f"_as_telemetry: {type_}")

    if type_ == "device_metrics":
        return (
            "device_metrics",
            {
                "battery_level": obj.device_metrics.battery_level,
                "voltage": obj.device_metrics.voltage,
                "channel_utilization": obj.device_metrics.channel_utilization,
                "air_util_tx": obj.device_metrics.air_util_tx,
            },
        )

    elif type_ == "environment_metrics":
        return (
            "environment_metrics",
            {
                "temperature": obj.environment_metrics.temperature,
                "relative_humidity": obj.environment_metrics.relative_humidity,
                "barometric_pressure": obj.environment_metrics.barometric_pressure,
                "gas_resistance": obj.environment_metrics.gas_resistance,
            },
        )

    return (None, {})


def _as_node_info(obj, envelope):
    """Convert NODEINFO_APP messages to JSON."""
    return (
        "nodeinfo",
        {
            "id": obj.id,
            "shortname": obj.short_name,
            "longname": obj.long_name,
        },
    )


def _as_neighbor_info(obj, envelope):
    """Convert neighbor info packets to JSON."""
    payload = {
        "neighbors": [{"node_id": n.node_id, "snr": n.snr} for n in obj.neighbors],
        "neighbors_count": len(obj.neighbors),
    }
    return ("neighborinfo", payload)


def _as_text_message(obj, envelope):
    """Convert raw text packets to JSON."""
    return (
        "text_message",
        {
            "text": obj,
            "rx_time": envelope.packet.rx_time,
        },
    )


_converters = {
    portnums_pb2.POSITION_APP: (mesh_pb2.Position, _as_position),
    portnums_pb2.TELEMETRY_APP: (telemetry_pb2.Telemetry, _as_telemetry),
    portnums_pb2.NODEINFO_APP: (mesh_pb2.User, _as_node_info),
    portnums_pb2.NEIGHBORINFO_APP: (mesh_pb2.NeighborInfo, _as_neighbor_info),
    portnums_pb2.TEXT_MESSAGE_APP: (None, _as_text_message),
}


def convert_envelope_to_json(envelope) -> dict:
    """Convert a protobuf ServiceEnvelope to a simplified dict for logging or display."""
    result = {
        "from": getattr(envelope.packet, "from"),
        "rx_snr": getattr(envelope.packet, "rx_snr"),
        "rx_rssi": getattr(envelope.packet, "rx_rssi"),
        "hops_taken": getattr(envelope.packet, "hop_start")
        - getattr(envelope.packet, "hop_limit"),
        "sender": envelope.gateway_id,
        "packet_id": getattr(envelope.packet, "id", "None"),
        "rx_time": getattr(envelope.packet, "rx_time", "None"),
    }

    if config := _converters.get(envelope.packet.decoded.portnum):
        # Decode payload
        if config[0]:
            obj = config[0]()
            obj.ParseFromString(envelope.packet.decoded.payload)
            _LOGGER.debug(f"convert_packet_to_json(): proto = {obj}")
        else:
            obj = envelope.packet.decoded.payload.decode("utf8")

        type_, payload = config[1](obj, envelope)
        if type_ and payload:
            result.update({"type": type_})
            result = {**result, **payload}
    else:
        _LOGGER.debug(
            f"convert_packet_to_json(): unsupported portnum = {envelope.packet.decoded.portnum}"
        )

    return result


# ----------------------------------------------------------------------------
# Message construction (encryption + envelope building)
# ----------------------------------------------------------------------------


def build_encrypted_envelope(
    text: str,
    from_id: int,
    to_id: int,
    channel: int,
    key_b64: str,
    channel_id: str = None,
    gateway_id: str = None,
    hops: int = 3,
    rx_snr: float = 0.0,
    rx_rssi: float = 0.0,
):
    """
    Build a fully encrypted ServiceEnvelope ready to send over MQTT.
    Mirrors Meshtastic device behavior (AES-CTR mode).
    """

    try:
        # --- Step 1: Decode PSK (Base64) ---
        key_bytes = base64.b64decode(
            key_b64.replace("_", "/").replace("-", "+").encode("ascii")
        )
        if len(key_bytes) == 1 and key_bytes[0] == 0x01:
            key_bytes = base64.b64decode(DEFAULT_ENC_KEY.encode("ascii"))

        # --- Step 2: Build inner Data message ---
        data_msg = mesh_pb2.Data()
        data_msg.portnum = portnums_pb2.PortNum.TEXT_MESSAGE_APP
        data_msg.payload = text.encode("utf-8")
        plaintext = data_msg.SerializeToString()

        # --- Step 3: Create nonce & encrypt using AES-CTR ---
        packet_id = int.from_bytes(os.urandom(4), "little")  # 32-bit safe random ID
        nonce = packet_id.to_bytes(8, "little") + from_id.to_bytes(8, "little")

        cipher = Cipher(
            algorithms.AES(key_bytes), modes.CTR(nonce), backend=default_backend()
        )
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()

        # --- Step 4: Build MeshPacket ---
        packet = mesh_pb2.MeshPacket()
        packet.id = packet_id
        setattr(packet, "from", from_id)  # Python reserved keyword workaround
        packet.to = to_id
        packet.channel = channel
        packet.hop_limit = hops
        packet.hop_start = hops
        packet.rx_snr = int(rx_snr)  # Typically 0 on send
        packet.rx_rssi = int(rx_rssi)  # Typically 0 on send
        packet.priority = mesh_pb2.MeshPacket.Priority.HIGH
        packet.rx_time = int(time.time())
        packet.encrypted = ciphertext

        # --- Step 5: Wrap into ServiceEnvelope ---
        envelope = mqtt_pb2.ServiceEnvelope()
        envelope.packet.CopyFrom(packet)
        if channel_id:
            envelope.channel_id = channel_id
        if gateway_id:
            envelope.gateway_id = gateway_id

        _LOGGER.debug(f"Built encrypted envelope: {envelope}")
        return envelope

    except Exception as e:
        _LOGGER.exception(f"Failed to build encrypted envelope: {e}")
        return None


def try_encrypt_envelope(envelope, key_b64):
    """Decrypt a received envelope using AES-CTR (for debugging or inspection)."""
    key_bytes = base64.b64decode(
        key_b64.replace("_", "/").replace("-", "+").encode("ascii")
    )
    if len(key_bytes) == 1 and key_bytes[0] == 0x01:
        key_bytes = base64.b64decode(DEFAULT_ENC_KEY.encode("ascii"))

    nonce = envelope.packet.id.to_bytes(8, "little") + getattr(
        envelope.packet, "from"
    ).to_bytes(8, "little")

    cipher = Cipher(
        algorithms.AES(key_bytes), modes.CTR(nonce), backend=default_backend()
    )
    decryptor = cipher.decryptor()
    decrypted_bytes = decryptor.update(envelope.packet.encrypted) + decryptor.finalize()

    data = mesh_pb2.Data()
    data.ParseFromString(decrypted_bytes)
    envelope.packet.decoded.CopyFrom(data)
