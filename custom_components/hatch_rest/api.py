"""pyhatchbabyrestasync.

Derived from kjoconnor's pyhatchbabyrest repo.
All rights reserved.
https://github.com/kjoconnor/pyhatchbabyrest/blob/master/LICENSE

"""

import asyncio
from collections.abc import Callable
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

    return {
        "color": (red, green, blue),
        "brightness": brightness,
        "sound": PyHatchBabyRestSound(data[sound_index + 1]),
        "volume": data[sound_index + 2],
        "power": not bool(POWER_OFF_MASK & data[power_index + 1]),
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

        self._last_state_update = monotonic()
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
        """Connect to the device."""
        self._cancel_reconnect()

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
            _LOGGER.warning("Exception during _client_connect -- %r", e)
            client = None

        async with self._connection_cv:
            self._connecting = False
            self._client = client
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
        self._active_operations = 0
        await self._client_disconnect()

    def _apply_state(self, state: dict, source: str) -> bool:
        """Store parsed state, returning whether anything changed."""
        changed = (
            self.color,
            self.brightness,
            self.sound,
            self.volume,
            self.power,
        ) != (
            state["color"],
            state["brightness"],
            state["sound"],
            state["volume"],
            state["power"],
        )

        self.color = state["color"]
        self.brightness = state["brightness"]
        self.sound = state["sound"]
        self.volume = state["volume"]
        self.power = state["power"]

        _LOGGER.debug(
            "%s %s state: color=%s brightness=%s sound=%s volume=%s power=%s "
            "(changed=%s)",
            self.address,
            source,
            self.color,
            self.brightness,
            self.sound,
            self.volume,
            self.power,
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

    def update_from_advertisement(self, manufacturer_data: bytes | None) -> bool:
        """Update state from a manufacturer specific advertisement payload.

        The advertisement carries the same state as the feedback
        characteristic, so no connection is needed to stay up to date.
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

        self._last_state_update = monotonic()
        return self._apply_state(state, "advertisement")

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

    async def refresh_data(self):
        """Refresh data from Hatch Rest device."""
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started refresh_data at %s", datetime.now().isoformat())

        self._set_active_operations(1)
        await self._client_connect()

        try:
            raw_char_read = await self._client.read_gatt_char(CHAR_FEEDBACK)  # pyright: ignore[reportOptionalMemberAccess]
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

        except (
            BleakNotFoundError,
            BleakOutOfConnectionSlotsError,
            BleakAbortedError,
            BleakConnectionError,
            Exception,  # noqa: BLE001
        ) as e:
            _LOGGER.warning("Exception during refresh_data -- %r", e)

        self._set_active_operations(-1)

        if log_timing:
            _LOGGER.debug(
                "Finished refresh_data at %s (total of %.3f seconds)",
                datetime.now().isoformat(),
                monotonic() - start,  # pyright: ignore[reportPossiblyUnboundVariable]
            )

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
