# Nanoleaf Panel Bridge

A bridge to expose Nanoleaf Shapes panels as individual panels in Home Assistant as MQTT lights.

[![Open your Home Assistant instance and show the add app repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fluka-kovac%2Fnanoleaf-panel-bridge)

## Usage

Install as a repository in Home Assitant and install the App.

Ensure you have an MQTT broker (tested with Mosquitto in HA).

You will need to edit the config as YAML.

Ensure you have a token for your Nanoleaf Shapes panel by holding down the power button on the Shapes for 5-7 seconds until the lights flash, then running `curl -X POST http://xxx.xxx.xxx.xxx:16021/api/v1/new` with the IP address of your Shapes.

### Options

| Configurable Options         | Description                                                     |
| ---------------------------- |:---------------------------------------------------------------:|
| output_mode                  | stream UDP or rest REST mode                                    |
| stream_fps                   | Number of UDP frames to send per second                         |
| stream_transition_time       | Transition setting of Nanoleaf API (in 10ms)                    |
| render_on_startup            | true or false                                                   |
| global_brightness_value      | 0-100 set overrides default Nanoleaf integration brightness max |
| sync_global_state            | Override default Nanoleaf integration if it makes changes       |
| global_sync_interval_seconds | How often to override default Nanoleaf integration              |
| brightness_multiplier        | 0-1 multiplier for how bright each zone can get                 |
| max_brightness               | 0-1 set max absolute brightness for each zone                   |

Example config file:

```
nanoleaf_ip: xxx.xxx.xxx.xxx
nanoleaf_token: TOKEN
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_user: USERNAME
mqtt_password:PASSWORD
output_mode: stream
stream_fps: 25
stream_transition_time: 1
render_on_startup: false
global_brightness_value: 100
sync_global_state: true
global_sync_interval_seconds: 5
zones:
  - name: Top Row
    id: top_row_panels
    panels:
      - 13475
      - 27059
      - 63775
      - 62872
      - 7593
    brightness_multiplier: 0.5
    max_brightness: 1
  - name: Bottom Row
    id: bottom_row_panels
    panels:
      - 36421
      - 48544
      - 19012
      - 19960
    brightness_multiplier: 1
    max_brightness: 1
```

**Please note this was created with the use of AI, my programming skills are not this good.**
