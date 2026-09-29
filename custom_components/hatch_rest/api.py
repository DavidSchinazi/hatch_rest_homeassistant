"""pyhatchbabyrestasync.

Derived from kjoconnor's pyhatchbabyrest repo.
All rights reserved.
https://github.com/kjoconnor/pyhatchbabyrest/blob/master/LICENSE

"""

import asyncio
from collections.abc import Callable
from datetime import date, datetime
import logging
import math
import struct
from time import monotonic

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BleakAbortedError,
    BleakClientWithServiceCache,
    BleakConnectionError,
    BleakNotFoundError,
    BleakOutOfConnectionSlotsError,
    establish_connection,
)

from .const import (
    ADVERTISEMENT_COLOR_INDEX,
    CLOCK_SYNC_EARLIEST,
    BLOCK_FAVORITE,
    BLOCK_PROGRAM,
    CHAR_LIST,
    BLOCK_HEADER,
    FAVORITE_BLOCK_LENGTH,
    FAVORITE_BLUE_INDEX,
    FAVORITE_BRIGHTNESS_INDEX,
    FAVORITE_ENABLED_MASK,
    FAVORITE_FLAG_DISABLED,
    FAVORITE_FLAG_ENABLED,
    FAVORITE_FLAGS_INDEX,
    FAVORITE_GREEN_INDEX,
    LIST_ACK,
    LIST_ACK_TIMEOUT_SECONDS,
    FAVORITE_MASK,
    FAVORITE_RED_INDEX,
    LIST_REPLY_TIMEOUT_SECONDS,
    FAVORITE_SLOTS,
    PROGRAM_BLOCK_LENGTH,
    PROGRAM_BLUE_INDEX,
    PROGRAM_BRIGHTNESS_INDEX,
    PROGRAM_DAYS,
    PROGRAM_DAYS_INDEX,
    PROGRAM_ENABLED_MASK,
    PROGRAM_FLAGS_INDEX,
    PROGRAM_GREEN_INDEX,
    PROGRAM_LOCK_INDEX,
    PROGRAM_DURATION_INDEX,
    PROGRAM_RED_INDEX,
    PROGRAM_SLOTS,
    PROGRAM_SOUND_INDEX,
    PROGRAM_START_INDEX,
    PROGRAM_VOLUME_INDEX,
    TIMER_NONE,
    FAVORITE_SOUND_INDEX,
    FAVORITE_VOLUME_INDEX,
    ADVERTISEMENT_POWER_INDEX,
    ADVERTISEMENT_SOUND_INDEX,
    CHAR_FEEDBACK,
    CHAR_TX,
    COMMAND_SETTLE_SECONDS,
    CONNECT_TIMEOUT_SECONDS,
    FEEDBACK_COLOR_INDEX,
    FEEDBACK_POWER_INDEX,
    FEEDBACK_SOUND_INDEX,
    MARKER_COLOR,
    MARKER_POWER,
    MARKER_SOUND,
    MAX_RECONNECT_DELAY_SECONDS,
    POWER_OFF_MASK,
    RECONNECT_DELAY_SECONDS,
    PyHatchBabyRestSound,
)

_LOGGER = logging.getLogger(__name__)


class HatchRestConnectionError(Exception):
    """Raised when the device could not be reached."""


def _assert_marker(data: bytes, index: int, marker: int):
    if data[index] != marker:
        raise ValueError(f"data[{index}] {data[index]:#04x} != {marker:#04x}")


def _parse_state(
    data: bytes, color_index: int, sound_index: int, power_index: int
) -> dict:
    """Parse device state out of a feedback or advertisement payload.

    Both payloads carry the same color / sound / power blocks, just at
    different offsets, so the two code paths share this parser.
    """
    _assert_marker(data, color_index, MARKER_COLOR)
    _assert_marker(data, sound_index, MARKER_SOUND)
    _assert_marker(data, power_index, MARKER_POWER)

    red, green, blue, brightness = data[color_index + 1 : color_index + 5]

    power_byte = data[power_index + 1]
    favorite = power_byte & FAVORITE_MASK

    return {
        "color": (red, green, blue),
        "brightness": brightness,
        "sound": PyHatchBabyRestSound(data[sound_index + 1]),
        "volume": data[sound_index + 2],
        "power": not bool(POWER_OFF_MASK & power_byte),
        "active_favorite": favorite if 1 <= favorite <= FAVORITE_SLOTS else None,
    }


def _parse_favorite_block(data: bytes) -> dict:
    """Parse one stored favorite out of a reply to PGB.

    The colour arrives blue first here, which is the reverse of the order the
    command that writes a favorite expects. Getting that backwards swaps red
    and blue silently, so the two orderings are named rather than sliced.
    """
    if len(data) < FAVORITE_BLOCK_LENGTH:
        raise ValueError(f"favorite block is {len(data)} bytes, want at least 15")

    _assert_marker(data, 0, BLOCK_HEADER)

    sound_id = data[FAVORITE_SOUND_INDEX]
    try:
        sound = PyHatchBabyRestSound(sound_id)
    except ValueError:
        # The sound numbering has gaps, so a favorite can hold one this
        # integration has no name for. Worth showing anyway, and the raw id
        # is what gets written back.
        sound = None

    return {
        "color": (
            data[FAVORITE_RED_INDEX],
            data[FAVORITE_GREEN_INDEX],
            data[FAVORITE_BLUE_INDEX],
        ),
        "brightness": data[FAVORITE_BRIGHTNESS_INDEX],
        "sound": sound,
        "sound_id": sound_id,
        "volume": data[FAVORITE_VOLUME_INDEX],
        "enabled": bool(data[FAVORITE_FLAGS_INDEX] & FAVORITE_ENABLED_MASK),
    }


