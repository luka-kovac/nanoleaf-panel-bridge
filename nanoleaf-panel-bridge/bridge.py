import copy
import html
import json
import math
import os
import random
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


ADDON_OPTIONS_FILE = Path(os.environ.get("ADDON_OPTIONS_FILE", "/data/options.json"))


def load_addon_options() -> dict:
    """Read Home Assistant add-on options when running under Supervisor.

    Environment variables remain supported so the bridge can still run as a plain
    Python script or Docker container outside Home Assistant OS.
    """
    if not ADDON_OPTIONS_FILE.exists():
        return {}

    try:
        data = json.loads(ADDON_OPTIONS_FILE.read_text())
    except Exception as exc:
        print(f"Could not read add-on options from {ADDON_OPTIONS_FILE}: {exc}")
        return {}

    if not isinstance(data, dict):
        print(f"Ignoring add-on options because {ADDON_OPTIONS_FILE} did not contain an object")
        return {}

    return data


ADDON_OPTIONS = load_addon_options()


def config_value(option_name: str, env_name: str, default: Any = None, *, allow_empty: bool = False) -> Any:
    if option_name in ADDON_OPTIONS:
        value = ADDON_OPTIONS[option_name]
        if allow_empty or value not in (None, ""):
            return value

    if env_name in os.environ:
        value = os.environ.get(env_name)
        if allow_empty or value not in (None, ""):
            return value

    return default


def config_required(option_name: str, env_name: str) -> str:
    value = config_value(option_name, env_name)
    if value in (None, ""):
        raise RuntimeError(
            f"Missing required configuration option '{option_name}' "
            f"or environment variable '{env_name}'"
        )
    return str(value)


def parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).lower() in {"1", "true", "yes", "on"}


def config_bool(option_name: str, env_name: str, default: bool) -> bool:
    return parse_bool(config_value(option_name, env_name, default), default)


