# Nanoleaf Panel Bridge

A bridge to expose Nanoleaf Shapes panels as individual panels in Home Assistant as MQTT lights.

[![Open your Home Assistant instance and show the add app repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fluka-kovac%2Fnanoleaf-panel-bridge)

## Usage

Install as a repository in Home Assistant and install the App.

Ensure you have an MQTT broker (tested with Mosquitto in HA).

You will need to edit the config as YAML.

Ensure you have a token for your Nanoleaf Shapes panel by holding down the power button on the Shapes for 5-7 seconds until the lights flash, then running `curl -X POST http://xxx.xxx.xxx.xxx:16021/api/v1/new` with the IP address of your Shapes.

## Notes

This add-on will expose Nanoleaf Shapes as MQTT lights for Home Assistant as both individual panels with the individual panel ID ("Nanoleaf Shapes Panel ####') for per-panel control, and as a single "full" light like the default integration, allowing saved effects (Scenes) to be used.

A framebuffer is saved to store the per-panel settings and make it look pretty in HA and restore state as needed.

The default control mode of Nanoleaf Shapes is as an effect with extControl using UDP, streaming the colour/brightness info to the panel. Optionally this can be changed to use REST (not as performant for rapid colour/brightness changes). The add-on will change between modes when selecting a Scene with the full panel if required.

It will try to display the panel layout and IDs as an image.

Additionally, zones can be defined as groups of panels in the config.yaml using the panel IDs. This exposes the zone as a single light to HA. For example, two zones for the top and bottom of the panel can be set separately. Zones can have a max brightness or brightness multiplier set to reduce how bright they are.

My primary use for this was to enable functionality with Entertainment Zones in diyhue. This has only been tested with the Nanoleaf Hexagons, feedback appreciated.

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
layout_preview_debounce_seconds: float
layout_preview_padding: true
layout_preview_labels: ture
layout_preview_background: #000000
layout_preview_stroke: #FFFFFF
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
