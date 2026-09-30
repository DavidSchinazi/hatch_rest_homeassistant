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

# A program comes back on CHAR_LIST as a 20 byte block sharing its header
# with a favorite, and differing only in length:
# [0x01] [start LE x4] [sound] [volume] [duration LE x2] [00 x2] [lock LE x2]
# [brightness] [B] [G] [R] [?] [days] [flags]
# Colour is blue first here too.
#
# Both published sources have the middle of this wrong. They call bytes 1-4 a
# modified timestamp and bytes 7-8 the hour and minute; read from real slots
# those give times like 46:14 and 238:182, which are not times at all.
#
# What bytes 1-4 actually hold is the start time, as a unix timestamp whose
# time of day read *as UTC* is the local time the program runs at. The date
# part is when the slot was written. Reading it as UTC rather than converting
# is what makes it right, and also what makes it immune to daylight saving.
#
# Bytes 7-8 are how long it runs for, in seconds. Every duration seen ends in
# a spare 30 seconds, which is presumably how the app writes them.
PROGRAM_START_INDEX = 1
PROGRAM_DURATION_INDEX = 7

# The app's "Toddler Lock" toggle. Caught by diffing one slot across the app
# session that turned it on: nothing else in the block moved except the date
# half of the start value. Zero when off, 0x01ff when on -- an odd value for
# something that reads as a switch, so it is reported as set or not and the
# whole block is kept for whatever the rest of it may mean.
PROGRAM_LOCK_INDEX = 11
PROGRAM_SLOTS = 10
PROGRAM_BLOCK_LENGTH = 20
PROGRAM_SOUND_INDEX = 5
PROGRAM_VOLUME_INDEX = 6
PROGRAM_BRIGHTNESS_INDEX = 13
PROGRAM_BLUE_INDEX = 14
PROGRAM_GREEN_INDEX = 15
PROGRAM_RED_INDEX = 16
PROGRAM_DAYS_INDEX = 18
PROGRAM_FLAGS_INDEX = 19

# Whether a program runs is not in the block at all. Every EGB reply is
# followed by a second, 17 byte one: a status byte, then the program's name as
# NUL padded ASCII. Bit 0x02 of that status byte is enabled -- set on the three
# programs one device's app showed enabled, clear on the three it showed
# disabled -- and an ESL40 sent to an empty slot moved its status from 0x00 to
# exactly 0x02. What the other bits are is unknown.
#
# Not the flags byte: every populated slot reads 0xdf there, enabled or not.
# Nor byte 17, which moved from 0xff to 0x00 when the app enabled a program
# but reads 0x00 on one the app shows disabled.
PROGRAM_STATUS_LENGTH = 17
PROGRAM_STATUS_ENABLED = 0x02

# What ESL writes to turn a program on or off, as the notes give it. Whatever
# the device does with it, it is not stored in the flags byte: that read 0xdf
# both before and after an ESL9f.
PROGRAM_FLAG_ENABLED = 0xC0
PROGRAM_FLAG_DISABLED = 0x80

# The fields a program keeps when only whether it is enabled changes. The start
# timestamp is left out: its date half moves on its own when a slot is
# written, and only its time of day -- already covered by "time" -- matters.
PROGRAM_CONTENT_FIELDS = (
    "time",
    "duration_seconds",
    "days_mask",
    "color",
    "brightness",
    "sound_id",
    "volume",
    "toddler_lock",
)

# Bit 0 is Sunday. Ordered to match, so the index into this is the bit number.
PROGRAM_DAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")

# The sleep timer answers in short ASCII hex rather than a block. GD gives
# what is left in seconds -- not the minutes the notes say -- and zero when
# nothing is running. GI is not understood: the notes call it the total and
# FF "no timer", but a device with a timer running answered FF, and idle ones
# answer FF or 00.
TIMER_NONE = "FF"

# SD sets the timer in seconds, four hex digits like GD, so the most it can
# hold is 0xffff seconds: 18h12m. The app offers at least nine hours.
TIMER_MAX_SECONDS = 0xFFFF

# What the sleep timer shows, and offers, when it is not running.
TIMER_OFF = "Off"

# The durations offered for the sleep timer on a dashboard. The app takes any
# number of hours and minutes; these are just common ones, and anything else
# can be set with the set_sleep_timer action.
TIMER_PRESETS = {
    "15 minutes": 15 * 60,
    "30 minutes": 30 * 60,
    "45 minutes": 45 * 60,
    "1 hour": 3600,
    "1.5 hours": 90 * 60,
    "2 hours": 2 * 3600,
    "3 hours": 3 * 3600,
    "4 hours": 4 * 3600,
    "6 hours": 6 * 3600,
    "8 hours": 8 * 3600,
    "10 hours": 10 * 3600,
    "12 hours": 12 * 3600,
}

# What the timer control shows for a timer that is not one of the presets,
# such as one started from the app.
TIMER_CUSTOM = "Custom"


# Programs fire off the device's own clock, which nothing else sets. It is
# told the local wall clock, with no zone, the same way it stores a program's
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
BLOCK_PROGRAM = "program"

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
# Briefly ten, while working out why one device failed seven connects in a row
# on the deadline. It turned out to be sitting on a metal nightstand, which
# detunes its antenna: it still advertises well enough to be seen, since that
# is the direction RSSI measures, but cannot hear the proxy well enough to
# take a connection. No deadline fixes a link that will not form, and a longer
# one only makes each doomed attempt cost more.
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

# How far either side of its delay a reconnect may land, as a fraction of it.
#
# Every device starts together at setup and backs off on the same schedule,
# so without this they retry in lockstep. Through a single proxy that is
# self-defeating: seen with three Hatches sharing one after the other went
# offline, each attempt queued behind the others, ran into the connect
# deadline, and left the proxy still busy with it for the next round. Not one
# connected in six minutes.
RECONNECT_JITTER = 0.5

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