def config_int(option_name: str, env_name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    value = int(config_value(option_name, env_name, default))
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def config_float(option_name: str, env_name: str, default: float, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    value = float(config_value(option_name, env_name, default))
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

NANOLEAF_IP = config_required("nanoleaf_ip", "NANOLEAF_IP")
NANOLEAF_TOKEN = config_required("nanoleaf_token", "NANOLEAF_TOKEN")

MQTT_HOST = str(config_value("mqtt_host", "MQTT_HOST", "localhost"))
MQTT_PORT = config_int("mqtt_port", "MQTT_PORT", 1883)
MQTT_USER = config_value("mqtt_user", "MQTT_USER", None)
MQTT_PASS = config_value("mqtt_password", "MQTT_PASS", None, allow_empty=True)

if MQTT_USER == "":
    MQTT_USER = None
if MQTT_PASS == "":
    MQTT_PASS = None

DISCOVERY_PREFIX = str(config_value("discovery_prefix", "DISCOVERY_PREFIX", "homeassistant"))
BASE_TOPIC = str(config_value("base_topic", "BASE_TOPIC", "nanoleaf_bridge/shapes"))

DEVICE_ID = str(
    config_value(
        "device_id",
        "DEVICE_ID",
        f"nanoleaf_shapes_{NANOLEAF_IP.replace('.', '_')}",
    )
)
DEVICE_NAME = str(config_value("device_name", "DEVICE_NAME", "Nanoleaf Shapes"))

NANOLEAF_BASE_URL = f"http://{NANOLEAF_IP}:16021/api/v1/{NANOLEAF_TOKEN}"

# Output mode:
#   OUTPUT_MODE=stream  -> extControl UDP streaming
#   OUTPUT_MODE=rest    -> REST static custom effect fallback
OUTPUT_MODE = str(config_value("output_mode", "OUTPUT_MODE", "stream")).lower()
USE_STREAMING = OUTPUT_MODE in {"stream", "udp", "extcontrol", "ext_control"}

RENDER_DEBOUNCE_SECONDS = config_float(
    "render_debounce_seconds",
    "RENDER_DEBOUNCE_SECONDS",
    0.15,
    minimum=0.0,
)

# For a Home Assistant add-on, the default is persistent add-on data.
STATE_FILE = Path(str(config_value("state_file", "STATE_FILE", "/data/nanoleaf_panel_state.json")))

RENDER_ON_STARTUP = config_bool("render_on_startup", "RENDER_ON_STARTUP", False)
RESTORE_ONLY_IF_NANOLEAF_ON = config_bool(
    "restore_only_if_nanoleaf_on",
    "RESTORE_ONLY_IF_NANOLEAF_ON",
    True,
)

FORCE_GLOBAL_ON_ON_RENDER = config_bool(
    "force_global_on_on_render",
    "FORCE_GLOBAL_ON_ON_RENDER",
    True,
)

FORCE_GLOBAL_BRIGHTNESS_ON_RENDER = config_bool(
    "force_global_brightness_on_render",
    "FORCE_GLOBAL_BRIGHTNESS_ON_RENDER",
    True,
)
GLOBAL_BRIGHTNESS_VALUE = config_int(
    "global_brightness_value",
    "GLOBAL_BRIGHTNESS_VALUE",
    100,
    minimum=1,
    maximum=100,
)

TURN_GLOBAL_OFF_WHEN_FRAME_EMPTY = config_bool(
    "turn_global_off_when_frame_empty",
    "TURN_GLOBAL_OFF_WHEN_FRAME_EMPTY",
    True,
)

SYNC_GLOBAL_STATE = config_bool("sync_global_state", "SYNC_GLOBAL_STATE", True)
GLOBAL_SYNC_INTERVAL_SECONDS = config_float(
    "global_sync_interval_seconds",
    "GLOBAL_SYNC_INTERVAL_SECONDS",
    5.0,
    minimum=1.0,
)

RESTORE_FRAMEBUFFER_WHEN_GLOBAL_TURNS_ON = config_bool(
    "restore_framebuffer_when_global_turns_on",
    "RESTORE_FRAMEBUFFER_WHEN_GLOBAL_TURNS_ON",
    True,
)

PUBLISH_EFFECTIVE_OFF_WHEN_GLOBAL_OFF = config_bool(
    "publish_effective_off_when_global_off",
    "PUBLISH_EFFECTIVE_OFF_WHEN_GLOBAL_OFF",
    True,
)

# Effects dropdown on the All Panels MQTT light.
ENABLE_NANOLEAF_EFFECTS = config_bool(
    "enable_nanoleaf_effects",
    "ENABLE_NANOLEAF_EFFECTS",
    True,
)
BRIDGE_EFFECT_NAME = str(
    config_value("bridge_effect_name", "BRIDGE_EFFECT_NAME", "Bridge Framebuffer")
)

# Bridge-side pseudo effects. These are rendered by bridge.py using the
# framebuffer colours rather than selecting a native Nanoleaf scene.
ENABLE_BRIDGE_EFFECTS = config_bool(
    "enable_bridge_effects",
    "ENABLE_BRIDGE_EFFECTS",
    True,
)
SPARKLE_EFFECT_NAME = str(
    config_value("sparkle_effect_name", "SPARKLE_EFFECT_NAME", "Bridge Sparkle")
)
SPARKLE_INTERVAL_SECONDS = config_float(
    "sparkle_interval_seconds",
    "SPARKLE_INTERVAL_SECONDS",
    0.12,
    minimum=0.02,
    maximum=10.0,
)
SPARKLE_MIN_MULTIPLIER = config_float(
    "sparkle_min_multiplier",
    "SPARKLE_MIN_MULTIPLIER",
    0.35,
    minimum=0.0,
    maximum=1.0,
)
SPARKLE_MAX_MULTIPLIER = config_float(
    "sparkle_max_multiplier",
    "SPARKLE_MAX_MULTIPLIER",
    1.0,
    minimum=0.0,
    maximum=1.0,
)
if SPARKLE_MAX_MULTIPLIER < SPARKLE_MIN_MULTIPLIER:
    SPARKLE_MIN_MULTIPLIER, SPARKLE_MAX_MULTIPLIER = (
        SPARKLE_MAX_MULTIPLIER,
        SPARKLE_MIN_MULTIPLIER,
    )
SPARKLE_SMOOTHING = config_float(
    "sparkle_smoothing",
    "SPARKLE_SMOOTHING",
    0.65,
    minimum=0.0,
    maximum=0.99,
)

# extControl streaming options
EXTCONTROL_PORT = config_int(
    "extcontrol_port",
    "EXTCONTROL_PORT",
    60222,
    minimum=1,
    maximum=65535,
)
STREAM_FPS = config_float("stream_fps", "STREAM_FPS", 25.0, minimum=1.0, maximum=60.0)

# Nanoleaf transition time is in tenths of a second.
# 0 = immediate, 1 = 100 ms, 10 = 1 second.
STREAM_TRANSITION_TIME = config_int(
    "stream_transition_time",
    "STREAM_TRANSITION_TIME",
    1,
    minimum=0,
    maximum=65535,
)

EXTCONTROL_REENABLE_INTERVAL_SECONDS = config_float(
    "extcontrol_reenable_interval_seconds",
    "EXTCONTROL_REENABLE_INTERVAL_SECONDS",
    30.0,
    minimum=5.0,
)

# Layout / preview publishing. The preview is published as an MQTT image entity
# using raw SVG bytes. The layout JSON is retained separately for dashboards,
# custom cards, or external tools.
ENABLE_LAYOUT_PREVIEW = config_bool(
    "enable_layout_preview",
    "ENABLE_LAYOUT_PREVIEW",
    True,
)
LAYOUT_PREVIEW_DEBOUNCE_SECONDS = config_float(
    "layout_preview_debounce_seconds",
    "LAYOUT_PREVIEW_DEBOUNCE_SECONDS",
    0.25,
    minimum=0.0,
)
LAYOUT_PREVIEW_PADDING = config_int(
    "layout_preview_padding",
    "LAYOUT_PREVIEW_PADDING",
    24,
    minimum=0,
    maximum=500,
)
LAYOUT_PREVIEW_PANEL_RADIUS_MULTIPLIER = config_float(
    "layout_preview_panel_radius_multiplier",
    "LAYOUT_PREVIEW_PANEL_RADIUS_MULTIPLIER",
    0.42,
    minimum=0.05,
    maximum=2.0,
)
LAYOUT_PREVIEW_MIN_PANEL_RADIUS = config_float(
    "layout_preview_min_panel_radius",
    "LAYOUT_PREVIEW_MIN_PANEL_RADIUS",
    18.0,
    minimum=1.0,
    maximum=500.0,
)
LAYOUT_PREVIEW_LABELS = config_bool(
    "layout_preview_labels",
    "LAYOUT_PREVIEW_LABELS",
    True,
)
LAYOUT_PREVIEW_BACKGROUND = str(
    config_value("layout_preview_background", "LAYOUT_PREVIEW_BACKGROUND", "#101418")
)
LAYOUT_PREVIEW_STROKE = str(
    config_value("layout_preview_stroke", "LAYOUT_PREVIEW_STROKE", "#d7dde5")
)

# Painter brush light. This is an MQTT-discovered helper light that does not
# control the Nanoleaf directly. A dashboard can use Home Assistant's native
# light colour/brightness picker on this entity, and a custom painter card can
# read its state as the current brush.
ENABLE_PAINTER_BRUSH = config_bool(
    "enable_painter_brush",
    "ENABLE_PAINTER_BRUSH",
    True,
)
PAINTER_BRUSH_NAME = str(
    config_value("painter_brush_name", "PAINTER_BRUSH_NAME", "Panel Painter Brush")
)
PAINTER_BRUSH_DEFAULT_BRIGHTNESS = config_int(
    "painter_brush_default_brightness",
    "PAINTER_BRUSH_DEFAULT_BRIGHTNESS",
    200,
    minimum=1,
    maximum=255,
)
PAINTER_BRUSH_DEFAULT_COLOR = str(
    config_value("painter_brush_default_color", "PAINTER_BRUSH_DEFAULT_COLOR", "#ff66cc")
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
    orientation: int = 0


@dataclass
class Zone:
    zone_id: str
    name: str
    panel_ids: List[int]
    # Scales brightness commands sent to this zone.
    # 1.0 = unchanged, 0.5 = half brightness.
    brightness_multiplier: float = 1.0
    # Hard cap after scaling, in Home Assistant brightness units.
    # 255 = no cap, 128 = about 50%.
    max_brightness: int = 255


def slugify_identifier(value: str) -> str:
    value = value.strip().lower()
    chars = []
    previous_underscore = False

    for char in value:
        if char.isalnum():
            chars.append(char)
            previous_underscore = False
        elif not previous_underscore:
            chars.append("_")
            previous_underscore = True

    slug = "".join(chars).strip("_")
    return slug or "zone"


def parse_panel_ids(raw_value: Any) -> List[int]:
    if raw_value is None:
        return []

    if isinstance(raw_value, str):
        # Accept either "1,2,3" or a JSON-style string such as "[1, 2, 3]".
        text = raw_value.strip()
        if not text:
            return []

        if text.startswith("["):
            try:
                raw_value = json.loads(text)
            except json.JSONDecodeError:
                raw_value = text.split(",")
        else:
            raw_value = text.split(",")

    if not isinstance(raw_value, list):
        return []

    panel_ids: List[int] = []
    for item in raw_value:
        try:
            panel_ids.append(int(item))
        except (TypeError, ValueError):
            print(f"Ignoring invalid zone panel id: {item!r}")

    # Preserve order while removing duplicates.
    seen = set()
    result = []
    for panel_id in panel_ids:
        if panel_id not in seen:
            seen.add(panel_id)
            result.append(panel_id)

    return result


def parse_zone_brightness_multiplier(raw_value: Any) -> float:
    if raw_value in (None, ""):
        return 1.0

    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        print(f"Ignoring invalid zone brightness_multiplier: {raw_value!r}")
        return 1.0

    # Accept either 0.5 style or 50 style percentage input.
    if value > 1.0 and value <= 100.0:
        value = value / 100.0

    return max(0.0, min(1.0, value))


def parse_zone_max_brightness(raw_value: Any) -> int:
    if raw_value in (None, ""):
        return 255

    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        print(f"Ignoring invalid zone max_brightness: {raw_value!r}")
        return 255

    # Accept 0.0-1.0 as a fraction, 1-100 as a percentage, or 0-255 as HA brightness.
    if 0.0 <= value <= 1.0:
        value = value * 255.0
    elif 1.0 < value <= 100.0:
        value = (value / 100.0) * 255.0

    return bounded_int(value, minimum=0, maximum=255)


def zone_effective_max_brightness(zone: Zone) -> int:
    multiplier_cap = bounded_int(255 * zone.brightness_multiplier, minimum=0, maximum=255)
    return min(zone.max_brightness, multiplier_cap)


def scale_zone_brightness(zone: Zone, brightness: int | float) -> int:
    scaled = float(clamp(brightness)) * zone.brightness_multiplier
    return bounded_int(scaled, minimum=0, maximum=zone.max_brightness)


def load_zones_from_options(valid_panel_ids: set[int]) -> List[Zone]:
    raw_zones = ADDON_OPTIONS.get("zones", [])

    if isinstance(raw_zones, str):
        try:
            raw_zones = json.loads(raw_zones)
        except json.JSONDecodeError:
            print("Ignoring zones option because it was not valid JSON")
            return []

    if raw_zones is None:
        return []

    if not isinstance(raw_zones, list):
        print("Ignoring zones option because it is not a list")
        return []

    zones: List[Zone] = []
    used_ids: set[str] = set()

    for index, item in enumerate(raw_zones, start=1):
        if not isinstance(item, dict):
            print(f"Ignoring zone #{index}; expected an object")
            continue

        name = str(item.get("name") or f"Zone {index}")
        zone_id = slugify_identifier(str(item.get("id") or name))

        if zone_id in used_ids:
            base_zone_id = zone_id
            suffix = 2
            while f"{base_zone_id}_{suffix}" in used_ids:
                suffix += 1
            zone_id = f"{base_zone_id}_{suffix}"

        panel_ids = parse_panel_ids(item.get("panels"))
        unknown_panel_ids = [panel_id for panel_id in panel_ids if panel_id not in valid_panel_ids]
        panel_ids = [panel_id for panel_id in panel_ids if panel_id in valid_panel_ids]

        if unknown_panel_ids:
            print(
                f"Zone '{name}' references unknown Nanoleaf panels; ignoring: "
                f"{unknown_panel_ids}"
            )

        if not panel_ids:
            print(f"Ignoring zone '{name}' because it has no valid panels")
            continue

        brightness_multiplier = parse_zone_brightness_multiplier(
            item.get("brightness_multiplier", item.get("brightness_scale", item.get("multiplier")))
        )
        max_brightness = parse_zone_max_brightness(
            item.get("max_brightness", item.get("brightness_max"))
        )

        used_ids.add(zone_id)
        zones.append(
            Zone(
                zone_id=zone_id,
                name=name,
                panel_ids=panel_ids,
                brightness_multiplier=brightness_multiplier,
                max_brightness=max_brightness,
            )
        )

    return zones


def clamp(value: int | float, minimum: int = 0, maximum: int = 255) -> int:
    return max(minimum, min(maximum, int(round(float(value)))))


def xy_to_rgb(x: float, y: float) -> dict:
    """Convert CIE 1931 xy to sRGB at full brightness.

    The bridge stores colour separately from brightness. Rendering later applies
    the panel brightness, so this function intentionally returns an un-dimmed RGB
    colour.
    """
    x = max(0.0, min(1.0, float(x)))
    y = max(0.0001, min(1.0, float(y)))

    Y = 1.0
    X = (Y / y) * x
    Z = (Y / y) * (1.0 - x - y)

    r = X * 1.656492 - Y * 0.354851 - Z * 0.255038
    g = -X * 0.707196 + Y * 1.655397 + Z * 0.036152
    b = X * 0.051713 - Y * 0.121364 + Z * 1.011530

    r = max(0.0, r)
    g = max(0.0, g)
    b = max(0.0, b)

    max_component = max(r, g, b)
    if max_component > 1.0:
        r /= max_component
        g /= max_component
        b /= max_component

    def gamma_correct(component: float) -> int:
        if component <= 0.0031308:
            corrected = 12.92 * component
        else:
            corrected = 1.055 * (component ** (1.0 / 2.4)) - 0.055
        return clamp(corrected * 255)

    return {
        "r": gamma_correct(r),
        "g": gamma_correct(g),
        "b": gamma_correct(b),
    }


def extract_xy_from_payload(payload: dict) -> Optional[Tuple[float, float]]:
    if "xy_color" in payload and isinstance(payload["xy_color"], list) and len(payload["xy_color"]) >= 2:
        return float(payload["xy_color"][0]), float(payload["xy_color"][1])

    if "xy" in payload:
        value = payload["xy"]
        if isinstance(value, list) and len(value) >= 2:
            return float(value[0]), float(value[1])
        if isinstance(value, str) and "," in value:
            x_text, y_text = value.split(",", 1)
            return float(x_text), float(y_text)

    color = payload.get("color")
    if isinstance(color, dict):
        if "x" in color and "y" in color:
            return float(color["x"]), float(color["y"])
        if "xy" in color and isinstance(color["xy"], list) and len(color["xy"]) >= 2:
            return float(color["xy"][0]), float(color["xy"][1])

    return None


def parse_panel_orientation(raw_item: dict) -> int:
    """Extract panel orientation from the Nanoleaf layout item.

    Nanoleaf firmware/API variants may expose this as 'orientation', 'o',
    'rotation', or not at all. The bridge keeps using zero when unavailable.
    """
    for key in ("orientation", "o", "rotation", "rot"):
        if key in raw_item:
            try:
                return bounded_int(raw_item[key], minimum=0, maximum=359)
            except (TypeError, ValueError):
                pass
    return 0


def normalise_degrees(value: int | float) -> float:
    return float(value) % 360.0


def shape_kind_for_type(shape_type: Optional[int]) -> str:
    # Common Nanoleaf values: Shapes Hexagons=7, Shapes Triangles=8,
    # Shapes Mini Triangles=9. Older Light Panels are also triangular.
    if shape_type in {1, 2, 8}:
        return "triangle"
    if shape_type in {9}:
        return "mini_triangle"
    if shape_type in {3, 4}:
        return "square"
    return "hexagon"


def infer_panel_radius(panels: List[Panel]) -> float:
    distances: List[float] = []
    for index, a in enumerate(panels):
        for b in panels[index + 1:]:
            distance = math.hypot(a.x - b.x, a.y - b.y)
            if distance > 0:
                distances.append(distance)

    if not distances:
        return LAYOUT_PREVIEW_MIN_PANEL_RADIUS

    # The multiplier controls how large each drawn panel is relative to the
    # nearest Nanoleaf layout centre-to-centre distance. Increase it to reduce
    # visual gaps between panels; decrease it to create more separation.
    return max(
        LAYOUT_PREVIEW_MIN_PANEL_RADIUS,
        min(distances) * LAYOUT_PREVIEW_PANEL_RADIUS_MULTIPLIER,
    )


def regular_polygon_points(
    center_x: float,
    center_y: float,
    radius: float,
    sides: int,
    rotation_degrees: float,
) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    for index in range(sides):
        angle = math.radians(rotation_degrees + (360.0 * index / sides))
        points.append((
            center_x + radius * math.cos(angle),
            center_y + radius * math.sin(angle),
        ))
    return points


def panel_polygon_points(panel: Panel, radius: float) -> List[Tuple[float, float]]:
    # SVG has positive Y downward. Nanoleaf layout coordinates are easier to
    # reason about if positive Y is treated as upward, so invert here.
    center_x = float(panel.x)
    center_y = float(-panel.y)
    orientation = normalise_degrees(panel.orientation)
    shape_kind = shape_kind_for_type(panel.shape_type)

    if shape_kind == "triangle":
        return regular_polygon_points(center_x, center_y, radius * 1.05, 3, -90 + orientation)
    if shape_kind == "mini_triangle":
        return regular_polygon_points(center_x, center_y, radius * 0.68, 3, -90 + orientation)
    if shape_kind == "square":
        return regular_polygon_points(center_x, center_y, radius * 0.92, 4, 45 + orientation)

    return regular_polygon_points(center_x, center_y, radius, 6, 0 + orientation)


def svg_colour_for_panel_state(state: dict) -> str:
    state = normalise_panel_state(state)
    if state["state"] != "ON":
        return "#0f1115"

    brightness = state["brightness"] / 255.0
    color = state["color"]
    r = clamp(color["r"] * brightness)
    g = clamp(color["g"] * brightness)
    b = clamp(color["b"] * brightness)
    return f"#{r:02x}{g:02x}{b:02x}"


def panel_text_colour(state: dict) -> str:
    state = normalise_panel_state(state)
    if state["state"] != "ON":
        return "#d7dde5"

    brightness = state["brightness"] / 255.0
    color = state["color"]
    r = clamp(color["r"] * brightness)
    g = clamp(color["g"] * brightness)
    b = clamp(color["b"] * brightness)
    # Relative luminance approximation.
    return "#101418" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


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


def rgb_from_hex(value: str, fallback: str = "#ff66cc") -> dict:
    text = str(value or fallback).strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        text = str(fallback).strip().lstrip("#")

    try:
        return {
            "r": int(text[0:2], 16),
            "g": int(text[2:4], 16),
            "b": int(text[4:6], 16),
        }
    except Exception:
        return {"r": 255, "g": 102, "b": 204}


def rendered_color_for_state(state: dict) -> dict:
    state = normalise_panel_state(state)
    if state["state"] != "ON":
        return {"r": 0, "g": 0, "b": 0}

    brightness = state["brightness"] / 255.0
    color = state["color"]
    return {
        "r": clamp(color["r"] * brightness),
        "g": clamp(color["g"] * brightness),
        "b": clamp(color["b"] * brightness),
    }


def default_brush_state() -> dict:
    state = default_panel_state()
    state["state"] = "ON"
    state["brightness"] = PAINTER_BRUSH_DEFAULT_BRIGHTNESS
    state["color"] = rgb_from_hex(PAINTER_BRUSH_DEFAULT_COLOR)
    return normalise_panel_state(state)


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
                orientation=parse_panel_orientation(item),
            )
        )

    if not panels:
        raise RuntimeError("No controllable panels found in Nanoleaf layout response")

    return panels


# ----------------------------
# Bridge
# ----------------------------


class NanoleafBridge:
    def __init__(self, panels: List[Panel], zones: Optional[List[Zone]] = None):
        self.panels = panels
        self.panel_ids = [panel.panel_id for panel in panels]
        self.zones = zones or []
        self.zones_by_id = {zone.zone_id: zone for zone in self.zones}

        self.lock = threading.RLock()

        self.render_timer: Optional[threading.Timer] = None

        self.preview_timer: Optional[threading.Timer] = None

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

        # MQTT-discovered helper light used by the painter card as a native
        # Home Assistant colour/brightness picker. It does not render by itself.
        self.brush_state: dict = default_brush_state()

        # Nanoleaf whole-device state.
        self.device_power_on: Optional[bool] = None
        self.device_brightness: Optional[int] = None

        # Native Nanoleaf effects.
        self.effects: List[str] = []
        self.active_effect: Optional[str] = None

        # Bridge-side pseudo effects, such as brightness-only sparkle.
        # These do not overwrite self.state; they are applied as a render-time
        # overlay so the selected base colours remain editable.
        self.bridge_effect: Optional[str] = None
        self.sparkle_multipliers: Dict[int, float] = {}
        self.sparkle_next_update = 0.0

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
        print(f"Bridge effects enabled: {ENABLE_BRIDGE_EFFECTS}")
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

            if self.preview_timer is not None:
                self.preview_timer.cancel()
                self.preview_timer = None

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
        client.subscribe(f"{BASE_TOPIC}/zone/+/set")
        if ENABLE_PAINTER_BRUSH:
            client.subscribe(f"{BASE_TOPIC}/brush/set")
        client.subscribe("homeassistant/status")

        if SYNC_GLOBAL_STATE:
            self.refresh_global_state(publish=False)

        if ENABLE_NANOLEAF_EFFECTS:
            self.refresh_effects(publish=False)

        self.publish_discovery()
        self.publish_layout_json()
        self.publish_all_states()
        self.schedule_preview_publish(immediate=True)

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
            self.publish_layout_json()
            self.publish_all_states()
            self.schedule_preview_publish(immediate=True)
            return

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            state_payload = payload_raw.strip().upper()
            if state_payload in {"ON", "OFF"}:
                payload = {"state": state_payload}
            else:
                print(f"Invalid JSON on {topic}: {payload_raw}")
                return

        try:
            if topic == f"{BASE_TOPIC}/set":
                self.handle_whole_light_command(payload)
            elif topic.startswith(f"{BASE_TOPIC}/panel/") and topic.endswith("/set"):
                panel_id = int(topic.split("/")[-2])
                self.handle_panel_command(panel_id, payload)
            elif topic.startswith(f"{BASE_TOPIC}/zone/") and topic.endswith("/set"):
                zone_id = topic.split("/")[-2]
                self.handle_zone_command(zone_id, payload)
            elif topic == f"{BASE_TOPIC}/brush/set":
                self.handle_brush_command(payload)
        except Exception as exc:
            print(f"Error handling MQTT command on {topic}: {exc}")

    # ----------------------------
    # Command handling
    # ----------------------------

    def handle_brush_command(self, payload: dict):
        if not ENABLE_PAINTER_BRUSH:
            return

        with self.lock:
            self.apply_payload_to_brush(payload)

        self.publish_brush_state()

    def apply_payload_to_brush(self, payload: dict):
        brush_state = self.brush_state

        if "state" in payload:
            state_value = str(payload["state"]).upper()
            brush_state["state"] = "ON" if state_value == "ON" else "OFF"
        elif "on" in payload:
            brush_state["state"] = "ON" if bool(payload["on"]) else "OFF"

        if "brightness" in payload:
            brush_state["brightness"] = clamp(payload["brightness"])
        elif "bri" in payload:
            brush_state["brightness"] = bounded_int(
                (float(payload["bri"]) / 254.0) * 255.0,
                minimum=0,
                maximum=255,
            )

        xy_value = None
        try:
            xy_value = extract_xy_from_payload(payload)
        except (TypeError, ValueError) as exc:
            print(f"Ignoring invalid brush xy colour payload: {payload!r}: {exc}")

        if xy_value is not None:
            x, y = xy_value
            brush_state["color"] = xy_to_rgb(x, y)
        elif "color" in payload:
            color = payload["color"]
            if isinstance(color, dict):
                brush_state["color"] = {
                    "r": clamp(color.get("r", brush_state["color"]["r"])),
                    "g": clamp(color.get("g", brush_state["color"]["g"])),
                    "b": clamp(color.get("b", brush_state["color"]["b"])),
                }

        if "transition" in payload:
            try:
                brush_state["transition"] = bounded_int(
                    float(payload["transition"]) * 10,
                    minimum=0,
                    maximum=65535,
                )
            except (TypeError, ValueError):
                print(f"Ignoring invalid brush transition value: {payload['transition']}")

        if "brightness" in payload or "bri" in payload or "color" in payload or xy_value is not None:
            brush_state["state"] = "ON"

        brush_state["color_mode"] = "rgb"
        self.brush_state = normalise_panel_state(brush_state)

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

            if effect_name == BRIDGE_EFFECT_NAME:
                print("Switching All Panels back to bridge framebuffer mode")
                with self.lock:
                    self.active_effect = None
                    self.bridge_effect = None
                    self.sparkle_multipliers.clear()

            elif self.is_bridge_effect_name(effect_name):
                self.handle_bridge_effect_command(effect_name, payload)
                return

            elif effect_name:
                self.handle_effect_command(effect_name, payload)
                return

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
        self.schedule_preview_publish()
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
        self.publish_framebuffer_json()
        self.publish_global_attributes()
        self.schedule_preview_publish()
        self.schedule_render()

    def scaled_zone_payload(self, zone: Zone, payload: dict) -> dict:
        """Return a copy of a zone command with brightness limited for this zone.

        diyHue/Hue Sync can keep sending full-brightness updates. Applying the
        multiplier here means the Nanoleaf panels are dimmed without changing
        the colour data or relying on the Hue app brightness balancer.
        """
        adjusted = copy.deepcopy(payload)

        if "brightness" in adjusted:
            adjusted["brightness"] = scale_zone_brightness(zone, adjusted["brightness"])
        elif "bri" in adjusted:
            # Convert Hue 1-254 brightness into HA 0-255, then scale it.
            try:
                brightness_255 = (float(adjusted["bri"]) / 254.0) * 255.0
                adjusted["brightness"] = scale_zone_brightness(zone, brightness_255)
                adjusted.pop("bri", None)
            except (TypeError, ValueError):
                pass

        return adjusted

    def enforce_zone_brightness_limit(self, zone: Zone, panel_id: int):
        """Clamp existing panel brightness so colour-only zone commands stay dim."""
        if panel_id not in self.state:
            return

        panel_state = normalise_panel_state(self.state[panel_id])
        panel_state["brightness"] = min(
            panel_state["brightness"],
            zone_effective_max_brightness(zone),
        )
        self.state[panel_id] = panel_state

    def handle_zone_command(self, zone_id: str, payload: dict):
        zone = self.zones_by_id.get(zone_id)
        if zone is None:
            print(f"Ignoring command for unknown zone {zone_id}")
            return

        adjusted_payload = self.scaled_zone_payload(zone, payload)

        with self.lock:
            for panel_id in zone.panel_ids:
                if panel_id not in self.state:
                    print(f"Ignoring zone panel {panel_id}; not in current Nanoleaf layout")
                    continue

                self.apply_payload_to_panel(panel_id, adjusted_payload)
                self.enforce_zone_brightness_limit(zone, panel_id)

            # Any zone command exits native Nanoleaf effect mode.
            self.active_effect = None

            self.update_optimistic_device_power_from_framebuffer()
            self.save_state()

        self.publish_zone_state(zone)
        for panel_id in zone.panel_ids:
            if panel_id in self.state:
                self.publish_panel_state(panel_id)
        self.publish_whole_state()
        self.publish_framebuffer_json()
        self.publish_global_attributes()
        self.schedule_preview_publish()
        self.schedule_render()

    def is_bridge_effect_name(self, effect_name: Optional[str]) -> bool:
        if not ENABLE_BRIDGE_EFFECTS or not effect_name:
            return False

        return str(effect_name) == SPARKLE_EFFECT_NAME

    def handle_bridge_effect_command(self, effect_name: str, payload: dict):
        if not self.is_bridge_effect_name(effect_name):
            return

        payload_without_effect = {
            key: value for key, value in payload.items() if key != "effect"
        }

        with self.lock:
            # Bridge pseudo effects and native Nanoleaf effects are mutually
            # exclusive. Sparkle keeps the existing framebuffer colours and only
            # varies brightness as a render-time overlay.
            self.active_effect = None
            self.bridge_effect = effect_name
            self.sparkle_multipliers.clear()
            self.sparkle_next_update = 0.0

            if payload_without_effect:
                for panel_id in self.panel_ids:
                    self.apply_payload_to_panel(panel_id, payload_without_effect)

            self.update_optimistic_device_power_from_framebuffer()
            self.save_state()

        if not USE_STREAMING:
            print(
                f"{effect_name} works best with output_mode=stream; "
                "REST mode can only render static sparkle frames."
            )

        self.publish_all_states()
        self.publish_global_attributes()
        self.schedule_preview_publish()
        self.schedule_render()

    def handle_effect_command(self, effect_name: str, payload: dict):
        if effect_name not in self.effects:
            print(
                f"Effect '{effect_name}' was not in cached effect list; trying anyway"
            )

        # Native Nanoleaf effect mode and bridge streaming mode are mutually exclusive.
        self.stop_streaming()

        with self.lock:
            self.bridge_effect = None
            self.sparkle_multipliers.clear()
            self.sparkle_next_update = 0.0

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
        elif "on" in payload:
            panel_state["state"] = "ON" if bool(payload["on"]) else "OFF"

        if "brightness" in payload:
            panel_state["brightness"] = clamp(payload["brightness"])
        elif "bri" in payload:
            # Hue-style brightness is usually 1-254; normalise to HA's 0-255 scale.
            panel_state["brightness"] = bounded_int(
                (float(payload["bri"]) / 254.0) * 255.0,
                minimum=0,
                maximum=255,
            )

        xy_value = None
        try:
            xy_value = extract_xy_from_payload(payload)
        except (TypeError, ValueError) as exc:
            print(f"Ignoring invalid xy colour payload: {payload!r}: {exc}")

        if xy_value is not None:
            x, y = xy_value
            panel_state["color"] = xy_to_rgb(x, y)
        elif "color" in payload:
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

        # HA often sends brightness/colour without explicit state.
        if "brightness" in payload or "bri" in payload or "color" in payload or xy_value is not None:
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

        if ENABLE_BRIDGE_EFFECTS and SPARKLE_EFFECT_NAME not in effects:
            effects.append(SPARKLE_EFFECT_NAME)

        with self.lock:
            active_effect = self.active_effect
            bridge_effect = self.bridge_effect
            cached_effects = list(self.effects)

        if bridge_effect and bridge_effect not in effects:
            effects.append(bridge_effect)

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
            # If bridge streaming is active, keep reporting the active bridge
            # framebuffer/effect rather than a native Nanoleaf extControl effect.
            if self.is_streaming_locked():
                self.active_effect = None
            else:
                self.active_effect = selected
                if selected:
                    self.bridge_effect = None
                    self.sparkle_multipliers.clear()
                    self.sparkle_next_update = 0.0

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
        state_source = copy.deepcopy(state_snapshot or self.state)
        state_source = self.apply_bridge_effect_to_snapshot(state_source)

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
        state_source = copy.deepcopy(state_snapshot or self.state)
        state_source = self.apply_bridge_effect_to_snapshot(state_source)

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

                state_snapshot = self.apply_bridge_effect_to_snapshot(state_snapshot)
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

    def apply_bridge_effect_to_snapshot(
        self,
        state_snapshot: Dict[int, dict],
    ) -> Dict[int, dict]:
        with self.lock:
            bridge_effect = self.bridge_effect

        if bridge_effect == SPARKLE_EFFECT_NAME:
            return self.apply_sparkle_effect_to_snapshot(state_snapshot)

        return state_snapshot

    def apply_sparkle_effect_to_snapshot(
        self,
        state_snapshot: Dict[int, dict],
    ) -> Dict[int, dict]:
        now = time.monotonic()

        with self.lock:
            should_update = now >= self.sparkle_next_update

            if should_update:
                smoothing = SPARKLE_SMOOTHING
                step = 1.0 - smoothing

                for panel_id in self.panel_ids:
                    panel_state = normalise_panel_state(
                        state_snapshot.get(panel_id, default_panel_state())
                    )

                    if panel_state["state"] != "ON":
                        self.sparkle_multipliers.pop(panel_id, None)
                        continue

                    target = random.uniform(
                        SPARKLE_MIN_MULTIPLIER,
                        SPARKLE_MAX_MULTIPLIER,
                    )
                    current = self.sparkle_multipliers.get(panel_id, target)
                    self.sparkle_multipliers[panel_id] = (
                        current + ((target - current) * step)
                    )

                self.sparkle_next_update = now + SPARKLE_INTERVAL_SECONDS

            multipliers = dict(self.sparkle_multipliers)

        for panel_id, multiplier in multipliers.items():
            if panel_id not in state_snapshot:
                continue

            panel_state = normalise_panel_state(state_snapshot[panel_id])
            if panel_state["state"] != "ON":
                continue

            panel_state["brightness"] = clamp(panel_state["brightness"] * multiplier)
            state_snapshot[panel_id] = panel_state

        return state_snapshot

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
    # Layout JSON and SVG preview
    # ----------------------------

    def build_layout_payload(self) -> dict:
        radius = infer_panel_radius(self.panels)
        panel_items = []

        for panel in self.panels:
            polygon = panel_polygon_points(panel, radius)
            panel_items.append(
                {
                    "panel_id": panel.panel_id,
                    "x": panel.x,
                    "y": panel.y,
                    "shape_type": panel.shape_type,
                    "shape": shape_kind_for_type(panel.shape_type),
                    "orientation": panel.orientation,
                    "polygon": [
                        {"x": round(x, 3), "y": round(y, 3)}
                        for x, y in polygon
                    ],
                    "mqtt_command_topic": f"{BASE_TOPIC}/panel/{panel.panel_id}/set",
                    "mqtt_state_topic": f"{BASE_TOPIC}/panel/{panel.panel_id}/state",
                }
            )

        all_points = [
            (point["x"], point["y"])
            for item in panel_items
            for point in item["polygon"]
        ]
        if all_points:
            min_x = min(x for x, _ in all_points)
            max_x = max(x for x, _ in all_points)
            min_y = min(y for _, y in all_points)
            max_y = max(y for _, y in all_points)
        else:
            min_x = min_y = 0
            max_x = max_y = 1

        return {
            "device_id": DEVICE_ID,
            "device_name": DEVICE_NAME,
            "base_topic": BASE_TOPIC,
            "panel_count": len(panel_items),
            "panels": panel_items,
            "zones": [
                {
                    "id": zone.zone_id,
                    "name": zone.name,
                    "panel_ids": zone.panel_ids,
                    "brightness_multiplier": zone.brightness_multiplier,
                    "max_brightness": zone.max_brightness,
                    "effective_max_brightness": zone_effective_max_brightness(zone),
                    "mqtt_command_topic": f"{BASE_TOPIC}/zone/{zone.zone_id}/set",
                    "mqtt_state_topic": f"{BASE_TOPIC}/zone/{zone.zone_id}/state",
                }
                for zone in self.zones
            ],
            "preview": {
                "panel_radius": round(radius, 3),
                "panel_radius_multiplier": LAYOUT_PREVIEW_PANEL_RADIUS_MULTIPLIER,
                "min_panel_radius": LAYOUT_PREVIEW_MIN_PANEL_RADIUS,
                "padding": LAYOUT_PREVIEW_PADDING,
            },
            "bounds": {
                "min_x": round(min_x, 3),
                "max_x": round(max_x, 3),
                "min_y": round(min_y, 3),
                "max_y": round(max_y, 3),
                "width": round(max_x - min_x, 3),
                "height": round(max_y - min_y, 3),
            },
        }

    def publish_layout_json(self):
        payload = self.build_layout_payload()

        self.client.publish(
            f"{BASE_TOPIC}/layout",
            json.dumps(payload, separators=(",", ":")),
            retain=True,
        )
        self.client.publish(
            f"{BASE_TOPIC}/layout/state",
            str(payload["panel_count"]),
            retain=True,
        )

    def build_framebuffer_payload(self) -> dict:
        with self.lock:
            state_snapshot = copy.deepcopy(self.state)
            device_power_on = self.device_power_on
            active_effect = self.active_effect
            bridge_effect = self.bridge_effect

        panels = {}
        for panel in self.panels:
            state = normalise_panel_state(
                state_snapshot.get(panel.panel_id, default_panel_state())
            )
            panels[str(panel.panel_id)] = {
                "panel_id": panel.panel_id,
                "state": state["state"],
                "color_mode": state["color_mode"],
                "brightness": state["brightness"],
                "color": state["color"],
                "rendered_color": rendered_color_for_state(state),
                "transition": state["transition"],
                "mqtt_command_topic": f"{BASE_TOPIC}/panel/{panel.panel_id}/set",
                "mqtt_state_topic": f"{BASE_TOPIC}/panel/{panel.panel_id}/state",
            }

        return {
            "device_id": DEVICE_ID,
            "device_name": DEVICE_NAME,
            "base_topic": BASE_TOPIC,
            "panel_count": len(panels),
            "updated_at": int(time.time()),
            "nanoleaf_global_power": device_power_on,
            "active_effect": active_effect,
            "bridge_effect": bridge_effect,
            "panels": panels,
        }

    def publish_framebuffer_json(self):
        payload = self.build_framebuffer_payload()

        self.client.publish(
            f"{BASE_TOPIC}/framebuffer",
            json.dumps(payload, separators=(",", ":")),
            retain=True,
        )
        self.client.publish(
            f"{BASE_TOPIC}/framebuffer/state",
            str(payload["updated_at"]),
            retain=True,
        )

    def build_layout_svg(self, state_snapshot: Optional[Dict[int, dict]] = None) -> str:
        state_source = copy.deepcopy(state_snapshot or self.state)
        state_source = self.apply_bridge_effect_to_snapshot(state_source)
        radius = infer_panel_radius(self.panels)

        panel_shapes = []
        all_points: List[Tuple[float, float]] = []

        for panel in self.panels:
            points = panel_polygon_points(panel, radius)
            all_points.extend(points)
            panel_shapes.append((panel, points))

        if all_points:
            min_x = min(x for x, _ in all_points) - LAYOUT_PREVIEW_PADDING
            max_x = max(x for x, _ in all_points) + LAYOUT_PREVIEW_PADDING
            min_y = min(y for _, y in all_points) - LAYOUT_PREVIEW_PADDING
            max_y = max(y for _, y in all_points) + LAYOUT_PREVIEW_PADDING
        else:
            min_x = min_y = 0
            max_x = max_y = 100

        width = max(1.0, max_x - min_x)
        height = max(1.0, max_y - min_y)
        font_size = max(8.0, radius * 0.22)
        escaped_name = html.escape(DEVICE_NAME)

        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" '
                f'viewBox="{min_x:.3f} {min_y:.3f} {width:.3f} {height:.3f}" '
                f'width="{width:.0f}" height="{height:.0f}" '
                f'role="img" aria-label="{escaped_name} panel preview">'
            ),
            f'<rect x="{min_x:.3f}" y="{min_y:.3f}" width="{width:.3f}" height="{height:.3f}" fill="{html.escape(LAYOUT_PREVIEW_BACKGROUND)}"/>',
        ]

        for panel, points in panel_shapes:
            state = normalise_panel_state(state_source.get(panel.panel_id, default_panel_state()))
            fill = svg_colour_for_panel_state(state)
            text_colour = panel_text_colour(state)
            point_text = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
            center_x = float(panel.x)
            center_y = float(-panel.y)
            panel_id_text = html.escape(str(panel.panel_id))
            shape_name = html.escape(shape_kind_for_type(panel.shape_type))

            parts.append(
                f'<g id="panel-{panel.panel_id}" data-panel-id="{panel.panel_id}" '
                f'data-shape-type="{html.escape(str(panel.shape_type))}" '
                f'data-shape="{shape_name}" data-orientation="{panel.orientation}">'
            )
            parts.append(
                f'<title>Panel {panel_id_text}</title>'
            )
            parts.append(
                f'<polygon points="{point_text}" fill="{fill}" '
                f'stroke="{html.escape(LAYOUT_PREVIEW_STROKE)}" stroke-width="3" '
                f'stroke-linejoin="round"/>'
            )

            if LAYOUT_PREVIEW_LABELS:
                parts.append(
                    f'<text x="{center_x:.3f}" y="{center_y:.3f}" '
                    f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif" '
                    f'font-size="{font_size:.2f}" font-weight="700" '
                    f'text-anchor="middle" dominant-baseline="central" '
                    f'fill="{text_colour}">{panel_id_text}</text>'
                )

            parts.append('</g>')

        parts.append('</svg>')
        return "".join(parts)

    def schedule_preview_publish(self, immediate: bool = False):
        if not ENABLE_LAYOUT_PREVIEW:
            return

        if immediate or LAYOUT_PREVIEW_DEBOUNCE_SECONDS == 0:
            self.publish_preview_image()
            return

        with self.lock:
            if self.preview_timer is not None:
                self.preview_timer.cancel()

            self.preview_timer = threading.Timer(
                LAYOUT_PREVIEW_DEBOUNCE_SECONDS,
                self.publish_preview_image,
            )
            self.preview_timer.daemon = True
            self.preview_timer.start()

    def publish_preview_image(self):
        if not ENABLE_LAYOUT_PREVIEW:
            return

        with self.lock:
            self.preview_timer = None
            state_snapshot = copy.deepcopy(self.state)

        try:
            svg = self.build_layout_svg(state_snapshot)
        except Exception as exc:
            print(f"Could not render layout preview SVG: {exc}")
            return

        self.client.publish(
            f"{BASE_TOPIC}/preview/image",
            svg.encode("utf-8"),
            retain=True,
        )
        self.client.publish(
            f"{BASE_TOPIC}/preview/state",
            str(int(time.time())),
            retain=True,
        )

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
                "shape": shape_kind_for_type(panel.shape_type),
                "orientation": panel.orientation,
            }

            self.client.publish(
                f"{BASE_TOPIC}/panel/{panel.panel_id}/attributes",
                json.dumps(attrs),
                retain=True,
            )

        for zone in self.zones:
            unique_id = f"{DEVICE_ID}_zone_{zone.zone_id}"
            zone_panel_unique_ids = [
                f"{DEVICE_ID}_panel_{panel_id}" for panel_id in zone.panel_ids
            ]

            zone_config = {
                "name": zone.name,
                "unique_id": unique_id,
                "schema": "json",
                "command_topic": f"{BASE_TOPIC}/zone/{zone.zone_id}/set",
                "state_topic": f"{BASE_TOPIC}/zone/{zone.zone_id}/state",
                "json_attributes_topic": f"{BASE_TOPIC}/zone/{zone.zone_id}/attributes",
                "availability_topic": f"{BASE_TOPIC}/status",
                "brightness": True,
                "supported_color_modes": ["rgb", "xy"],
                "optimistic": False,
                "device": device,
                "group": zone_panel_unique_ids,
                "icon": "mdi:shape-polygon-plus",
            }

            self.client.publish(
                f"{DISCOVERY_PREFIX}/light/{unique_id}/config",
                json.dumps(zone_config),
                retain=True,
            )

            attrs = {
                "zone_id": zone.zone_id,
                "panel_ids": zone.panel_ids,
                "panel_count": len(zone.panel_ids),
                "brightness_multiplier": zone.brightness_multiplier,
                "max_brightness": zone.max_brightness,
                "effective_max_brightness": zone_effective_max_brightness(zone),
            }

            self.client.publish(
                f"{BASE_TOPIC}/zone/{zone.zone_id}/attributes",
                json.dumps(attrs),
                retain=True,
            )

        layout_sensor_config = {
            "name": "Layout",
            "unique_id": f"{DEVICE_ID}_layout",
            "state_topic": f"{BASE_TOPIC}/layout/state",
            "json_attributes_topic": f"{BASE_TOPIC}/layout",
            "availability_topic": f"{BASE_TOPIC}/status",
            "device": device,
            "icon": "mdi:vector-polygon",
        }

        self.client.publish(
            f"{DISCOVERY_PREFIX}/sensor/{DEVICE_ID}_layout/config",
            json.dumps(layout_sensor_config),
            retain=True,
        )

        framebuffer_sensor_config = {
            "name": "Framebuffer",
            "unique_id": f"{DEVICE_ID}_framebuffer",
            "state_topic": f"{BASE_TOPIC}/framebuffer/state",
            "json_attributes_topic": f"{BASE_TOPIC}/framebuffer",
            "availability_topic": f"{BASE_TOPIC}/status",
            "device": device,
            "icon": "mdi:memory",
        }

        self.client.publish(
            f"{DISCOVERY_PREFIX}/sensor/{DEVICE_ID}_framebuffer/config",
            json.dumps(framebuffer_sensor_config),
            retain=True,
        )

        if ENABLE_PAINTER_BRUSH:
            brush_config = {
                "name": PAINTER_BRUSH_NAME,
                "unique_id": f"{DEVICE_ID}_painter_brush",
                "schema": "json",
                "command_topic": f"{BASE_TOPIC}/brush/set",
                "state_topic": f"{BASE_TOPIC}/brush/state",
                "json_attributes_topic": f"{BASE_TOPIC}/brush/attributes",
                "availability_topic": f"{BASE_TOPIC}/status",
                "brightness": True,
                "supported_color_modes": ["rgb", "xy"],
                "optimistic": False,
                "device": device,
                "icon": "mdi:brush-variant",
            }

            self.client.publish(
                f"{DISCOVERY_PREFIX}/light/{DEVICE_ID}_painter_brush/config",
                json.dumps(brush_config),
                retain=True,
            )
            self.publish_brush_state()

        if ENABLE_LAYOUT_PREVIEW:
            preview_config = {
                "name": "Panel Preview",
                "unique_id": f"{DEVICE_ID}_panel_preview",
                "image_topic": f"{BASE_TOPIC}/preview/image",
                "content_type": "image/svg+xml",
                "availability_topic": f"{BASE_TOPIC}/status",
                "device": device,
                "icon": "mdi:image-filter-center-focus",
            }

            self.client.publish(
                f"{DISCOVERY_PREFIX}/image/{DEVICE_ID}_panel_preview/config",
                json.dumps(preview_config),
                retain=True,
            )

        self.publish_global_attributes()

        print(
            f"Published MQTT discovery for {len(self.panels)} panels, "
            f"{len(self.zones)} zones, plus whole-light, layout, framebuffer, "
            "brush, and preview entities"
        )

    def publish_all_states(self):
        for panel_id in self.panel_ids:
            self.publish_panel_state(panel_id)

        for zone in self.zones:
            self.publish_zone_state(zone)

        self.publish_whole_state()
        self.publish_framebuffer_json()
        self.schedule_preview_publish()

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

    def aggregate_panel_ids_state(self, panel_ids: List[int]) -> dict:
        with self.lock:
            device_power_on = self.device_power_on
            state_values = [
                normalise_panel_state(self.state[panel_id])
                for panel_id in panel_ids
                if panel_id in self.state
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
        }

        if not state_values:
            return off_state

        if PUBLISH_EFFECTIVE_OFF_WHEN_GLOBAL_OFF and device_power_on is False:
            return off_state

        on_panels = [state for state in state_values if state["state"] == "ON"]
        if not on_panels:
            return off_state

        brightness = round(
            sum(panel["brightness"] for panel in on_panels) / len(on_panels)
        )

        color = {
            "r": round(sum(panel["color"]["r"] for panel in on_panels) / len(on_panels)),
            "g": round(sum(panel["color"]["g"] for panel in on_panels) / len(on_panels)),
            "b": round(sum(panel["color"]["b"] for panel in on_panels) / len(on_panels)),
        }

        transition = round(
            sum(
                panel.get("transition", STREAM_TRANSITION_TIME)
                for panel in on_panels
            )
            / len(on_panels)
        )

        return {
            "state": "ON",
            "color_mode": "rgb",
            "brightness": brightness,
            "color": color,
            "transition": bounded_int(transition, minimum=0, maximum=65535),
        }

    def publish_panel_state(self, panel_id: int):
        payload = json.dumps(self.effective_panel_state(panel_id))

        self.client.publish(
            f"{BASE_TOPIC}/panel/{panel_id}/state",
            payload,
            retain=True,
        )

    def publish_zone_state(self, zone: Zone):
        payload = json.dumps(self.aggregate_panel_ids_state(zone.panel_ids))

        self.client.publish(
            f"{BASE_TOPIC}/zone/{zone.zone_id}/state",
            payload,
            retain=True,
        )

    def publish_brush_state(self):
        if not ENABLE_PAINTER_BRUSH:
            return

        with self.lock:
            brush_state = self.state_for_mqtt(self.brush_state)

        self.client.publish(
            f"{BASE_TOPIC}/brush/state",
            json.dumps(brush_state),
            retain=True,
        )

        attrs = {
            "purpose": "Colour and brightness source for the Nanoleaf panel painter card",
            "does_not_render_directly": True,
            "framebuffer_topic": f"{BASE_TOPIC}/framebuffer",
        }
        self.client.publish(
            f"{BASE_TOPIC}/brush/attributes",
            json.dumps(attrs),
            retain=True,
        )

    def publish_whole_state(self):
        with self.lock:
            device_power_on = self.device_power_on
            device_brightness = self.device_brightness
            active_effect = self.active_effect
            bridge_effect = self.bridge_effect
            reported_effect = active_effect or bridge_effect or BRIDGE_EFFECT_NAME
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
            "effect": reported_effect,
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
                    "effect": reported_effect,
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
                "active_effect": self.active_effect or self.bridge_effect or BRIDGE_EFFECT_NAME,
                "native_effect": self.active_effect,
                "bridge_effect": self.bridge_effect,
                "bridge_effects_enabled": ENABLE_BRIDGE_EFFECTS,
                "sparkle_effect_name": SPARKLE_EFFECT_NAME,
                "sparkle_interval_seconds": SPARKLE_INTERVAL_SECONDS,
                "sparkle_min_multiplier": SPARKLE_MIN_MULTIPLIER,
                "sparkle_max_multiplier": SPARKLE_MAX_MULTIPLIER,
                "sparkle_smoothing": SPARKLE_SMOOTHING,
                "effect_count": len(self.effects),
                "effects": self.get_effect_list_for_ha(),
                "zone_count": len(self.zones),
                "zones": [
                    {
                        "id": zone.zone_id,
                        "name": zone.name,
                        "panel_ids": zone.panel_ids,
                        "brightness_multiplier": zone.brightness_multiplier,
                        "max_brightness": zone.max_brightness,
                        "effective_max_brightness": zone_effective_max_brightness(zone),
                    }
                    for zone in self.zones
                ],
                "layout_topic": f"{BASE_TOPIC}/layout",
                "framebuffer_topic": f"{BASE_TOPIC}/framebuffer",
                "preview_image_topic": f"{BASE_TOPIC}/preview/image",
                "layout_preview_enabled": ENABLE_LAYOUT_PREVIEW,
                "layout_preview_panel_radius_multiplier": LAYOUT_PREVIEW_PANEL_RADIUS_MULTIPLIER,
                "layout_preview_min_panel_radius": LAYOUT_PREVIEW_MIN_PANEL_RADIUS,
                "painter_brush_enabled": ENABLE_PAINTER_BRUSH,
                "painter_brush_name": PAINTER_BRUSH_NAME,
                "painter_brush_command_topic": f"{BASE_TOPIC}/brush/set",
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
            f"x={panel.x}, y={panel.y}, shape_type={panel.shape_type}, "
            f"orientation={panel.orientation}"
        )

    zones = load_zones_from_options({panel.panel_id for panel in panels})
    if zones:
        print("Configured zones:")
        for zone in zones:
            print(
                f"  {zone.zone_id} ({zone.name}): {zone.panel_ids} "
                f"brightness_multiplier={zone.brightness_multiplier}, "
                f"max_brightness={zone.max_brightness}, "
                f"effective_max={zone_effective_max_brightness(zone)}"
            )
    else:
        print("No configured zones; only All Panels and individual panel lights will be exposed")

    bridge = NanoleafBridge(panels, zones=zones)
    bridge.start()


if __name__ == "__main__":
    main()
