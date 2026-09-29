# Hatch Rest – Home Assistant Integration

This is a custom Home Assistant integration for controlling the **Hatch Rest** (1st-generation) nightlight and sound machine over Bluetooth Low Energy (BLE).

It provides a fully asynchronous, locally-controlled interface using a rewritten BLE API based on — and with gratitude to — the original work by **kjoconnor** in the `pyhatchbabyrest` project.

## This Fork

This repo is a fork of
[jcgoette/hatch_rest_homeassistant](https://github.com/jcgoette/hatch_rest_homeassistant).
The main difference is that this version never disconnects BLE on idle, to ensure better
responsiveness. It was deployed successfully for 4 Hatch devices using two ESP32s running
[ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy/). For that to work,
you need to go to: Home Assistant > Settings > Devices & services > ESPHome Integration
and hit the settings gear on each of the proxies to set the Bluetooth scanning mode to
Active. This fork was developed using Claude over a couple of days by a new sleep-deprived
father, so no promises.

## ✨ Features

* **Local BLE control** — no cloud required
* **Three (3) entities exposed:**
  * Light (RGB + brightness)
  * Switch (main power)
  * Media Player (sounds + volume)

## 📦 Installation

### HACS

1. Add this repository as a **Custom Repository**
   *(HACS → Integrations → Custom Repositories)*
2. Search for **Hatch Rest**
3. Install → Restart Home Assistant

## 🔍 Adding the Device

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

### 🟡 Light

### 🔊 Media Player

### ⭐ Select
* Which of the six stored favorites is playing. Selecting one plays it;
  what each holds is exposed as attributes

### 🔘 Button
* One per favorite: saves whatever the device is playing into that slot

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

## 📡 Bluetooth Requirements

Because the Hatch Rest is a BLE device:

* A compatible Home Assistant Bluetooth controller is required


This integration uses BLE connections aggressively but cleanly:

* Queues operations
* Avoids simultaneous connects
* Disconnects when idle
* Automatically retries on common BLE failures

## 🧪 Contributing

Issues and PRs are welcome!

If you improve the async BLE API or add new services (timers, programs, gradients), feel free to submit a pull request.

[PROTOCOL.md](PROTOCOL.md) describes the device's Bluetooth protocol and marks
which parts have been confirmed against hardware and which are taken on trust
from other people's reverse engineering. If you exercise a part that is still
marked as inherited, that document is worth updating.
