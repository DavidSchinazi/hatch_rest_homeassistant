"""pyhatchbabyrestasync.

Derived from kjoconnor's pyhatchbabyrest repo.
All rights reserved.
https://github.com/kjoconnor/pyhatchbabyrest/blob/master/LICENSE

"""

import asyncio
from collections.abc import Callable
import contextlib
from datetime import datetime
import logging
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
    CHAR_LIST,
    FAVORITE_BLOCK_HEADER,
    FAVORITE_BLOCK_LENGTH,
    FAVORITE_BLUE_INDEX,
    FAVORITE_BRIGHTNESS_INDEX,
    FAVORITE_ENABLED_MASK,
    FAVORITE_FLAGS_INDEX,
    FAVORITE_GREEN_INDEX,
    FAVORITE_MASK,
    FAVORITE_NAME_GRACE_SECONDS,
    FAVORITE_NAME_HEADER,
    FAVORITE_RED_INDEX,
    FAVORITE_REPLY_TIMEOUT_SECONDS,
    FAVORITE_SLOTS,
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

    _assert_marker(data, 0, FAVORITE_BLOCK_HEADER)

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
        # CHAR_LIST carries no request id, so replies are matched to requests
        # by only ever having one outstanding.
        self._favorite_lock = asyncio.Lock()
        self._favorite_slot_in_flight: int | None = None
        self._favorite_block_reply: asyncio.Future[dict] | None = None
        self._favorite_name_reply: asyncio.Future[str] | None = None
        self._favorite_sweep_task: asyncio.Task | None = None
        # Whether the device answered the subscription for favorite replies.
        # Sweeping one that did not would just be six timeouts.
        self._favorites_supported: bool = False

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

        # Separately, so that a device which will not talk about favorites
        # still reports its state.
        try:
            await client.start_notify(CHAR_LIST, self._list_notification_received)
        except Exception as e:  # noqa: BLE001
            self._favorites_supported = False
            _LOGGER.debug(
                "%s does not support favorite notifications -- %r", self.address, e
            )
        else:
            self._favorites_supported = True
            _LOGGER.debug("%s subscribed to favorite notifications", self.address)

    def _list_notification_received(self, characteristic, data: bytearray) -> None:
        """Handle a reply to a favorite command.

        Deliberately not gated on _commands_in_flight the way state
        notifications are. That gate exists to stop a stale reading of the
        device reverting an optimistic update; a reply here is an answer to a
        question this integration asked, and dropping it would hang the ask.
        """
        if not data:
            return

        if data[0] == FAVORITE_BLOCK_HEADER:
            try:
                favorite = _parse_favorite_block(data)
            except (IndexError, ValueError) as e:
                _LOGGER.debug("Ignoring unparseable favorite %s -- %r", data.hex(), e)
                return

            slot = self._favorite_slot_in_flight
            if slot is None:
                _LOGGER.debug("Ignoring favorite block with nothing in flight")
                return

            _LOGGER.debug("%s favorite %d: %s", self.address, slot, favorite)
            self.favorites.setdefault(slot, {}).update(favorite)
            self._resolve(self._favorite_block_reply, favorite)

        elif data[0] == FAVORITE_NAME_HEADER:
            name = self._parse_favorite_name(data)
            slot = self._favorite_slot_in_flight
            if not name or slot is None:
                return

            _LOGGER.debug("%s favorite %d is named %r", self.address, slot, name)
            self.favorites.setdefault(slot, {})["name"] = name
            self._resolve(self._favorite_name_reply, name)

        else:
            _LOGGER.debug("%s unhandled favorite reply %s", self.address, data.hex())

    @staticmethod
    def _parse_favorite_name(data: bytes) -> str:
        """Pull the ASCII name out of a name notification."""
        start = 1
        while start < len(data) and data[start] < 0x20:
            start += 1

        name = bytes(data[start:])
        if 0x00 in name:
            name = name[: name.index(0x00)]
        return name.decode("utf-8", errors="ignore").strip()

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
            self._start_favorite_sweep()
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
        if self._favorite_sweep_task is not None:
            self._favorite_sweep_task.cancel()
            self._favorite_sweep_task = None
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

    def _start_favorite_sweep(self) -> None:
        """Read the stored favorites in the background.

        Off the connect path on purpose: connecting is what commands wait on,
        and six round trips of housekeeping have no business delaying it.
        """
        if not self._favorites_supported:
            return

        if (
            self._favorite_sweep_task is not None
            and not self._favorite_sweep_task.done()
        ):
            return

        self._favorite_sweep_task = asyncio.create_task(self.async_refresh_favorites())

    async def async_refresh_favorites(self) -> None:
        """Ask the device for every stored favorite."""
        for slot in range(1, FAVORITE_SLOTS + 1):
            await self.async_refresh_favorite(slot)

    async def async_refresh_favorite(self, slot: int) -> dict | None:
        """Ask the device for one stored favorite."""
        return await self._favorite_exchange(f"PGB{slot:02X}", slot)

    async def _favorite_exchange(self, command: str, slot: int) -> dict | None:
        """Send a favorite command and wait for the block it replies with.

        Only one exchange runs at a time. The reply carries nothing that
        identifies the request, so the only way to know which slot a block
        describes is to have asked for exactly one.
        """
        async with self._favorite_lock:
            loop = asyncio.get_running_loop()
            self._favorite_slot_in_flight = slot
            self._favorite_block_reply = loop.create_future()
            self._favorite_name_reply = loop.create_future()

            try:
                await self._write_favorite_command(command)

                async with asyncio.timeout(FAVORITE_REPLY_TIMEOUT_SECONDS):
                    favorite = await self._favorite_block_reply

                # The name follows separately, and not every slot has one.
                # Keep hold of the lock while waiting so a late one is still
                # attributed to this slot.
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(FAVORITE_NAME_GRACE_SECONDS):
                        await self._favorite_name_reply

            except TimeoutError:
                _LOGGER.warning(
                    "%s did not answer %s within %ss",
                    self.address,
                    command,
                    FAVORITE_REPLY_TIMEOUT_SECONDS,
                )
                return None

            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("Exception during %s -- %r", command, e)
                return None

            finally:
                self._favorite_block_reply = None
                self._favorite_name_reply = None

            return favorite

    async def _write_favorite_command(self, command: str) -> None:
        """Write a favorite command to the device.

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

            _LOGGER.debug("%s favorite command: %s", self.address, command)
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
