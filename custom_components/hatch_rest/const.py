"""Hatch Rest constants."""

from enum import IntEnum

DOMAIN = "hatch_rest"
MANUFACTURER_ID = 1076

# What to restore the light to when it is turned on without a brightness and
# the device is sitting at zero. Turning the light off writes a brightness of
# zero, and the device keeps no memory of what it was before.
DEFAULT_ON_BRIGHTNESS = 255

# What to play when the media player is asked to resume but nothing is known
# to resume to, such as after a restart. Defined at the bottom of this file,
# once PyHatchBabyRestSound exists.

COLOR_GRADIENT = (254, 254, 254)  # setting this color turns on Gradient mode
CHAR_TX = "02240002-5efd-47eb-9c1a-de53f7a2b232"
CHAR_FEEDBACK = "02260002-5efd-47eb-9c1a-de53f7a2b232"
# Replies to the favorite commands arrive here rather than on the feedback
# characteristic. Notify only -- it cannot be read.
CHAR_LIST = "02240003-5efd-47eb-9c1a-de53f7a2b232"
BT_MANUFACTURER_ID = 1076

# Markers delimiting the blocks of a state payload.
MARKER_COLOR = 0x43  # "C", followed by red, green, blue, brightness
MARKER_SOUND = 0x53  # "S", followed by sound, volume
MARKER_POWER = 0x50  # "P", followed by the power byte
POWER_OFF_MASK = 0xC0  # bits set in the power byte while the device is off

# The low bits of that same power byte name the favorite the device is
# currently playing, so it costs nothing to read. Slots are numbered from one;
# zero means none is selected, and 0x1f and 0x3f both show up meaning the same
# thing. Anything outside the slot range is treated as no selection.
FAVORITE_MASK = 0x3F
FAVORITE_SLOTS = 6

# A stored favorite comes back on CHAR_LIST as a 15 byte block:
# [0x01] [sound] [volume] [00 x6] [brightness] [B] [G] [R] [flags] [0x03]
# Note the colour arrives blue first, while the command that writes a favorite
# takes it red first. The two are not symmetric.
FAVORITE_BLOCK_LENGTH = 15
BLOCK_HEADER = 0x01
FAVORITE_SOUND_INDEX = 1
FAVORITE_VOLUME_INDEX = 2
FAVORITE_BRIGHTNESS_INDEX = 9
FAVORITE_BLUE_INDEX = 10
FAVORITE_GREEN_INDEX = 11
FAVORITE_RED_INDEX = 12
FAVORITE_FLAGS_INDEX = 13
FAVORITE_ENABLED_MASK = 0x80

# Names arrive as their own notification, headed 0x07 and followed by ASCII.
FAVORITE_NAME_HEADER = 0x07

# How long to wait for a reply to a favorite command. Replies come back in
# well under a second on a healthy link; this only bounds a lost one.
LIST_REPLY_TIMEOUT_SECONDS = 3

# The device acknowledges every command with ASCII "OK" on CHAR_LIST, sent
# after whatever data the command asked for. That makes it the end of an
# exchange: once it arrives, nothing further is coming for this request and
# the next one can safely go out. Observed arriving within ~20ms of the data.
LIST_ACK = b"OK"
LIST_ACK_TIMEOUT_SECONDS = 1

# The flags byte written back to a slot. Only the enabled bit is understood;
# the low bits are whatever the device already had there.
FAVORITE_FLAG_ENABLED = 0xC0
FAVORITE_FLAG_DISABLED = 0x80

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
#
# Connecting either works quickly or not at all: observed successes take
# between 0.6s and 1.7s, while a stuck attempt runs until it is cut off.
# Giving up early costs little, since the retry follows a couple of seconds
# later and repeated failures back off on their own.
CONNECT_TIMEOUT_SECONDS = 5

# How long to wait before trying a dropped connection again, and the most
# it will ever wait.
#
# A disconnected device has no practical state source: holding connections
# starves the proxy's scanner, so its advertisements mostly do not arrive
# either. Reconnect quickly rather than leaving it unseen. The escalation in
# _connect_and_retry still applies to connects that fail outright, so a
# device that cannot connect at all backs off instead of hammering the radio.
RECONNECT_DELAY_SECONDS = 2
MAX_RECONNECT_DELAY_SECONDS = 60

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


DEFAULT_SOUND = PyHatchBabyRestSound.rain
