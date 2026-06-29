import copy
import json
import os
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import paho.mqtt.client as mqtt
import requests

# ----------------------------
# Configuration helpers
# ----------------------------


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default

    return raw.lower() in {"1", "true", "yes", "on"}


def env_int(
    name: str,
    default: int,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    value = int(os.environ.get(name, str(default)))

    if minimum is not None:
        value = max(minimum, value)

    if maximum is not None:
        value = min(maximum, value)

    return value


def env_float(
    name: str,
    default: float,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    value = float(os.environ.get(name, str(default)))

    if minimum is not None:
        value = max(minimum, value)

    if maximum is not None:
        value = min(maximum, value)

    return value


def bounded_int(
    value: int | float,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    value = int(round(float(value)))

    if minimum is not None:
        value = max(minimum, value)

    if maximum is not None:
        value = min(maximum, value)

    return value


# ----------------------------
# Configuration
# ----------------------------

NANOLEAF_IP = os.environ["NANOLEAF_IP"]
NANOLEAF_TOKEN = os.environ["NANOLEAF_TOKEN"]

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = env_int("MQTT_PORT", 1883)
MQTT_USER = os.environ.get("MQTT_USER")
MQTT_PASS = os.environ.get("MQTT_PASS")

DISCOVERY_PREFIX = os.environ.get("DISCOVERY_PREFIX", "homeassistant")
BASE_TOPIC = os.environ.get("BASE_TOPIC", "nanoleaf_bridge/shapes")

DEVICE_ID = os.environ.get(
    "DEVICE_ID",
    f"nanoleaf_shapes_{NANOLEAF_IP.replace('.', '_')}",
)
DEVICE_NAME = os.environ.get("DEVICE_NAME", "Nanoleaf Shapes")

NANOLEAF_BASE_URL = f"http://{NANOLEAF_IP}:16021/api/v1/{NANOLEAF_TOKEN}"

# Output mode:
#   OUTPUT_MODE=stream  -> extControl UDP streaming
#   OUTPUT_MODE=rest    -> REST static custom effect fallback
OUTPUT_MODE = os.environ.get("OUTPUT_MODE", "stream").lower()
USE_STREAMING = OUTPUT_MODE in {"stream", "udp", "extcontrol", "ext_control"}

RENDER_DEBOUNCE_SECONDS = env_float("RENDER_DEBOUNCE_SECONDS", 0.15, minimum=0.0)

# For a Home Assistant add-on later, use:
# STATE_FILE=/data/nanoleaf_panel_state.json
STATE_FILE = Path(os.environ.get("STATE_FILE", "nanoleaf_panel_state.json"))

RENDER_ON_STARTUP = env_bool("RENDER_ON_STARTUP", False)
RESTORE_ONLY_IF_NANOLEAF_ON = env_bool("RESTORE_ONLY_IF_NANOLEAF_ON", True)

FORCE_GLOBAL_ON_ON_RENDER = env_bool("FORCE_GLOBAL_ON_ON_RENDER", True)

FORCE_GLOBAL_BRIGHTNESS_ON_RENDER = env_bool("FORCE_GLOBAL_BRIGHTNESS_ON_RENDER", True)
GLOBAL_BRIGHTNESS_VALUE = env_int(
    "GLOBAL_BRIGHTNESS_VALUE", 100, minimum=1, maximum=100
)

TURN_GLOBAL_OFF_WHEN_FRAME_EMPTY = env_bool("TURN_GLOBAL_OFF_WHEN_FRAME_EMPTY", True)

SYNC_GLOBAL_STATE = env_bool("SYNC_GLOBAL_STATE", True)
GLOBAL_SYNC_INTERVAL_SECONDS = env_float(
    "GLOBAL_SYNC_INTERVAL_SECONDS", 5.0, minimum=1.0
)

RESTORE_FRAMEBUFFER_WHEN_GLOBAL_TURNS_ON = env_bool(
    "RESTORE_FRAMEBUFFER_WHEN_GLOBAL_TURNS_ON",
    True,
)

PUBLISH_EFFECTIVE_OFF_WHEN_GLOBAL_OFF = env_bool(
    "PUBLISH_EFFECTIVE_OFF_WHEN_GLOBAL_OFF",
    True,
)

# Effects dropdown on the All Panels MQTT light.
ENABLE_NANOLEAF_EFFECTS = env_bool("ENABLE_NANOLEAF_EFFECTS", True)
BRIDGE_EFFECT_NAME = os.environ.get("BRIDGE_EFFECT_NAME", "Bridge Framebuffer")

# extControl streaming options
EXTCONTROL_PORT = env_int("EXTCONTROL_PORT", 60222, minimum=1, maximum=65535)
STREAM_FPS = env_float("STREAM_FPS", 25.0, minimum=1.0, maximum=60.0)

# Nanoleaf transition time is in tenths of a second.
# 0 = immediate, 1 = 100 ms, 10 = 1 second.
STREAM_TRANSITION_TIME = env_int("STREAM_TRANSITION_TIME", 1, minimum=0, maximum=65535)

EXTCONTROL_REENABLE_INTERVAL_SECONDS = env_float(
    "EXTCONTROL_REENABLE_INTERVAL_SECONDS",
    30.0,
    minimum=5.0,
)


# ----------------------------
# Data model
# ----------------------------


@dataclass
class Panel:
    panel_id: int
    x: int = 0
    y: int = 0
    shape_type: Optional[int] = None


def clamp(value: int | float, minimum: int = 0, maximum: int = 255) -> int:
    return max(minimum, min(maximum, int(round(float(value)))))


def default_panel_state() -> dict:
    return {
        "state": "OFF",
        "color_mode": "rgb",
        "brightness": 255,
        "color": {
            "r": 255,
            "g": 255,
            "b": 255,
        },
        # Stored in Nanoleaf units: tenths of a second.
        "transition": STREAM_TRANSITION_TIME,
    }


def normalise_panel_state(value: dict, fallback: Optional[dict] = None) -> dict:
    if fallback is None:
        fallback = default_panel_state()

    if not isinstance(value, dict):
        return copy.deepcopy(fallback)

    state_value = str(value.get("state", fallback.get("state", "OFF"))).upper()
    state_value = "ON" if state_value == "ON" else "OFF"

    brightness = clamp(value.get("brightness", fallback.get("brightness", 255)))

    color_value = value.get(
        "color", fallback.get("color", {"r": 255, "g": 255, "b": 255})
    )
    if not isinstance(color_value, dict):
        color_value = fallback.get("color", {"r": 255, "g": 255, "b": 255})

    fallback_color = fallback.get("color", {"r": 255, "g": 255, "b": 255})

    transition = bounded_int(
        value.get("transition", fallback.get("transition", STREAM_TRANSITION_TIME)),
        minimum=0,
        maximum=65535,
    )

    return {
        "state": state_value,
        "color_mode": "rgb",
        "brightness": brightness,
        "color": {
            "r": clamp(color_value.get("r", fallback_color.get("r", 255))),
            "g": clamp(color_value.get("g", fallback_color.get("g", 255))),
            "b": clamp(color_value.get("b", fallback_color.get("b", 255))),
        },
        "transition": transition,
    }


# ----------------------------
# Nanoleaf discovery
# ----------------------------


def get_panel_layout() -> List[Panel]:
    response = requests.get(
        f"{NANOLEAF_BASE_URL}/panelLayout/layout",
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    if "positionData" in data:
        position_data = data["positionData"]
    elif "layout" in data and "positionData" in data["layout"]:
        position_data = data["layout"]["positionData"]
    elif "panelLayout" in data and "layout" in data["panelLayout"]:
        position_data = data["panelLayout"]["layout"]["positionData"]
    else:
        raise RuntimeError(f"Could not find positionData in layout response: {data}")

    panels: List[Panel] = []

    for item in position_data:
        panel_id = item.get("panelId")
        shape_type = item.get("shapeType")

        if panel_id is None:
            continue

        panel_id = int(panel_id)

        # Nanoleaf layouts commonly include the controller as panelId 0.
        if panel_id == 0:
            print("Ignoring panelId 0, likely controller/non-light pseudo-panel")
            continue

        panels.append(
            Panel(
                panel_id=panel_id,
                x=int(item.get("x", 0)),
                y=int(item.get("y", 0)),
                shape_type=shape_type,
            )
        )

    if not panels:
        raise RuntimeError("No controllable panels found in Nanoleaf layout response")

    return panels


# ----------------------------
# Bridge
# ----------------------------


class NanoleafBridge:
    def __init__(self, panels: List[Panel]):
        self.panels = panels
        self.panel_ids = [panel.panel_id for panel in panels]

        self.lock = threading.RLock()

        self.render_timer: Optional[threading.Timer] = None

        self.global_sync_timer: Optional[threading.Timer] = None
        self.global_sync_started = False

        self.stream_thread: Optional[threading.Thread] = None
        self.stream_stop_event = threading.Event()
        self.stream_socket: Optional[socket.socket] = None
        self.extcontrol_last_enabled = 0.0

        # Desired per-panel framebuffer owned by the bridge.
        self.state: Dict[int, dict] = {
            panel_id: default_panel_state() for panel_id in self.panel_ids
        }

        # Nanoleaf whole-device state.
        self.device_power_on: Optional[bool] = None
        self.device_brightness: Optional[int] = None

        # Native Nanoleaf effects.
        self.effects: List[str] = []
        self.active_effect: Optional[str] = None

        self.load_state()

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="nanoleaf_panel_bridge",
            protocol=mqtt.MQTTv5,
        )

        if MQTT_USER:
            self.client.username_pw_set(MQTT_USER, MQTT_PASS)

        self.client.will_set(
            f"{BASE_TOPIC}/status",
            payload="offline",
            retain=True,
        )

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    # ----------------------------
    # Lifecycle
    # ----------------------------

    def start(self):
        print(f"Output mode: {'stream/extControl' if USE_STREAMING else 'rest'}")
        print(f"Effects enabled: {ENABLE_NANOLEAF_EFFECTS}")
        print(f"Connecting to MQTT broker {MQTT_HOST}:{MQTT_PORT}")

        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            print("Stopping bridge...")
            self.shutdown()

    def shutdown(self):
        self.stop_streaming()

        with self.lock:
            if self.render_timer is not None:
                self.render_timer.cancel()
                self.render_timer = None

            if self.global_sync_timer is not None:
                self.global_sync_timer.cancel()
                self.global_sync_timer = None

            self.global_sync_started = False

        self.save_state()

        try:
            self.client.publish(f"{BASE_TOPIC}/status", "offline", retain=True)
            self.client.disconnect()
        except Exception:
            pass

    # ----------------------------
    # MQTT callbacks
    # ----------------------------

    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"Connected to MQTT: {reason_code}")

        client.publish(f"{BASE_TOPIC}/status", "online", retain=True)

        client.subscribe(f"{BASE_TOPIC}/set")
        client.subscribe(f"{BASE_TOPIC}/panel/+/set")
        client.subscribe("homeassistant/status")

        if SYNC_GLOBAL_STATE:
            self.refresh_global_state(publish=False)

        if ENABLE_NANOLEAF_EFFECTS:
            self.refresh_effects(publish=False)

        self.publish_discovery()
        self.publish_all_states()

        if SYNC_GLOBAL_STATE:
            self.start_global_sync()

        if RENDER_ON_STARTUP:
            should_render = True

            if RESTORE_ONLY_IF_NANOLEAF_ON and self.device_power_on is not True:
                should_render = False

            if should_render:
                print(
                    "RENDER_ON_STARTUP enabled; rendering saved framebuffer to Nanoleaf"
                )
                with self.lock:
                    self.active_effect = None
                self.schedule_render()
            else:
                print(
                    "RENDER_ON_STARTUP enabled but Nanoleaf global power is not ON; skipping render"
                )

    def on_message(self, client, userdata, message):
        topic = message.topic
        payload_raw = message.payload.decode("utf-8", errors="replace")

        if topic == "homeassistant/status" and payload_raw == "online":
            if ENABLE_NANOLEAF_EFFECTS:
                self.refresh_effects(publish=False)

            self.publish_discovery()
            self.publish_all_states()
            return

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            print(f"Invalid JSON on {topic}: {payload_raw}")
            return

        try:
            if topic == f"{BASE_TOPIC}/set":
                self.handle_whole_light_command(payload)
            elif topic.startswith(f"{BASE_TOPIC}/panel/") and topic.endswith("/set"):
                panel_id = int(topic.split("/")[-2])
                self.handle_panel_command(panel_id, payload)
        except Exception as exc:
            print(f"Error handling MQTT command on {topic}: {exc}")

    # ----------------------------
    # Command handling
    # ----------------------------

    def handle_whole_light_command(self, payload: dict):
        """
        Handles commands sent to the All Panels entity.

        Important behaviour:
        - Selecting a native Nanoleaf effect stops bridge streaming.
        - Selecting Bridge Framebuffer returns to bridge-controlled panels.
        - If a native Nanoleaf effect is active, brightness/on/off commands
          adjust global Nanoleaf state only and do not restart the framebuffer.
        """

        if ENABLE_NANOLEAF_EFFECTS and "effect" in payload:
            effect_name = str(payload["effect"])

            if effect_name and effect_name != BRIDGE_EFFECT_NAME:
                self.handle_effect_command(effect_name, payload)
                return

            if effect_name == BRIDGE_EFFECT_NAME:
                print("Switching All Panels back to bridge framebuffer mode")
                with self.lock:
                    self.active_effect = None

        # Fix for native scenes/effects:
        # While a native Nanoleaf effect is active, brightness/on/off commands
        # should control Nanoleaf global state only, not restart extControl.
        if ENABLE_NANOLEAF_EFFECTS and self.handle_native_effect_global_command(
            payload
        ):
            return

        with self.lock:
            for panel_id in self.panel_ids:
                self.apply_payload_to_panel(panel_id, payload)

            self.active_effect = None
            self.update_optimistic_device_power_from_framebuffer()
            self.save_state()

        self.publish_all_states()
        self.publish_global_attributes()
        self.schedule_render()

    def handle_panel_command(self, panel_id: int, payload: dict):
        with self.lock:
            if panel_id not in self.state:
                print(f"Ignoring command for unknown panel {panel_id}")
                return

            self.apply_payload_to_panel(panel_id, payload)

            # Any individual panel command exits native Nanoleaf effect mode.
            self.active_effect = None

            self.update_optimistic_device_power_from_framebuffer()
            self.save_state()

        self.publish_panel_state(panel_id)
        self.publish_whole_state()
        self.publish_global_attributes()
        self.schedule_render()

    def handle_effect_command(self, effect_name: str, payload: dict):
        if effect_name not in self.effects:
            print(
                f"Effect '{effect_name}' was not in cached effect list; trying anyway"
            )

        # Native Nanoleaf effect mode and bridge streaming mode are mutually exclusive.
        self.stop_streaming()

        if "state" in payload and str(payload["state"]).upper() == "OFF":
            self.set_global_nanoleaf_state(on=False)
            self.publish_all_states()
            self.publish_global_attributes()
            return

        brightness = None

        if "brightness" in payload:
            # HA brightness is 0-255; Nanoleaf global brightness is 1-100.
            brightness = bounded_int(
                (clamp(payload["brightness"]) / 255) * 100,
                minimum=1,
                maximum=100,
            )
        elif FORCE_GLOBAL_BRIGHTNESS_ON_RENDER:
            brightness = GLOBAL_BRIGHTNESS_VALUE

        self.set_global_nanoleaf_state(
            on=True,
            brightness=brightness,
        )

        self.select_nanoleaf_effect(effect_name)

        self.publish_whole_state()
        self.publish_global_attributes()

    def handle_native_effect_global_command(self, payload: dict) -> bool:
        """
        If a native Nanoleaf effect is active, global brightness/on/off commands
        from the All Panels light should adjust the Nanoleaf's global state
        without returning to bridge framebuffer mode.

        Returns True if handled here.
        Returns False if the command should fall through to framebuffer handling.
        """
        with self.lock:
            active_effect = self.active_effect

        if not active_effect:
            return False

        # Colour changes should intentionally leave native effect mode and return
        # to the bridge framebuffer.
        if "color" in payload:
            return False

        # Explicitly choosing Bridge Framebuffer should not be handled here.
        if payload.get("effect") == BRIDGE_EFFECT_NAME:
            return False

        # These are safe to apply to the native Nanoleaf effect globally.
        allowed_keys = {
            "state",
            "brightness",
            "transition",
            "color_mode",
        }

        # If Home Assistant sends the currently active native effect along with
        # brightness, that is still safe.
        if "effect" in payload:
            if payload["effect"] == active_effect:
                allowed_keys.add("effect")
            else:
                return False

        if not set(payload.keys()).issubset(allowed_keys):
            return False

        on_value = None
        brightness_value = None

        if "state" in payload:
            state_value = str(payload["state"]).upper()
            on_value = state_value == "ON"

        if "brightness" in payload:
            # HA brightness is 0-255; Nanoleaf global brightness is 1-100.
            brightness_value = bounded_int(
                (clamp(payload["brightness"]) / 255) * 100,
                minimum=1,
                maximum=100,
            )

            # A brightness change usually implies ON.
            if on_value is None:
                on_value = True

        self.set_global_nanoleaf_state(
            on=on_value,
            brightness=brightness_value,
        )

        # Preserve the effect as the active mode.
        with self.lock:
            self.active_effect = active_effect

        self.publish_whole_state()
        self.publish_global_attributes()

        print(
            "Handled All Panels global command while native effect is active: "
            f"effect={active_effect}, on={on_value}, brightness={brightness_value}"
        )

        return True

    def apply_payload_to_panel(self, panel_id: int, payload: dict):
        panel_state = self.state[panel_id]

        if "state" in payload:
            state_value = str(payload["state"]).upper()
            panel_state["state"] = "ON" if state_value == "ON" else "OFF"

        if "brightness" in payload:
            panel_state["brightness"] = clamp(payload["brightness"])

        if "color" in payload:
            color = payload["color"]
            if isinstance(color, dict):
                panel_state["color"] = {
                    "r": clamp(color.get("r", panel_state["color"]["r"])),
                    "g": clamp(color.get("g", panel_state["color"]["g"])),
                    "b": clamp(color.get("b", panel_state["color"]["b"])),
                }

        # Home Assistant transition is in seconds.
        # Nanoleaf transition is in tenths of a second.
        if "transition" in payload:
            try:
                panel_state["transition"] = bounded_int(
                    float(payload["transition"]) * 10,
                    minimum=0,
                    maximum=65535,
                )
            except (TypeError, ValueError):
                print(f"Ignoring invalid transition value: {payload['transition']}")

        # HA often sends brightness/color without explicit state.
        if "brightness" in payload or "color" in payload:
            panel_state["state"] = "ON"

        panel_state["color_mode"] = "rgb"
        self.state[panel_id] = normalise_panel_state(panel_state)

    def any_desired_panel_on(self) -> bool:
        return any(
            normalise_panel_state(state)["state"] == "ON"
            for state in self.state.values()
        )

    def update_optimistic_device_power_from_framebuffer(self):
        if self.any_desired_panel_on():
            self.device_power_on = True
        elif TURN_GLOBAL_OFF_WHEN_FRAME_EMPTY:
            self.device_power_on = False

    # ----------------------------
    # Persistence
    # ----------------------------

    def load_state(self):
        if not STATE_FILE.exists():
            return

        try:
            saved = json.loads(STATE_FILE.read_text())
        except Exception as exc:
            print(f"Could not read saved state file {STATE_FILE}: {exc}")
            return

        with self.lock:
            for panel_id in self.panel_ids:
                saved_state = saved.get(str(panel_id))
                if saved_state:
                    self.state[panel_id] = normalise_panel_state(
                        saved_state,
                        fallback=self.state[panel_id],
                    )

        print(f"Loaded saved panel state from {STATE_FILE}")

    def save_state(self):
        with self.lock:
            serialisable = {
                str(panel_id): normalise_panel_state(state)
                for panel_id, state in self.state.items()
            }

        try:
            if STATE_FILE.parent != Path("."):
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

            tmp_file = STATE_FILE.with_name(f"{STATE_FILE.name}.tmp")
            tmp_file.write_text(json.dumps(serialisable, indent=2))
            tmp_file.replace(STATE_FILE)
        except Exception as exc:
            print(f"Could not save panel state to {STATE_FILE}: {exc}")

    # ----------------------------
    # Nanoleaf global state
    # ----------------------------

    def get_global_nanoleaf_state(self) -> dict:
        response = requests.get(
            f"{NANOLEAF_BASE_URL}/state",
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()

        on_value = None
        brightness_value = None

        if isinstance(data.get("on"), dict) and "value" in data["on"]:
            on_value = bool(data["on"]["value"])

        if isinstance(data.get("brightness"), dict) and "value" in data["brightness"]:
            brightness_value = int(data["brightness"]["value"])

        return {
            "on": on_value,
            "brightness": brightness_value,
            "raw": data,
        }

    def set_global_nanoleaf_state(
        self,
        on: Optional[bool] = None,
        brightness: Optional[int] = None,
    ):
        payload = {}

        if on is not None:
            payload["on"] = {"value": bool(on)}

        if brightness is not None:
            payload["brightness"] = {
                "value": bounded_int(brightness, minimum=1, maximum=100)
            }

        if not payload:
            return

        response = requests.put(
            f"{NANOLEAF_BASE_URL}/state",
            json=payload,
            timeout=10,
        )

        if not response.ok:
            print(
                f"Nanoleaf global state error {response.status_code}: {response.text}"
            )
            response.raise_for_status()

        with self.lock:
            if on is not None:
                self.device_power_on = bool(on)

            if brightness is not None:
                self.device_brightness = bounded_int(brightness, minimum=1, maximum=100)

        print(
            "Updated Nanoleaf global state: "
            f"on={self.device_power_on}, brightness={self.device_brightness}"
        )

    def refresh_global_state(self, publish: bool = True):
        try:
            global_state = self.get_global_nanoleaf_state()
        except Exception as exc:
            print(f"Could not read Nanoleaf global state: {exc}")
            return

        previous_power = self.device_power_on
        previous_brightness = self.device_brightness

        with self.lock:
            if global_state["on"] is not None:
                self.device_power_on = global_state["on"]

            if global_state["brightness"] is not None:
                self.device_brightness = global_state["brightness"]

            current_power = self.device_power_on
            current_brightness = self.device_brightness
            active_effect = self.active_effect

        if previous_power != current_power:
            print(f"Nanoleaf global power changed: {previous_power} -> {current_power}")

            if current_power is False:
                print("Nanoleaf was turned off externally; stopping UDP stream")
                self.stop_streaming()

            if (
                current_power is True
                and previous_power is False
                and RESTORE_FRAMEBUFFER_WHEN_GLOBAL_TURNS_ON
                and active_effect is None
                and self.any_desired_panel_on()
            ):
                print("Nanoleaf was turned on externally; restoring bridge framebuffer")
                self.schedule_render()

        if previous_brightness != current_brightness:
            print(
                "Nanoleaf global brightness changed: "
                f"{previous_brightness} -> {current_brightness}"
            )

        # Only sync selected native effect when we are not actively streaming.
        if ENABLE_NANOLEAF_EFFECTS and not self.is_streaming():
            self.refresh_selected_effect(publish=False)

        if publish:
            self.publish_all_states()
            self.publish_global_attributes()

    def start_global_sync(self):
        with self.lock:
            if self.global_sync_started:
                return

            self.global_sync_started = True

        self.schedule_global_sync()

    def schedule_global_sync(self):
        with self.lock:
            if not self.global_sync_started:
                return

            if self.global_sync_timer is not None:
                self.global_sync_timer.cancel()

            self.global_sync_timer = threading.Timer(
                GLOBAL_SYNC_INTERVAL_SECONDS,
                self.global_sync_tick,
            )
            self.global_sync_timer.daemon = True
            self.global_sync_timer.start()

    def global_sync_tick(self):
        try:
            self.refresh_global_state(publish=True)
        finally:
            with self.lock:
                should_continue = self.global_sync_started

            if should_continue:
                self.schedule_global_sync()

    # ----------------------------
    # Native Nanoleaf effects
    # ----------------------------

    def get_effect_list_for_ha(self) -> List[str]:
        effects = [BRIDGE_EFFECT_NAME]

        with self.lock:
            active_effect = self.active_effect
            cached_effects = list(self.effects)

        for effect in cached_effects:
            if effect and effect not in effects:
                effects.append(effect)

        if active_effect and active_effect not in effects:
            effects.append(active_effect)

        return effects

    def refresh_effects(self, publish: bool = True):
        try:
            effects = self.get_nanoleaf_effects()
        except Exception as exc:
            print(f"Could not load Nanoleaf effects: {exc}")
            effects = []

        with self.lock:
            self.effects = effects

        self.refresh_selected_effect(publish=False)

        print(f"Loaded {len(effects)} Nanoleaf effects")

        if publish:
            self.publish_discovery()
            self.publish_whole_state()
            self.publish_global_attributes()

    def get_nanoleaf_effects(self) -> List[str]:
        # Try the simple effect list endpoint first.
        try:
            response = requests.get(
                f"{NANOLEAF_BASE_URL}/effects/effectsList",
                timeout=10,
            )

            if response.ok:
                data = response.json()

                if isinstance(data, list):
                    return sorted(set(str(item) for item in data))

                if isinstance(data, dict):
                    for key in ("effectsList", "animations"):
                        if key in data and isinstance(data[key], list):
                            return self.extract_effect_names(data[key])
        except Exception as exc:
            print(f"GET effects/effectsList failed, trying requestAll: {exc}")

        # Fallback: command API requestAll.
        response = requests.put(
            f"{NANOLEAF_BASE_URL}/effects",
            json={
                "write": {
                    "command": "requestAll",
                }
            },
            timeout=10,
        )

        if not response.ok:
            print(
                f"Nanoleaf requestAll effects error {response.status_code}: {response.text}"
            )
            response.raise_for_status()

        if not response.text.strip():
            return []

        data = response.json()

        if isinstance(data, dict) and isinstance(data.get("animations"), list):
            return self.extract_effect_names(data["animations"])

        if isinstance(data, list):
            return self.extract_effect_names(data)

        return []

    def extract_effect_names(self, items: list) -> List[str]:
        names: List[str] = []

        for item in items:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = (
                    item.get("animName") or item.get("name") or item.get("effectName")
                )
                if name:
                    names.append(str(name))

        return sorted(set(names))

    def get_selected_nanoleaf_effect(self) -> Optional[str]:
        response = requests.get(
            f"{NANOLEAF_BASE_URL}/effects/select",
            timeout=10,
        )

        if not response.ok:
            response.raise_for_status()

        text = response.text.strip()

        if not text:
            return None

        try:
            parsed = response.json()
            if isinstance(parsed, str):
                return parsed
        except Exception:
            pass

        return text.strip('"')

    def is_extcontrol_effect_name(self, effect_name: Optional[str]) -> bool:
        if not effect_name:
            return False

        normalised = effect_name.lower().replace(" ", "").replace("_", "")
        return "extcontrol" in normalised or "externalcontrol" in normalised

    def refresh_selected_effect(self, publish: bool = True):
        try:
            selected = self.get_selected_nanoleaf_effect()
        except Exception as exc:
            print(f"Could not read selected Nanoleaf effect: {exc}")
            return

        if self.is_extcontrol_effect_name(selected):
            selected = None

        with self.lock:
            # If bridge streaming is active, keep reporting Bridge Framebuffer.
            if self.is_streaming_locked():
                self.active_effect = None
            else:
                self.active_effect = selected

        if publish:
            self.publish_whole_state()
            self.publish_global_attributes()

    def select_nanoleaf_effect(self, effect_name: str):
        response = requests.put(
            f"{NANOLEAF_BASE_URL}/effects",
            json={
                "select": effect_name,
            },
            timeout=10,
        )

        if not response.ok:
            print(
                f"Nanoleaf select effect error {response.status_code}: {response.text}"
            )
            response.raise_for_status()

        with self.lock:
            self.active_effect = effect_name
            self.device_power_on = True

        print(f"Selected Nanoleaf effect: {effect_name}")

    # ----------------------------
    # Debounced rendering
    # ----------------------------

    def schedule_render(self):
        with self.lock:
            if self.render_timer is not None:
                self.render_timer.cancel()

            self.render_timer = threading.Timer(
                RENDER_DEBOUNCE_SECONDS,
                self.flush_render,
            )
            self.render_timer.daemon = True
            self.render_timer.start()

    def flush_render(self):
        with self.lock:
            self.render_timer = None
            state_snapshot = copy.deepcopy(self.state)

        try:
            self.render_to_nanoleaf(state_snapshot)
        except Exception as exc:
            print(f"Could not render to Nanoleaf: {exc}")

    def render_to_nanoleaf(self, state_snapshot: Optional[Dict[int, dict]] = None):
        if USE_STREAMING:
            self.render_to_nanoleaf_stream(state_snapshot)
        else:
            self.render_to_nanoleaf_rest(state_snapshot)

    # ----------------------------
    # REST renderer fallback
    # ----------------------------

    def render_to_nanoleaf_rest(self, state_snapshot: Optional[Dict[int, dict]] = None):
        state_source = state_snapshot or self.state

        desired_on_panels = [
            panel_id
            for panel_id in self.panel_ids
            if normalise_panel_state(state_source[panel_id])["state"] == "ON"
        ]

        if not desired_on_panels:
            print("Framebuffer has no ON panels")

            self.stop_streaming()

            if TURN_GLOBAL_OFF_WHEN_FRAME_EMPTY:
                self.set_global_nanoleaf_state(on=False)
                self.publish_all_states()
                self.publish_global_attributes()
                return

        if FORCE_GLOBAL_ON_ON_RENDER or self.device_power_on is False:
            brightness = (
                GLOBAL_BRIGHTNESS_VALUE if FORCE_GLOBAL_BRIGHTNESS_ON_RENDER else None
            )
            self.set_global_nanoleaf_state(on=True, brightness=brightness)
        elif FORCE_GLOBAL_BRIGHTNESS_ON_RENDER:
            self.set_global_nanoleaf_state(brightness=GLOBAL_BRIGHTNESS_VALUE)

        parts = [str(len(self.panel_ids))]

        for panel_id in self.panel_ids:
            state = normalise_panel_state(state_source[panel_id])

            if state["state"] != "ON":
                r = g = b = 0
            else:
                brightness_255 = state["brightness"] / 255
                color = state["color"]
                r = clamp(color["r"] * brightness_255)
                g = clamp(color["g"] * brightness_255)
                b = clamp(color["b"] * brightness_255)

            parts.extend(
                [
                    str(panel_id),
                    "1",
                    str(r),
                    str(g),
                    str(b),
                    "0",
                    str(
                        bounded_int(
                            state.get("transition", STREAM_TRANSITION_TIME),
                            minimum=0,
                            maximum=65535,
                        )
                    ),
                ]
            )

        anim_data = " ".join(parts)

        payload = {
            "write": {
                "command": "display",
                "version": "2.0",
                "animType": "static",
                "animData": anim_data,
                "loop": False,
                "palette": [],
            }
        }

        response = requests.put(
            f"{NANOLEAF_BASE_URL}/effects",
            json=payload,
            timeout=10,
        )

        if not response.ok:
            print(f"Nanoleaf effect error {response.status_code}: {response.text}")
            response.raise_for_status()

        with self.lock:
            self.device_power_on = True
            self.active_effect = None
            if FORCE_GLOBAL_BRIGHTNESS_ON_RENDER:
                self.device_brightness = GLOBAL_BRIGHTNESS_VALUE

        self.publish_all_states()
        self.publish_global_attributes()

        print(f"Rendered {len(self.panel_ids)} panels to Nanoleaf via REST")

    # ----------------------------
    # extControl streaming renderer
    # ----------------------------

    def render_to_nanoleaf_stream(
        self, state_snapshot: Optional[Dict[int, dict]] = None
    ):
        state_source = state_snapshot or self.state

        desired_on_panels = [
            panel_id
            for panel_id in self.panel_ids
            if normalise_panel_state(state_source[panel_id])["state"] == "ON"
        ]

        if not desired_on_panels:
            print("Framebuffer has no ON panels")

            self.stop_streaming()

            if TURN_GLOBAL_OFF_WHEN_FRAME_EMPTY:
                self.set_global_nanoleaf_state(on=False)
                self.publish_all_states()
                self.publish_global_attributes()

            return

        if FORCE_GLOBAL_ON_ON_RENDER or self.device_power_on is False:
            brightness = (
                GLOBAL_BRIGHTNESS_VALUE if FORCE_GLOBAL_BRIGHTNESS_ON_RENDER else None
            )
            self.set_global_nanoleaf_state(on=True, brightness=brightness)
        elif FORCE_GLOBAL_BRIGHTNESS_ON_RENDER:
            self.set_global_nanoleaf_state(brightness=GLOBAL_BRIGHTNESS_VALUE)

        with self.lock:
            self.active_effect = None

        self.start_streaming()

        with self.lock:
            self.device_power_on = True
            if FORCE_GLOBAL_BRIGHTNESS_ON_RENDER:
                self.device_brightness = GLOBAL_BRIGHTNESS_VALUE

        self.publish_all_states()
        self.publish_global_attributes()

    def is_streaming(self) -> bool:
        with self.lock:
            return self.is_streaming_locked()

    def is_streaming_locked(self) -> bool:
        return self.stream_thread is not None and self.stream_thread.is_alive()

    def start_streaming(self):
        with self.lock:
            if self.stream_thread is not None and self.stream_thread.is_alive():
                return

            self.stream_stop_event.clear()

            self.stream_thread = threading.Thread(
                target=self.stream_loop,
                name="nanoleaf-extcontrol-stream",
                daemon=True,
            )
            self.stream_thread.start()

        print(f"Started Nanoleaf UDP streamer at {STREAM_FPS:.1f} FPS")

    def stop_streaming(self):
        thread = None

        with self.lock:
            if self.stream_thread is not None:
                thread = self.stream_thread

            self.stream_stop_event.set()

            if self.stream_socket is not None:
                try:
                    self.stream_socket.close()
                except Exception:
                    pass
                self.stream_socket = None

            self.stream_thread = None
            self.extcontrol_last_enabled = 0.0

        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)

    def enable_extcontrol(self):
        payload = {
            "write": {
                "command": "display",
                "animType": "extControl",
                "extControlVersion": "v2",
            }
        }

        response = requests.put(
            f"{NANOLEAF_BASE_URL}/effects",
            json=payload,
            timeout=10,
        )

        if response.status_code == 422:
            fallback_payload = {
                "write": {
                    "command": "display",
                    "animType": "extControl",
                }
            }

            response = requests.put(
                f"{NANOLEAF_BASE_URL}/effects",
                json=fallback_payload,
                timeout=10,
            )

        if not response.ok:
            print(f"Nanoleaf extControl error {response.status_code}: {response.text}")
            response.raise_for_status()

        with self.lock:
            self.extcontrol_last_enabled = time.monotonic()
            self.active_effect = None

        print("Enabled Nanoleaf extControl mode")

    def stream_loop(self):
        period = 1.0 / STREAM_FPS

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        with self.lock:
            self.stream_socket = sock

        while not self.stream_stop_event.is_set():
            loop_started = time.monotonic()

            try:
                now = time.monotonic()

                with self.lock:
                    last_enabled = self.extcontrol_last_enabled

                if (
                    last_enabled == 0.0
                    or now - last_enabled >= EXTCONTROL_REENABLE_INTERVAL_SECONDS
                ):
                    if FORCE_GLOBAL_ON_ON_RENDER:
                        brightness = (
                            GLOBAL_BRIGHTNESS_VALUE
                            if FORCE_GLOBAL_BRIGHTNESS_ON_RENDER
                            else None
                        )
                        self.set_global_nanoleaf_state(on=True, brightness=brightness)
                    elif FORCE_GLOBAL_BRIGHTNESS_ON_RENDER:
                        self.set_global_nanoleaf_state(
                            brightness=GLOBAL_BRIGHTNESS_VALUE
                        )

                    self.enable_extcontrol()

                with self.lock:
                    state_snapshot = copy.deepcopy(self.state)

                if not self.snapshot_has_on_panels(state_snapshot):
                    break

                packet = self.build_extcontrol_packet(state_snapshot)
                sock.sendto(packet, (NANOLEAF_IP, EXTCONTROL_PORT))

            except OSError:
                break
            except Exception as exc:
                print(f"Streaming error: {exc}")
                time.sleep(1.0)

            elapsed = time.monotonic() - loop_started
            sleep_for = max(0.0, period - elapsed)
            time.sleep(sleep_for)

        try:
            sock.close()
        except Exception:
            pass

        with self.lock:
            if self.stream_socket is sock:
                self.stream_socket = None

            if self.stream_thread is threading.current_thread():
                self.stream_thread = None

        print("Nanoleaf UDP stream loop exited")

    def snapshot_has_on_panels(self, state_snapshot: Dict[int, dict]) -> bool:
        return any(
            normalise_panel_state(state_snapshot[panel_id])["state"] == "ON"
            for panel_id in self.panel_ids
        )

    def build_extcontrol_packet(self, state_snapshot: Dict[int, dict]) -> bytes:
        packet = bytearray()

        packet += struct.pack(">H", len(self.panel_ids))

        for panel_id in self.panel_ids:
            state = normalise_panel_state(state_snapshot[panel_id])

            if state["state"] != "ON":
                r = g = b = 0
            else:
                brightness_255 = state["brightness"] / 255
                color = state["color"]

                r = clamp(color["r"] * brightness_255)
                g = clamp(color["g"] * brightness_255)
                b = clamp(color["b"] * brightness_255)

            transition_time = bounded_int(
                state.get("transition", STREAM_TRANSITION_TIME),
                minimum=0,
                maximum=65535,
            )

            packet += struct.pack(
                ">HBBBBH",
                panel_id,
                r,
                g,
                b,
                0,
                transition_time,
            )

        return bytes(packet)

    # ----------------------------
    # MQTT discovery and state
    # ----------------------------

    def publish_discovery(self):
        device = {
            "identifiers": [DEVICE_ID],
            "name": DEVICE_NAME,
            "manufacturer": "Nanoleaf",
            "model": "Shapes",
        }

        panel_unique_ids = [
            f"{DEVICE_ID}_panel_{panel_id}" for panel_id in self.panel_ids
        ]

        whole_config = {
            "name": "All Panels",
            "unique_id": f"{DEVICE_ID}_all",
            "schema": "json",
            "command_topic": f"{BASE_TOPIC}/set",
            "state_topic": f"{BASE_TOPIC}/state",
            "json_attributes_topic": f"{BASE_TOPIC}/attributes",
            "availability_topic": f"{BASE_TOPIC}/status",
            "brightness": True,
            "supported_color_modes": ["rgb"],
            "optimistic": False,
            "device": device,
            "group": panel_unique_ids,
            "icon": "mdi:hexagon-multiple-outline",
        }

        if ENABLE_NANOLEAF_EFFECTS:
            whole_config["effect"] = True
            whole_config["effect_list"] = self.get_effect_list_for_ha()

        self.client.publish(
            f"{DISCOVERY_PREFIX}/light/{DEVICE_ID}_all/config",
            json.dumps(whole_config),
            retain=True,
        )

        for panel in self.panels:
            unique_id = f"{DEVICE_ID}_panel_{panel.panel_id}"

            panel_config = {
                "name": f"Panel {panel.panel_id}",
                "unique_id": unique_id,
                "schema": "json",
                "command_topic": f"{BASE_TOPIC}/panel/{panel.panel_id}/set",
                "state_topic": f"{BASE_TOPIC}/panel/{panel.panel_id}/state",
                "json_attributes_topic": f"{BASE_TOPIC}/panel/{panel.panel_id}/attributes",
                "availability_topic": f"{BASE_TOPIC}/status",
                "brightness": True,
                "supported_color_modes": ["rgb"],
                "optimistic": False,
                "device": device,
                "icon": "mdi:hexagon-outline",
            }

            self.client.publish(
                f"{DISCOVERY_PREFIX}/light/{unique_id}/config",
                json.dumps(panel_config),
                retain=True,
            )

            attrs = {
                "panel_id": panel.panel_id,
                "x": panel.x,
                "y": panel.y,
                "shape_type": panel.shape_type,
            }

            self.client.publish(
                f"{BASE_TOPIC}/panel/{panel.panel_id}/attributes",
                json.dumps(attrs),
                retain=True,
            )

        self.publish_global_attributes()

        print(
            f"Published MQTT discovery for {len(self.panels)} panels "
            "plus whole-light entity"
        )

    def publish_all_states(self):
        for panel_id in self.panel_ids:
            self.publish_panel_state(panel_id)

        self.publish_whole_state()

    def state_for_mqtt(self, state: dict) -> dict:
        normalised = normalise_panel_state(state)

        return {
            "state": normalised["state"],
            "color_mode": "rgb",
            "brightness": normalised["brightness"],
            "color": normalised["color"],
            "transition": normalised["transition"],
        }

    def effective_panel_state(self, panel_id: int) -> dict:
        with self.lock:
            desired = self.state_for_mqtt(self.state[panel_id])
            device_power_on = self.device_power_on

        effective = copy.deepcopy(desired)

        if PUBLISH_EFFECTIVE_OFF_WHEN_GLOBAL_OFF and device_power_on is False:
            effective["state"] = "OFF"

        return effective

    def publish_panel_state(self, panel_id: int):
        payload = json.dumps(self.effective_panel_state(panel_id))

        self.client.publish(
            f"{BASE_TOPIC}/panel/{panel_id}/state",
            payload,
            retain=True,
        )

    def publish_whole_state(self):
        with self.lock:
            device_power_on = self.device_power_on
            device_brightness = self.device_brightness
            active_effect = self.active_effect
            state_values = [
                normalise_panel_state(state) for state in self.state.values()
            ]

        off_state = {
            "state": "OFF",
            "color_mode": "rgb",
            "brightness": 255,
            "color": {
                "r": 255,
                "g": 255,
                "b": 255,
            },
            "transition": STREAM_TRANSITION_TIME,
            "effect": active_effect or BRIDGE_EFFECT_NAME,
        }

        if PUBLISH_EFFECTIVE_OFF_WHEN_GLOBAL_OFF and device_power_on is False:
            whole_state = off_state
        elif active_effect:
            brightness_255 = 255
            if device_brightness is not None:
                brightness_255 = bounded_int(
                    (device_brightness / 100) * 255, minimum=1, maximum=255
                )

            whole_state = {
                "state": "ON",
                "color_mode": "rgb",
                "brightness": brightness_255,
                "color": {
                    "r": 255,
                    "g": 255,
                    "b": 255,
                },
                "transition": STREAM_TRANSITION_TIME,
                "effect": active_effect,
            }
        else:
            on_panels = [state for state in state_values if state["state"] == "ON"]

            if not on_panels:
                whole_state = off_state
            else:
                brightness = round(
                    sum(panel["brightness"] for panel in on_panels) / len(on_panels)
                )

                color = {
                    "r": round(
                        sum(panel["color"]["r"] for panel in on_panels) / len(on_panels)
                    ),
                    "g": round(
                        sum(panel["color"]["g"] for panel in on_panels) / len(on_panels)
                    ),
                    "b": round(
                        sum(panel["color"]["b"] for panel in on_panels) / len(on_panels)
                    ),
                }

                transition = round(
                    sum(
                        panel.get("transition", STREAM_TRANSITION_TIME)
                        for panel in on_panels
                    )
                    / len(on_panels)
                )

                whole_state = {
                    "state": "ON",
                    "color_mode": "rgb",
                    "brightness": brightness,
                    "color": color,
                    "transition": bounded_int(transition, minimum=0, maximum=65535),
                    "effect": BRIDGE_EFFECT_NAME,
                }

        self.client.publish(
            f"{BASE_TOPIC}/state",
            json.dumps(whole_state),
            retain=True,
        )

    def publish_global_attributes(self):
        with self.lock:
            attrs = {
                "nanoleaf_global_power": self.device_power_on,
                "nanoleaf_global_brightness": self.device_brightness,
                "output_mode": OUTPUT_MODE,
                "use_streaming": USE_STREAMING,
                "stream_fps": STREAM_FPS,
                "stream_transition_time_default": STREAM_TRANSITION_TIME,
                "extcontrol_port": EXTCONTROL_PORT,
                "force_global_on_on_render": FORCE_GLOBAL_ON_ON_RENDER,
                "force_global_brightness_on_render": FORCE_GLOBAL_BRIGHTNESS_ON_RENDER,
                "global_brightness_value": GLOBAL_BRIGHTNESS_VALUE,
                "sync_global_state": SYNC_GLOBAL_STATE,
                "effects_enabled": ENABLE_NANOLEAF_EFFECTS,
                "active_effect": self.active_effect or BRIDGE_EFFECT_NAME,
                "effect_count": len(self.effects),
                "effects": self.get_effect_list_for_ha(),
            }

        self.client.publish(
            f"{BASE_TOPIC}/attributes",
            json.dumps(attrs),
            retain=True,
        )


# ----------------------------
# Main
# ----------------------------


def main():
    try:
        panels = get_panel_layout()
    except Exception as exc:
        print(f"Could not discover Nanoleaf panels: {exc}")
        sys.exit(1)

    print("Discovered controllable panels:")
    for panel in panels:
        print(
            f"  Panel {panel.panel_id}: "
            f"x={panel.x}, y={panel.y}, shape_type={panel.shape_type}"
        )

    bridge = NanoleafBridge(panels)
    bridge.start()


if __name__ == "__main__":
    main()
