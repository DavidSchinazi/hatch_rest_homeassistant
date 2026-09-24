"""Hatch Rest constants."""

from enum import IntEnum

DOMAIN = "hatch_rest"
MANUFACTURER_ID = 1076

COLOR_GRADIENT = (254, 254, 254)  # setting this color turns on Gradient mode
CHAR_TX = "02240002-5efd-47eb-9c1a-de53f7a2b232"
CHAR_FEEDBACK = "02260002-5efd-47eb-9c1a-de53f7a2b232"
BT_MANUFACTURER_ID = 1076

# Markers delimiting the blocks of a state payload.
MARKER_COLOR = 0x43  # "C", followed by red, green, blue, brightness
MARKER_SOUND = 0x53  # "S", followed by sound, volume
MARKER_POWER = 0x50  # "P", followed by the power byte
POWER_OFF_MASK = 0xC0  # bits set in the power byte while the device is off

# Offsets of those markers within the feedback characteristic:
# T .. .. .. .. C r g b br S sn vol P pwr
FEEDBACK_COLOR_INDEX = 5
FEEDBACK_SOUND_INDEX = 10
FEEDBACK_POWER_INDEX = 13

# The manufacturer specific advertisement repeats the same blocks, prefixed
# with "R" and with an extra "E" block inserted before the power byte:
# R T .. .. .. .. C r g b br S sn vol E .. .. .. .. .. P pwr
ADVERTISEMENT_COLOR_INDEX = 6
ADVERTISEMENT_SOUND_INDEX = 11
ADVERTISEMENT_POWER_INDEX = 20

# How often, and for how long, to ask AUTO mode scanners to scan actively for
# a configured device. State is carried in the scan response, which only an
# active scan collects, and the defaults of 10s every 5 minutes are far too
# sparse. These are the tightest cadence habluetooth allows: the interval is
# measured between window starts and must be >= MIN_ACTIVE_SCAN_INTERVAL, and
# the window is clamped to AUTO_WINDOW_MAX_DURATION.
#
# That still leaves 25s of every minute unscanned, so this only limits the
# damage. Pinning the proxy's scanner to active mode in the ESPHome config
# entry is what actually keeps state current -- and a pinned scanner ignores
# these values entirely.
ACTIVE_SCAN_INTERVAL_SECONDS = 60
ACTIVE_SCAN_DURATION_SECONDS = 35

# How long state from an advertisement stays trusted before the coordinator
# falls back to reading over GATT. Advertisements normally arrive every few
# seconds, so this only connects to a device that has really gone quiet.
ADVERTISEMENT_STALE_SECONDS = 300

# How long to wait for a connection. establish_connection retries internally
# with no overall deadline, so an unreachable device can otherwise block for
# minutes -- and block every other caller behind it, since a connection
# attempt holds off the ones waiting on it.
CONNECT_TIMEOUT_SECONDS = 20

# How long to keep a connection open after the last operation.
#
# The device stops advertising entirely while something is connected to it,
# so every second the connection is held is a second in which its state is
# invisible: a command cannot be confirmed, and a button pressed on the
# device itself is not seen. Keep this just long enough to cover a burst of
# commands -- adjusting colour, or turning on and setting a sound -- and no
# longer, since advertisements resume a few seconds after disconnecting.
IDLE_DISCONNECT_SECONDS = 5

# How long after a command to keep trusting what was written over what the
# device advertises, so an advertisement still describing the old state does
# not briefly revert it.
COMMAND_SETTLE_SECONDS = 2


class PyHatchBabyRestSound(IntEnum):
    """Enum for Hatch Rest sound options."""

    none = 0
    stream = 2
    noise = 3
    dryer = 4
    ocean = 5
    wind = 6
    rain = 7
    bird = 9
    crickets = 10
    brahms = 11
    twinkle = 13
    rockabye = 14
