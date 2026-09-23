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

# How long to keep a connection open after the last operation.
IDLE_DISCONNECT_SECONDS = 30


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
