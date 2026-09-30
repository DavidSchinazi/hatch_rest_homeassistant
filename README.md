# Hatch Rest – Home Assistant Integration

This is a custom Home Assistant integration for controlling the
**Hatch Rest** (1st-generation) nightlight and sound machine over
Bluetooth Low Energy (BLE). Everything is local, no Cloud involved.

It was deployed successfully for 4 Hatch Rest 1st-gen devices using two ESP32s running
[ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy/). 

## 📦 Installation

### HACS

1. Add this repository as a **Custom Repository**
   *(HACS → Integrations → Custom Repositories)*
2. Search for **Hatch Rest**
3. Install → Restart Home Assistant

### 🔍 Adding the Device

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Hatch Rest**
3. Choose your discovered device from the list
4. Confirm the Bluetooth address
5. Done!

## 🧩 Supported Entities

### 🔌 Switch
* Master on/off power state of the device
* One per favorite, for whether the device offers it when cycling favorites
  on the touch ring
* One per program, for whether each of the ten slots runs. What the program
  is set to is exposed as attributes

### 🟡 Light

### 🔊 Media Player
* Play/pause the sounds.
* Select which of the twelve tracks is playing.

### ⭐ Select
* Which of the six stored favorites is playing. Selecting one plays it;
  what each holds is exposed as attributes

### 🔘 Button
* One per favorite: saves whatever the device is playing into that slot

### 🔢 Number
* Sleep timer, in minutes: set it to start a timer, or to 0 to cancel. Shows
  the time left, counting down

### 📊 Sensor
* Time remaining on the sleep timer

## ⭐ Favorites

The six favorites stored on the device can be read and rewritten from Home
Assistant, so editing them no longer means shutting down the Bluetooth proxies
to free the device up for the phone app.

The usual way is the buttons: set the light and sound how you want them with
the normal controls, then press **Save to Favorite N**. Two actions are
available for automations:

* `hatch_rest.save_favorite` — store what is playing now into a slot
* `hatch_rest.set_favorite` — set a slot's colour, brightness, sound, volume
  or enabled flag explicitly. Anything left out keeps its current value

The wire protocol these rely on, and how much of it has been verified against
real hardware, is written up in [PROTOCOL.md](PROTOCOL.md).

## 📡 Bluetooth

Because the Hatch Rest is a BLE device, a compatible Home Assistant Bluetooth
controller is required.

Note that
[ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy/)
is supported. For that to work, you need to go to:
`Home Assistant > Settings > Devices & services > ESPHome Integration`
and hit the settings gear on each of the proxies to set the
`Bluetooth scanning mode` to `Active`. 

This integration keeps BLE GATT connections open with the Hatch devices in
order to reduce latency. That may require deploying more Bluetooth proxies
to ensure you don't run out. When disconnected, it also relies on BLE
advertisements to keep information fresh.

## Acknowledgements

This repo is a fork of
[jcgoette/hatch_rest_homeassistant](https://github.com/jcgoette/hatch_rest_homeassistant).
The main difference is that this fork never disconnects BLE on idle, to ensure better
responsiveness. Credit is also due to
[wmbest2/hatch_rest_homeassistant](https://github.com/wmbest2/hatch_rest_homeassistant)
for taking packet captures to add support for favorites, programs, and more.

This fork was developed using Claude over a couple of days by a new sleep-deprived
father, so there are most likely still bugs.
