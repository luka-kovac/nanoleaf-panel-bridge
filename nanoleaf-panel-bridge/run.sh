#!/usr/bin/env sh
set -e

export NANOLEAF_IP="$(jq -r '.nanoleaf_ip' /data/options.json)"
export NANOLEAF_TOKEN="$(jq -r '.nanoleaf_token' /data/options.json)"

export MQTT_HOST="$(jq -r '.mqtt_host' /data/options.json)"
export MQTT_PORT="$(jq -r '.mqtt_port' /data/options.json)"
export MQTT_USER="$(jq -r '.mqtt_user' /data/options.json)"
export MQTT_PASS="$(jq -r '.mqtt_password' /data/options.json)"

export OUTPUT_MODE="$(jq -r '.output_mode' /data/options.json)"
export STREAM_FPS="$(jq -r '.stream_fps' /data/options.json)"
export STREAM_TRANSITION_TIME="$(jq -r '.stream_transition_time' /data/options.json)"

export RENDER_ON_STARTUP="$(jq -r '.render_on_startup' /data/options.json)"
export GLOBAL_BRIGHTNESS_VALUE="$(jq -r '.global_brightness_value' /data/options.json)"
export SYNC_GLOBAL_STATE="$(jq -r '.sync_global_state' /data/options.json)"
export GLOBAL_SYNC_INTERVAL_SECONDS="$(jq -r '.global_sync_interval_seconds' /data/options.json)"

# Persist state inside the add-on data directory.
export STATE_FILE="/data/nanoleaf_panel_state.json"

python /app/bridge.py