def _build_favorite_commands(
    slot: int,
    color: tuple[int, int, int],
    brightness: int,
    sound_id: int,
    volume: int,
    enabled: bool,
) -> list[str]:
    """Build the sequence that rewrites one stored favorite.

    The order matters and the last command commits: the device collects the
    fields as they arrive and writes them together. Note the colour goes out
    red first, the reverse of the order it comes back in.
    """
    red, green, blue = color
    flag = FAVORITE_FLAG_ENABLED if enabled else FAVORITE_FLAG_DISABLED

    return [
        f"PSB{slot:02x}",
        f"PSC{red:02x}{green:02x}{blue:02x}{brightness:02x}",
        f"PSN{sound_id:02x}",
        f"PSV{volume:02x}",
        f"PSL{flag:02x}",
        "PSF",
    ]


def _parse_hex_reply(text: str | None) -> int | None:
    """Read one of the short ASCII hex answers, or None if it is not one."""
    try:
        return int(text, 16)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError):
        return None


def _parse_program_block(data: bytes) -> dict:
    """Parse one stored program out of a reply to EGB.

    Colour arrives blue first, as in a favorite block. The days byte is a
    bitmask with Sunday as bit 0.
    """
    if len(data) < PROGRAM_BLOCK_LENGTH:
        raise ValueError(f"program block is {len(data)} bytes, want at least 20")

    _assert_marker(data, 0, BLOCK_HEADER)

    sound_id = data[PROGRAM_SOUND_INDEX]
    try:
        sound = PyHatchBabyRestSound(sound_id)
    except ValueError:
        sound = None

    days = data[PROGRAM_DAYS_INDEX]
    flags = data[PROGRAM_FLAGS_INDEX]

    # The stored value is a whole unix timestamp, but only its time of day
    # matters, and only read as UTC -- the device writes local wall clock into
    # it without a zone. Taking the remainder rather than building a datetime
    # keeps any timezone out of it, daylight saving included.
    start = struct.unpack_from("<I", data, PROGRAM_START_INDEX)[0]
    hour, minute = divmod(start % 86400 // 60, 60)

    return {
        "time": f"{hour:02d}:{minute:02d}",
        "duration_seconds": struct.unpack_from("<H", data, PROGRAM_DURATION_INDEX)[0],
        "days": [name for bit, name in enumerate(PROGRAM_DAYS) if days & (1 << bit)],
        "days_mask": days,
        "color": (
            data[PROGRAM_RED_INDEX],
            data[PROGRAM_GREEN_INDEX],
            data[PROGRAM_BLUE_INDEX],
        ),
        "brightness": data[PROGRAM_BRIGHTNESS_INDEX],
        "sound": sound,
        "sound_id": sound_id,
        "volume": data[PROGRAM_VOLUME_INDEX],
        "enabled": bool(flags & PROGRAM_ENABLED_MASK),
        "flags": flags,
        # The app calls this Toddler Lock. It reads as a switch, but holds
        # 0x01ff rather than 1, so only whether it is set is reported.
        "toddler_lock": bool(struct.unpack_from("<H", data, PROGRAM_LOCK_INDEX)[0]),
        # The whole start value. Its time of day is when the program runs,
        # which is confirmed; what the date half is for is not. It sits years
        # in the past and moved forward by exactly one day when a slot was
        # edited, which is not what a last-written date would do.
        "start_timestamp": start,
        "raw": bytes(data).hex(),
    }


class PyHatchBabyRestAsync:
    """An asynchronous interface to a Hatch Rest device using bleak."""

    def __init__(self, ble_device: BLEDevice) -> None:
        """Init PyHatchBabyRestAsync."""
        self.device = ble_device
        self.address = ble_device.address

        self._client: BleakClientWithServiceCache | None = None
        self._active_operations: int = 0
        self._settle_until: float = 0.0
        self._commands_in_flight: int = 0
        self._last_state_update: float | None = None
        self._keep_connected: bool = False
        self._reconnect_timer: asyncio.TimerHandle | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_delay: float = RECONNECT_DELAY_SECONDS
        self._reported_unreachable: bool = False
        self._state_changed_callback: Callable[[], None] | None = None

        # connection synchronization primitizes / state
        self._connection_cv = asyncio.Condition()
        self._connecting: bool = False

        # cached device state
        self.color: tuple[int, int, int] | None = None
        self.brightness: int | None = None
        self.sound: PyHatchBabyRestSound | None = None
        self.volume: int | None = None
        self.power: bool | None = None
        self.active_favorite: int | None = None

        # Stored favorites, by slot number. Populated by asking the device;
        # empty until it has answered.
        self.favorites: dict[int, dict] = {}
        # Stored programs, by slot number. Read only for now.
        self.programs: dict[int, dict] = {}

        # The sleep timer, as the device last reported it. Read once per
        # connection and counted down locally from there, rather than asked
        # for repeatedly -- it only ever runs one way.
        self.timer_total: int | None = None
        self._timer_expires_at: float | None = None

        # The last date the device was told what time it is.
        self._clock_synced_on: date | None = None
        # CHAR_LIST carries no request id, so replies are matched to requests
        # by only ever having one outstanding.
        self._list_lock = asyncio.Lock()
        self._slot_in_flight: int | None = None
        # Which kind of block the outstanding request asked for. Both kinds
        # arrive under the same header, so this is the only way to tell them
        # apart.
        self._block_kind_in_flight: str | None = None
        self._block_reply: asyncio.Future[dict] | None = None
        self._text_reply: asyncio.Future[str] | None = None
        self._last_text: str | None = None
        self._ack_reply: asyncio.Future[None] | None = None
        self._sweep_task: asyncio.Task | None = None
        # Whether the device answered the subscription for command replies.
        # Sweeping one that did not would just be timeouts.
        self._list_supported: bool = False

    def _set_active_operations(self, amount: int):
        """Change the number of running tasks."""
        if amount > 0:
            _LOGGER.debug("Incrementing self._active_operations by %d", amount)
            self._active_operations += 1
        if amount < 0:
            _LOGGER.debug("Decrementing self._active_operations by %d", abs(amount))
            self._active_operations -= 1
        _LOGGER.debug("self._active_operations = %d", self._active_operations)

    def _client_disconnected(self, client: BleakClientWithServiceCache) -> None:
        """Callback for when the client disconnects."""
        _LOGGER.debug("%s client has disconnected", self.address)
        self._client = None
        # Notifications stop with the connection, so get it back. State falls
        # back to advertisements until it returns.
        self._schedule_reconnect(RECONNECT_DELAY_SECONDS)

    async def _start_notifications(self, client: BleakClientWithServiceCache) -> None:
        """Subscribe to state updates over the connection.

        A connected device stops advertising, so without this its state is
        invisible for as long as the connection is held.
        """
        try:
            if characteristic := client.services.get_characteristic(CHAR_FEEDBACK):
                _LOGGER.debug(
                    "%s feedback characteristic properties: %s",
                    self.address,
                    characteristic.properties,
                )

            await client.start_notify(CHAR_FEEDBACK, self._notification_received)
        except Exception as e:  # noqa: BLE001
            # Not every device supports this. Advertisements still carry
            # state once the connection is released.
            _LOGGER.debug(
                "%s does not support feedback notifications -- %r", self.address, e
            )
        else:
            _LOGGER.debug("%s subscribed to feedback notifications", self.address)

        # Separately, so that a device which will not answer questions still
        # reports its state.
        try:
            await client.start_notify(CHAR_LIST, self._list_notification_received)
        except Exception as e:  # noqa: BLE001
            self._list_supported = False
            _LOGGER.debug(
                "%s does not support reply notifications -- %r", self.address, e
            )
        else:
            self._list_supported = True
            _LOGGER.debug("%s subscribed to reply notifications", self.address)

    def _list_notification_received(self, characteristic, data: bytearray) -> None:
        """Handle a reply to a command.

        Deliberately not gated on _commands_in_flight the way state
        notifications are. That gate exists to stop a stale reading of the
        device reverting an optimistic update; a reply here is an answer to a
        question this integration asked, and dropping it would hang the ask.
        """
        if not data:
            return

        if data[0] == BLOCK_HEADER:
            self._handle_block(data)

        elif bytes(data) == LIST_ACK:
            _LOGGER.debug("%s acknowledged the command", self.address)
            self._resolve(self._ack_reply, None)

        elif self._block_kind_in_flight is not None and (
            name := self._parse_name_reply(data)
        ):
            store = (
                self.favorites
                if self._block_kind_in_flight == BLOCK_FAVORITE
                else self.programs
            )
            slot = self._slot_in_flight
            if slot is None:
                return
            _LOGGER.debug(
                "%s %s %d is named %r",
                self.address,
                self._block_kind_in_flight,
                slot,
                name,
            )
            store.setdefault(slot, {})["name"] = name
            self._notify_state_changed()

        elif self._text_reply is not None:
            text = bytes(data).decode("ascii", errors="ignore").strip()
            if not text:
                return
            _LOGGER.debug("%s answered %r", self.address, text)
            self._last_text = text
            self._resolve(self._text_reply, text)

        else:
            _LOGGER.debug("%s unhandled reply %s", self.address, data.hex())

    def _handle_block(self, data: bytearray) -> None:
        """File a favorite or program block against the slot it answers.

        Favorites and programs share the 0x01 header and differ only in
        length, so what was asked for is what decides which this is. Checking
        the length as well means a reply that does not match is dropped rather
        than read as the wrong kind of thing.
        """
        kind = self._block_kind_in_flight
        slot = self._slot_in_flight
        if kind is None or slot is None:
            _LOGGER.debug("Ignoring block with nothing in flight")
            return

        if kind == BLOCK_FAVORITE:
            expected, parse, store = (
                FAVORITE_BLOCK_LENGTH,
                _parse_favorite_block,
                self.favorites,
            )
        else:
            expected, parse, store = (
                PROGRAM_BLOCK_LENGTH,
                _parse_program_block,
                self.programs,
            )

        if len(data) != expected:
            _LOGGER.debug(
                "Ignoring %d byte reply to a %s request wanting %d -- %s",
                len(data),
                kind,
                expected,
                data.hex(),
            )
            return

        try:
            parsed = parse(data)
        except (IndexError, ValueError) as e:
            _LOGGER.debug("Ignoring unparseable %s %s -- %r", kind, data.hex(), e)
            return

        _LOGGER.debug("%s %s %d: %s", self.address, kind, slot, parsed)
        store.setdefault(slot, {}).update(parsed)
        # Favorites and programs do not go through _apply_state, which is
        # what usually publishes, and that only fires when the device's own
        # state changes. An idle Hatch can go minutes without one, so without
        # this the entities showing these sit at unknown long after the
        # answer has arrived.
        self._notify_state_changed()
        self._resolve(self._block_reply, parsed)

    @staticmethod
    def _parse_name_reply(data: bytes) -> str | None:
        """Pull a slot's name out of a notification, if that is what it is.

        The leading byte varies -- 0x04, 0x05 and 0x85 have all turned up for
        programs, against the 0x07 the notes give for favorites -- so what
        marks one of these is the printable text after it rather than the
        byte itself.
        """
        name = bytes(data[1:])
        if 0x00 in name:
            name = name[: name.index(0x00)]

        text = name.decode("ascii", errors="ignore").strip()
        if not text or not text.isprintable():
            return None
        return text

    @staticmethod
    def _resolve(future: asyncio.Future | None, value) -> None:
        """Hand a reply to whoever is waiting for it, if anyone still is."""
        if future is not None and not future.done():
            future.set_result(value)

    def _notification_received(self, characteristic, data: bytearray) -> None:
        """Handle a state update pushed over the connection."""
        if self._commands_in_flight:
            # The write has not been acknowledged yet, so this still
            # describes the state before it and would revert the entity.
            _LOGGER.debug("Ignoring notification while a command is in flight")
            return

        try:
            state = _parse_state(
                data,
                FEEDBACK_COLOR_INDEX,
                FEEDBACK_SOUND_INDEX,
                FEEDBACK_POWER_INDEX,
            )
        except (IndexError, ValueError) as e:
            _LOGGER.debug("Ignoring unparseable notification %s -- %r", data.hex(), e)
            return

        self._apply_state(state, "notification")

    def _is_settling(self) -> bool:
        """Return whether a command was issued too recently to be second guessed.

        Advertisements lag and arrive unordered, so one can still describe the
        state before a command and revert what was optimistically applied.
        """
        return monotonic() < self._settle_until

    async def async_start(self) -> None:
        """Start keeping a connection to the device.

        Connecting is expensive and unpredictable on a weak link, so it is
        done once here rather than in front of every command, and held.
        """
        _LOGGER.debug("%s starting", self.address)
        self._keep_connected = True
        self._reconnect_delay = RECONNECT_DELAY_SECONDS
        await self._connect_and_retry()

    async def _connect_and_retry(self) -> None:
        """Connect, arranging another attempt later if it did not work."""
        if not self._keep_connected:
            return

        await self._client_connect()

        if self._client is not None:
            self._reconnect_delay = RECONNECT_DELAY_SECONDS
            self._start_sweep()
            return

        self._schedule_reconnect(self._reconnect_delay)
        self._reconnect_delay = min(
            self._reconnect_delay * 3, MAX_RECONNECT_DELAY_SECONDS
        )

    def _schedule_reconnect(self, delay: float) -> None:
        """Arrange to connect again after a delay."""
        if not self._keep_connected:
            return

        self._cancel_reconnect()
        _LOGGER.debug("%s reconnecting in %.0fs", self.address, delay)
        self._reconnect_timer = asyncio.get_running_loop().call_later(
            delay, self._reconnect
        )

    def _cancel_reconnect(self) -> None:
        """Cancel a pending reconnect."""
        if self._reconnect_timer is not None:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None

    def _reconnect(self) -> None:
        """Handle the reconnect timer firing."""
        self._reconnect_timer = None
        self._reconnect_task = asyncio.create_task(self._connect_and_retry())

    async def _client_connect(self) -> None:
        """Connect to the device.

        A pending reconnect is only cancelled once there is a connection to
        show for it. Cancelling on the way in would let a command or a
        coordinator poll retire the retry that _connect_and_retry scheduled,
        and neither of them schedules a replacement, so a single failure at
        the wrong moment would end reconnection for good.
        """
        async with self._connection_cv:
            if self._client and self._client.is_connected:
                _LOGGER.debug(
                    "self._client = %s and and self._client.is_connected = %s -- using existing connection",
                    self._client,
                    self._client.is_connected,
                )
                return

            if self._connecting:
                _LOGGER.debug(
                    "self._connecting = %s -- wait for connection to establish",
                    self._connecting,
                )
                await self._connection_cv.wait()
                return

            _LOGGER.debug("No existing connection -- setting self._connecting = True")
            self._connecting = True

        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    self.device,
                    self.device.address,
                    disconnected_callback=self._client_disconnected,
                )
            _LOGGER.debug("Client connected: %s", client.is_connected)
            await self._start_notifications(client)

        except (
            TimeoutError,
            BleakNotFoundError,
            BleakOutOfConnectionSlotsError,
            BleakAbortedError,
            BleakConnectionError,
            Exception,  # noqa: BLE001
        ) as e:
            self._log_unreachable(e)
            client = None

        async with self._connection_cv:
            self._connecting = False
            self._client = client
            if client is not None:
                self._cancel_reconnect()
            self._connection_cv.notify_all()

    async def _client_disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._active_operations == 0:
            # if self._client:
            _LOGGER.debug(
                "self._client = %s and self._running_tasks = %d, attempting to disconnect",
                # "self._client = %s, attempting to disconnect",
                self._client,
                self._active_operations,
            )
            try:
                await self._client.disconnect()

            except (
                BleakNotFoundError,
                BleakOutOfConnectionSlotsError,
                BleakAbortedError,
                BleakConnectionError,
                Exception,  # noqa: BLE001
            ) as e:
                _LOGGER.warning("Exception during _client_disconnect -- %r", e)
        else:
            _LOGGER.debug(
                "self._client = %s and self._running_tasks = %d, cannot currently disconnect",
                # "self._client = %s, cannot currently disconnect",
                self._client,
                self._active_operations,
            )

    async def async_stop(self) -> None:
        """Disconnect and stop talking to the device."""
        _LOGGER.debug("%s stopping", self.address)
        self._keep_connected = False
        self._cancel_reconnect()
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            self._sweep_task = None
        self._active_operations = 0
        await self._client_disconnect()

    def _log_unreachable(self, error: Exception) -> None:
        """Report a failed refresh, loudly once and quietly after that.

        A Hatch that is simply switched off stays unreachable for as long as
        it is unplugged, so warning on every poll would bury the log in
        repetitions of something already said.
        """
        if self._reported_unreachable:
            _LOGGER.debug("%s still unreachable -- %r", self.address, error)
        else:
            self._reported_unreachable = True
            _LOGGER.warning("%s is unreachable -- %r", self.address, error)

    def _apply_state(
        self, state: dict, source: str, received_at: float | None = None
    ) -> bool:
        """Store parsed state, returning whether anything changed.

        Only reports from the device itself land here -- commands publish
        through _notify_state_changed instead -- so this is where the device
        was last heard from.
        """
        # Any state at all means the device is answering again.
        self._reported_unreachable = False
        self._last_state_update = monotonic() if received_at is None else received_at

        changed = (
            self.color,
            self.brightness,
            self.sound,
            self.volume,
            self.power,
            self.active_favorite,
        ) != (
            state["color"],
            state["brightness"],
            state["sound"],
            state["volume"],
            state["power"],
            state["active_favorite"],
        )

        self.color = state["color"]
        self.brightness = state["brightness"]
        self.sound = state["sound"]
        self.volume = state["volume"]
        self.power = state["power"]
        self.active_favorite = state["active_favorite"]

        _LOGGER.debug(
            "%s %s state: color=%s brightness=%s sound=%s volume=%s power=%s "
            "favorite=%s (changed=%s)",
            self.address,
            source,
            self.color,
            self.brightness,
            self.sound,
            self.volume,
            self.power,
            self.active_favorite,
            changed,
        )

        if changed:
            self._notify_state_changed()
        return changed

    def set_state_changed_callback(self, callback: Callable[[], None] | None) -> None:
        """Set a callback to run whenever the cached state changes.

        Commands update the cache before they are written, so this reports a
        change as soon as it is asked for rather than once the device has
        acknowledged it.
        """
        self._state_changed_callback = callback

    def _notify_state_changed(self) -> None:
        """Tell the listener the cached state changed."""
        if self._state_changed_callback is not None:
            self._state_changed_callback()

    @property
    def has_state(self) -> bool:
        """Return whether the device's state is known yet."""
        return self.power is not None

    def seconds_since_state_update(self) -> float:
        """Return how long ago the device last reported its state.

        A connected device stops advertising and reports over the connection
        instead, so both count.
        """
        if self._last_state_update is None:
            return float("inf")
        return monotonic() - self._last_state_update

    def update_from_advertisement(
        self, manufacturer_data: bytes | None, received_at: float | None = None
    ) -> bool:
        """Update state from a manufacturer specific advertisement payload.

        The advertisement carries the same state as the feedback
        characteristic, so no connection is needed to stay up to date.

        received_at is when the advertisement was actually picked up, on the
        monotonic clock. Home Assistant hands out the last one it saw, which
        for a device that has been switched off for days is exactly that old,
        and taking it for current would make the device look alive.

        Returns whether anything changed.
        """
        if not manufacturer_data:
            return False

        if self._is_settling():
            _LOGGER.debug("Ignoring advertisement while the last command settles")
            return False

        try:
            state = _parse_state(
                manufacturer_data,
                ADVERTISEMENT_COLOR_INDEX,
                ADVERTISEMENT_SOUND_INDEX,
                ADVERTISEMENT_POWER_INDEX,
            )
        except (IndexError, ValueError) as e:
            _LOGGER.debug(
                "Ignoring unparseable advertisement %s -- %r",
                manufacturer_data.hex(),
                e,
            )
            return False

        return self._apply_state(state, "advertisement", received_at)

    async def _send_command(self, command: str):
        """Send a command do the device.

        :param command: The command to send.
        """
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started _send_command at %s", datetime.now().isoformat())

        self._set_active_operations(1)
        self._commands_in_flight += 1
        # Hold off advertisements for the whole command, not just from when it
        # completes: connecting can take seconds, and an advertisement still
        # describing the old state would undo what was optimistically applied.
        self._settle_until = monotonic() + COMMAND_SETTLE_SECONDS
        await self._client_connect()

        try:
            await self._client.write_gatt_char(  # pyright: ignore[reportOptionalMemberAccess]
                char_specifier=CHAR_TX,
                data=bytearray(command, "utf-8"),
                response=True,
            )

        except (
            BleakNotFoundError,
            BleakOutOfConnectionSlotsError,
            BleakAbortedError,
            BleakConnectionError,
            Exception,  # noqa: BLE001
        ) as e:
            _LOGGER.warning("Exception during _send_command -- %r", e)

        self._commands_in_flight -= 1
        self._set_active_operations(-1)
        self._settle_until = monotonic() + COMMAND_SETTLE_SECONDS

        if log_timing:
            _LOGGER.debug(
                "Finished _send_command at %s (total of %.3f seconds)",
                datetime.now().isoformat(),
                monotonic() - start,  # pyright: ignore[reportPossiblyUnboundVariable]
            )

    def _start_sweep(self) -> None:
        """Read the stored favorites in the background.

        Off the connect path on purpose: connecting is what commands wait on,
        and six round trips of housekeeping have no business delaying it.
        """
        if not self._list_supported:
            return

        if self._sweep_task is not None and not self._sweep_task.done():
            return

        self._sweep_task = asyncio.create_task(self._sweep())

    async def _sweep(self) -> None:
        """Set the clock if it is due, then read everything the device stores."""
        await self.async_sync_clock()
        await self.async_refresh_favorites()
        await self.async_refresh_programs()
        await self.async_refresh_timer()

    async def async_refresh_favorites(self) -> None:
        """Ask the device for every stored favorite."""
        for slot in range(1, FAVORITE_SLOTS + 1):
            await self.async_refresh_favorite(slot)

    async def async_refresh_favorite(self, slot: int) -> dict | None:
        """Ask the device for one stored favorite."""
        if not await self._list_exchange(
            f"PGB{slot:02X}", slot=slot, kind=BLOCK_FAVORITE
        ):
            return None
        return self.favorites.get(slot)

    async def async_refresh_programs(self) -> None:
        """Ask the device for every stored program."""
        for slot in range(1, PROGRAM_SLOTS + 1):
            await self.async_refresh_program(slot)

    async def async_refresh_program(self, slot: int) -> dict | None:
        """Ask the device for one stored program."""
        if not await self._list_exchange(
            f"EGB{slot:02X}", slot=slot, kind=BLOCK_PROGRAM
        ):
            return None
        return self.programs.get(slot)

    async def async_set_favorite(
        self,
        slot: int,
        color: tuple[int, int, int] | None = None,
        brightness: int | None = None,
        sound: PyHatchBabyRestSound | int | None = None,
        volume: int | None = None,
        enabled: bool | None = None,
    ) -> dict | None:
        """Rewrite one stored favorite, keeping whatever was not given.

        The commit writes every field at once, so a partial sequence would
        leave the others at whatever the device had collected rather than at
        what the slot held. Reading the slot first and filling the gaps from
        it is what keeps "set the colour" from clearing the sound.
        """
        if not 1 <= slot <= FAVORITE_SLOTS:
            raise ValueError(f"favorite slot {slot} is not between 1 and 6")

        current = self.favorites.get(slot)
        if current is None:
            current = await self.async_refresh_favorite(slot)
        if current is None:
            raise HatchRestConnectionError(
                f"{self.address} would not say what favorite {slot} holds, "
                "so it cannot be changed without discarding the rest of it"
            )

        if sound is None:
            sound_id = current["sound_id"]
        else:
            sound_id = int(sound)

        wanted = {
            "color": current["color"] if color is None else color,
            "brightness": current["brightness"] if brightness is None else brightness,
            "sound_id": sound_id,
            "volume": current["volume"] if volume is None else volume,
            "enabled": current["enabled"] if enabled is None else enabled,
        }
        _LOGGER.debug("%s setting favorite %d to %s", self.address, slot, wanted)

        commands = _build_favorite_commands(
            slot,
            wanted["color"],
            wanted["brightness"],
            wanted["sound_id"],
            wanted["volume"],
            wanted["enabled"],
        )
        for command in commands:
            if not await self._list_exchange(command):
                # Nothing is stored until the commit, so stopping short of it
                # leaves the slot exactly as it was. Carrying on regardless
                # would be worse than giving up: if it was the command
                # selecting the slot that went missing, the commit would land
                # on whichever slot the device still had selected.
                raise HatchRestConnectionError(
                    f"{self.address} did not acknowledge {command} while "
                    f"writing favorite {slot}"
                )

        # Read it back rather than trust the write: the byte layout here is
        # reverse engineered, and a slot that took the wrong values should say
        # so rather than quietly differ from what was asked for.
        written = await self.async_refresh_favorite(slot)
        if written is None:
            _LOGGER.warning(
                "%s would not say what favorite %d holds after writing it",
                self.address,
                slot,
            )
        else:
            differs = {
                field: (value, written[field])
                for field, value in wanted.items()
                if written[field] != value
            }
            if differs:
                _LOGGER.warning(
                    "%s favorite %d did not take what was asked for: %s",
                    self.address,
                    slot,
                    differs,
                )

        self._notify_state_changed()
        return written

    async def async_save_favorite(self, slot: int) -> dict | None:
        """Store what the device is playing now into one of its favorites.

        The enabled flag is deliberately left out, so saving into a slot the
        device is not currently offering does not quietly start offering it.
        """
        if not self.has_state:
            raise HatchRestConnectionError(
                f"{self.address} has not said what it is playing, so there is "
                f"nothing to save into favorite {slot}"
            )

        _LOGGER.debug("%s saving current state to favorite %d", self.address, slot)
        written = await self.async_set_favorite(
            slot,
            color=self.color,
            brightness=self.brightness,
            sound=self.sound,
            volume=self.volume,
        )

        # Storing a favorite does not select it: the device goes on reporting
        # whichever was playing before, or none at all if the state had been
        # changed by hand. What is playing now is this favorite by definition,
        # having just been copied from it, so say so -- and say it by telling
        # the device rather than by assuming, so the two cannot drift apart.
        await self.set_active_favorite(slot)

        return written

    async def async_sync_clock(self) -> None:
        """Tell the device what time it is, if it has not been told today.

        Its programs run off its own clock and nothing else sets it, so
        without this they drift away from the times they are set to.

        The device is told the local wall clock with no zone, which is how it
        stores a program's start time too. There is no command to read the
        clock back, so the acknowledgement is the only confirmation there is.
        """
        # Local wall clock is exactly what is wanted here: the device keeps no
        # zone, and converting would be converting to nothing.
        now = datetime.now()  # noqa: DTZ005

        if self._clock_synced_on == now.date():
            return

        if (now.hour, now.minute) < CLOCK_SYNC_EARLIEST:
            _LOGGER.debug(
                "%s not setting the clock before %02d:%02d",
                self.address,
                *CLOCK_SYNC_EARLIEST,
            )
            return

        _LOGGER.debug("%s setting the clock to %s", self.address, now)
        if await self._list_exchange(f"ST{now:%Y%m%d%H%M%S}U"):
            self._clock_synced_on = now.date()
        else:
            _LOGGER.warning("%s did not accept the clock", self.address)

    @property
    def timer_remaining(self) -> int | None:
        """Return the minutes left on the sleep timer, if one is running.

        Rounded up, so a timer the device just called 118 minutes does not
        read as 117 the instant it is asked. Whole minutes are all the device
        reports, and counting down means holding each one until it is spent.
        """
        if self._timer_expires_at is None:
            return None
        return max(0, math.ceil((self._timer_expires_at - monotonic()) / 60))

    async def async_refresh_timer(self) -> None:
        """Ask the device about its sleep timer.

        Two questions: whether one is running, and how much is left. Asked
        once per connection, because the answer only moves one way and can be
        counted down from here without pestering the device for it.
        """
        if not await self._list_exchange("GI", text=True):
            return

        total = _parse_hex_reply(self._last_text)
        if self._last_text == TIMER_NONE or not total:
            # Some devices say FF for this and some say 00. Either way there
            # is nothing to count down, and no point asking how much is left.
            self._clear_timer()
            _LOGGER.debug(
                "%s has no sleep timer running (%r)", self.address, self._last_text
            )
            return

        # What the total means when a timer IS running has not been confirmed,
        # so it is logged as it arrives rather than interpreted.
        _LOGGER.debug(
            "%s sleep timer total reported as %r", self.address, self._last_text
        )
        self.timer_total = total

        if not await self._list_exchange("GD", text=True):
            return

        minutes = _parse_hex_reply(self._last_text)
        if minutes is None:
            _LOGGER.debug(
                "%s gave %r for time remaining", self.address, self._last_text
            )
            return

        if not minutes:
            # A timer with nothing left to run is one that is not running.
            self._clear_timer()
            _LOGGER.debug("%s has no sleep timer left to run", self.address)
            return

        _LOGGER.debug("%s has %d minutes of sleep timer left", self.address, minutes)
        self._timer_expires_at = monotonic() + minutes * 60

    def _clear_timer(self) -> None:
        """Forget any sleep timer this device was thought to be running."""
        self.timer_total = None
        self._timer_expires_at = None

    async def _list_exchange(
        self,
        command: str,
        slot: int | None = None,
        kind: str | None = None,
        text: bool = False,
    ) -> bool:
        """Send a command and wait for what it replies with.

        Returns whether the device answered. What it answered with, when it
        is a slot's contents, lands in self.favorites rather than here: a
        command that only writes has no contents to return, and conflating
        that with a failure is a mistake waiting to happen.

        Only one exchange runs at a time. The replies carry nothing that
        identifies the request, so the only way to know which slot a block
        describes is to have asked for exactly one.

        Commands that ask for a slot get that slot's contents, optionally its
        name, and then an acknowledgement. Commands that only write get the
        acknowledgement alone. Either way the acknowledgement comes last, so
        waiting for it is what makes it safe to send the next command.
        """
        async with self._list_lock:
            loop = asyncio.get_running_loop()
            self._slot_in_flight = slot
            self._block_kind_in_flight = kind
            self._block_reply = loop.create_future()
            self._text_reply = loop.create_future() if text else None
            self._last_text = None
            self._ack_reply = loop.create_future()

            try:
                await self._write_list_command(command)

                if slot is not None:
                    async with asyncio.timeout(LIST_REPLY_TIMEOUT_SECONDS):
                        await self._block_reply

                if text:
                    async with asyncio.timeout(LIST_REPLY_TIMEOUT_SECONDS):
                        await self._text_reply

                async with asyncio.timeout(LIST_ACK_TIMEOUT_SECONDS):
                    await self._ack_reply

            except TimeoutError:
                _LOGGER.warning("%s did not answer %s", self.address, command)
                return False

            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("Exception during %s -- %r", command, e)
                return False

            finally:
                self._block_reply = None
                self._text_reply = None
                self._ack_reply = None

            return True

    async def _write_list_command(self, command: str) -> None:
        """Write a command whose reply comes back on CHAR_LIST.

        Unlike _send_command this leaves the settle window and the in-flight
        command count alone. Reading or rewriting a stored favorite does not
        change what the device is currently doing, so suppressing state
        updates for the duration would only blind us to real ones.
        """
        self._set_active_operations(1)
        try:
            await self._client_connect()

            if self._client is None:
                raise HatchRestConnectionError(f"{self.address} could not be connected")

            _LOGGER.debug("%s list command: %s", self.address, command)
            await self._client.write_gatt_char(
                char_specifier=CHAR_TX,
                data=bytearray(command, "utf-8"),
                response=True,
            )
        finally:
            self._set_active_operations(-1)

    async def refresh_data(self):
        """Refresh data from Hatch Rest device.

        Raises HatchRestConnectionError if the device could not be read, so
        that the caller can tell an unreachable device apart from one whose
        state simply has not changed.
        """
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started refresh_data at %s", datetime.now().isoformat())

        self._set_active_operations(1)

        try:
            await self._client_connect()

            if self._client is None:
                raise HatchRestConnectionError(f"{self.address} could not be connected")

            try:
                raw_char_read = await self._client.read_gatt_char(CHAR_FEEDBACK)
                _LOGGER.debug("Raw char read from refresh_data: %s", raw_char_read)

                self._apply_state(
                    _parse_state(
                        raw_char_read,
                        FEEDBACK_COLOR_INDEX,
                        FEEDBACK_SOUND_INDEX,
                        FEEDBACK_POWER_INDEX,
                    ),
                    "refresh_data",
                )

            except Exception as e:
                raise HatchRestConnectionError(
                    f"{self.address} could not be read -- {e!r}"
                ) from e

        except HatchRestConnectionError as e:
            self._log_unreachable(e)
            raise

        finally:
            self._set_active_operations(-1)

            if log_timing:
                _LOGGER.debug(
                    "Finished refresh_data at %s (total of %.3f seconds)",
                    datetime.now().isoformat(),
                    monotonic() - start,  # pyright: ignore[reportPossiblyUnboundVariable]
                )

    async def set_active_favorite(self, slot: int | None):
        """Play a stored favorite, or none of them.

        Goes through the ordinary command path rather than the favorites one:
        selecting a favorite changes colour, sound and volume together, so the
        settle window is needed to stop an advertisement describing the state
        before it from undoing what was applied optimistically.
        """
        command = f"SP{slot or 0:02x}"
        _LOGGER.debug("API command: set_active_favorite(%s)", slot)
        self.active_favorite = slot
        self._notify_state_changed()
        await self._send_command(command)

    async def turn_power_on(self):
        """Power on the Hatch Rest device."""
        command = f"SI{1:02x}"
        _LOGGER.debug("API command: turn_power_on")
        self.power = True
        self._notify_state_changed()
        await self._send_command(command)

    async def turn_power_off(self):
        """Power off the Hatch Rest device."""
        command = f"SI{0:02x}"
        _LOGGER.debug("API command: turn_power_off")
        self.power = False
        self._notify_state_changed()
        await self._send_command(command)

    async def set_sound(self, sound: int):
        """Set the sound of the Hatch Rest device."""
        command = f"SN{sound:02x}"
        _LOGGER.debug("API command: set_sound to %s", command)
        self.sound = PyHatchBabyRestSound(sound)
        self._notify_state_changed()
        return await self._send_command(command)

    async def set_volume(self, volume: int):
        """Set the volume of the Hatch Rest device."""
        command = f"SV{volume:02x}"
        _LOGGER.debug("API command: set_volume to %s", command)
        self.volume = volume
        self._notify_state_changed()
        return await self._send_command(command)

    async def set_color(self, red: int, green: int, blue: int):
        """Set the color of the Hatch Rest device."""
        return await self.set_color_and_brightness(
            red, green, blue, self.brightness if self.brightness is not None else 255
        )

    async def set_brightness(self, brightness: int):
        """Set the brightness of the Hatch Rest device."""
        red, green, blue = self.color if self.color is not None else (255, 255, 255)
        return await self.set_color_and_brightness(red, green, blue, brightness)

    async def set_color_and_brightness(
        self, red: int, green: int, blue: int, brightness: int
    ):
        """Set color and brightness together.

        The device takes both in a single command, so callers changing both
        should use this rather than paying for two round trips.
        """
        command = f"SC{red:02x}{green:02x}{blue:02x}{brightness:02x}"
        _LOGGER.debug("API command: set_color_and_brightness to %s", command)
        # Keep the cache in step with what was just written: set_color and
        # set_brightness each build their command from the other's cached
        # value, so a stale cache would make consecutive calls fight.
        self.color = (red, green, blue)
        self.brightness = brightness
        self._notify_state_changed()
        return await self._send_command(command)

    @property
    def name(self):
        """Return the name of the Hatch Rest device."""
        return self.device.name
