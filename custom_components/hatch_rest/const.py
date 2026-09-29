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

# How long to wait for a reply to a favorite command. Replies come back in
# well under a second on a healthy link; this only bounds a lost one.
LIST_REPLY_TIMEOUT_SECONDS = 3

# A schedule comes back on CHAR_LIST as a 20 byte block sharing its header
# with a favorite, and differing only in length:
# [0x01] [start LE x4] [sound] [volume] [duration LE x2] [00 x2] [lock LE x2]
# [brightness] [B] [G] [R] [00] [days] [flags]
# Colour is blue first here too.
#
# Both published sources have the middle of this wrong. They call bytes 1-4 a
# modified timestamp and bytes 7-8 the hour and minute; read from real slots
# those give times like 46:14 and 238:182, which are not times at all.
#
# What bytes 1-4 actually hold is the start time, as a unix timestamp whose
# time of day read *as UTC* is the local time the schedule runs at. The date
# part is when the slot was written. Reading it as UTC rather than converting
# is what makes it right, and also what makes it immune to daylight saving.
#
# Bytes 7-8 are how long it runs for, in seconds. Every duration seen ends in
# a spare 30 seconds, which is presumably how the app writes them.
SCHEDULE_START_INDEX = 1
SCHEDULE_DURATION_INDEX = 7

# The app's "Toddler Lock" toggle. Caught by diffing one slot across the app
# session that turned it on: nothing else in the block moved except the date
# half of the start value. Zero when off, 0x01ff when on -- an odd value for
# something that reads as a switch, so it is reported as set or not and the
# whole block is kept for whatever the rest of it may mean.
SCHEDULE_LOCK_INDEX = 11
SCHEDULE_SLOTS = 10
SCHEDULE_BLOCK_LENGTH = 20
SCHEDULE_SOUND_INDEX = 5
SCHEDULE_VOLUME_INDEX = 6
SCHEDULE_BRIGHTNESS_INDEX = 13
SCHEDULE_BLUE_INDEX = 14
SCHEDULE_GREEN_INDEX = 15
SCHEDULE_RED_INDEX = 16
SCHEDULE_DAYS_INDEX = 18
SCHEDULE_FLAGS_INDEX = 19

# 0x40, settled against real slots: populated ones read 0xdf and an empty one
# reads 0x9f, so 0x80 cannot be it -- that would call the empty slot enabled.
# The notes were right, and differ from favorites, which use 0x80.
SCHEDULE_ENABLED_MASK = 0x40

# Bit 0 is Sunday. Ordered to match, so the index into this is the bit number.
SCHEDULE_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

# The sleep timer answers in short ASCII hex rather than a block. GI says
# whether one is running, GD gives what is left in minutes. Note the asymmetry
# with setting it, which takes seconds.
#
# The protocol notes say an idle device answers GI with FF, and three of four
# devices here do. The fourth answers 00, and then answers GD with 0000, so a
# total or a remainder of zero means the same thing: nothing is running.
TIMER_NONE = "FF"


# Schedules fire off the device's own clock, which nothing else sets. It is
# told the local wall clock, with no zone, the same way it stores a schedule's
# start time.
#
# Not before half past two in the morning. Between two and three the local
# clock is ambiguous on the day the clocks go back and absent on the day they
# go forward, so a time sent then can be an hour out. Waiting costs nothing --
# this happens at most once a day and only matters to the minute.
CLOCK_SYNC_EARLIEST = (2, 30)

# Both kinds of block share the 0x01 header, so which one a reply is can only
# be known from what was asked for.
BLOCK_FAVORITE = "favorite"
BLOCK_SCHEDULE = "schedule"

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
#
# Ten rather than the five that range argues for, because the weakest device
# here sat at -89dBm failing seven connects in a row, every one cut off on the
# deadline, with slots free on its proxy and its neighbour on the same proxy
# connecting fine. That looks like a device refusing rather than a slow link,
# but the only way to tell them apart is to give a slow one room and see
# whether anything lands in between.
CONNECT_TIMEOUT_SECONDS = 10

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
