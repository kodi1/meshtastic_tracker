#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pb_data.py — MQTT client for sending and receiving Meshtastic protobuf messages.
"""

import logging
import threading
import paho.mqtt.client as paho
from cachetools import TTLCache

import sys
import json
from pathlib import Path

# Ensure local meshtastic protobufs are importable
local_meshtastic_path = Path(__file__).parent / "meshtastic"
if local_meshtastic_path.exists():
    sys.path.insert(0, str(local_meshtastic_path.parent))

import meshtastic.protobuf.mqtt_pb2 as mqtt_pb2
from proto import (
    convert_envelope_to_json,
    try_encrypt_envelope,
    build_encrypted_envelope,
)

# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
_LOGGER = logging.getLogger("mqtt_parser")

# ----------------------------------------------------------------------------
# Globals
# ----------------------------------------------------------------------------
seen_ids = TTLCache(maxsize=100, ttl=10)  # deduplication

# ----------------------------------------------------------------------------
# Message Parsing
# ----------------------------------------------------------------------------


def packet_receive(msg, key: str):
    """
    Parse an incoming MQTT message containing a protobuf ServiceEnvelope.
    Deduplicates, decrypts, and converts to JSON.
    """
    try:
        env = mqtt_pb2.ServiceEnvelope()
        env.ParseFromString(msg)

        if not env.HasField("packet"):
            _LOGGER.debug("Empty envelope, skipping.")
            return None

        packet_id = getattr(env.packet, "id", None)
        if not packet_id or packet_id in seen_ids:
            _LOGGER.debug(f"Ignoring duplicate or invalid packet id={packet_id}")
            return None
        seen_ids[packet_id] = True

        _LOGGER.debug(f"Parsed protobuf envelope: {env}")

        if env.packet.HasField("encrypted"):
            try:
                try_encrypt_envelope(env, key)
                _LOGGER.debug(f"Decrypted envelope: {env.packet}")
            except Exception as e:
                _LOGGER.warning(f"Decryption failed for packet {packet_id}: {e}")
                return None

        obj = convert_envelope_to_json(env)
        _LOGGER.debug(f"Converted to JSON: {obj}")
        return obj

    except Exception as e:
        _LOGGER.exception(f"Error parsing protobuf message: {e}")
        return None


def packet_send(
    text: str,
    to_id: int,
    gw_id: int,
    channel: int,
):
    _msg = {
        'channel': 0,
        'to': to_id,
        'from': gw_id,
        'type': 'sendtext',
        'payload': text
    }

    return json.dumps(_msg)

# ----------------------------------------------------------------------------
# MQTT Callbacks
# ----------------------------------------------------------------------------


def on_message(mosq, obj, msg):
    _LOGGER.debug(f"MQTT message received on topic: {msg.topic}")
    packet_receive(msg.payload)  # ✅ pass only the raw payload bytes


def on_publish(client, userdata, mid, reasonCode, properties):
    _LOGGER.debug(f"Message {mid} published (reasonCode={reasonCode})")


def on_connect(client, userdata, flags, reasonCode, properties):
    if reasonCode == 0:
        _LOGGER.info("Connected to MQTT broker successfully")
    else:
        _LOGGER.error(f"MQTT connection failed with code {reasonCode}")


def on_disconnect(client, userdata, disconnect_flags, reasonCode, properties):
    _LOGGER.warning(
        f"Disconnected from MQTT broker "
        f"(reasonCode={reasonCode}, disconnect_flags={disconnect_flags})"
    )


# ----------------------------------------------------------------------------
# Main Loop
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    client = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2)
    client.username_pw_set("device", "123456")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.on_publish = on_publish

    client.connect("127.0.0.1", 1883, 60)
    client.subscribe("lora/msh/Bulgaria/2/e/Bulgaria/#", 0)

    _LOGGER.info("Starting MQTT loop. Press Ctrl+C to exit.")

    def mqtt_loop():
        try:
            client.loop_forever()
        except Exception as e:
            _LOGGER.error(f"MQTT loop error: {e}")

    mqtt_thread = threading.Thread(target=mqtt_loop, daemon=True)
    mqtt_thread.start()

    try:
        node_id = 0xBABACECA
        receiving_id = 0xFFFFFFFF
        topic = f"lora/msh/Bulgaria/2/json/mqtt/!{node_id:x}"

        _LOGGER.info("Connected — press Enter to send messages, or Ctrl+C to quit.")

        while True:
            text = input("Enter message: ").strip()
            if not text:
                continue
            payload = packet_send(
                text,
                receiving_id,
                node_id,
                0
            )
            if payload:
                result = client.publish(topic, payload)
                _LOGGER.info(
                    f"Sent encrypted message '{text}' to {topic} (rc={result.rc})"
                )
            else:
                _LOGGER.error("Payload is not valid ...")

    except KeyboardInterrupt:
        _LOGGER.info("Interrupted by user, stopping MQTT...")
        client.disconnect()
        client.loop_stop()
        mqtt_thread.join(timeout=2)
        _LOGGER.info("MQTT loop stopped cleanly. Goodbye!")
