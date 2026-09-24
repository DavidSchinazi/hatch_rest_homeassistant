"""pyhatchbabyrestasync.

Derived from kjoconnor's pyhatchbabyrest repo.
All rights reserved.
https://github.com/kjoconnor/pyhatchbabyrest/blob/master/LICENSE

"""

import asyncio
from datetime import datetime
import logging
from time import monotonic

from collections.abc import Callable

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
    IDLE_DISCONNECT_SECONDS,
    MARKER_COLOR,
    MARKER_POWER,
    MARKER_SOUND,
    POWER_OFF_MASK,
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
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._disconnect_task: asyncio.Task | None = None
        self._settle_until: float = 0.0
        self._last_advertisement: float | None = None
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
        _LOGGER.debug("API client has successfully disconnected")
        self._cancel_idle_disconnect()
        self._client = None

    def _schedule_idle_disconnect(self) -> None:
        """Disconnect once the device has been idle for a while.

        Reconnecting costs up to a second, so holding the connection open
        makes a burst of commands much faster than connecting per command.
        """
        self._cancel_idle_disconnect()
        self._disconnect_timer = asyncio.get_running_loop().call_later(
            IDLE_DISCONNECT_SECONDS, self._idle_disconnect
        )

    def _cancel_idle_disconnect(self) -> None:
        """Cancel a pending idle disconnect."""
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

    def _idle_disconnect(self) -> None:
        """Handle the idle timer firing."""
        self._disconnect_timer = None
        _LOGGER.debug("Idle for %ds, disconnecting", IDLE_DISCONNECT_SECONDS)
        self._disconnect_task = asyncio.create_task(self._client_disconnect())

    async def _client_connect(self) -> None:
        """Connect to the device."""
        self._cancel_idle_disconnect()

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
        _LOGGER.debug("Stopping API for %s", self.address)
        self._cancel_idle_disconnect()
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

    def seconds_since_advertisement(self) -> float:
        """Return how long ago an advertisement last carried state."""
        if self._last_advertisement is None:
            return float("inf")
        return monotonic() - self._last_advertisement

    def update_from_advertisement(self, manufacturer_data: bytes | None) -> bool:
        """Update state from a manufacturer specific advertisement payload.

        The advertisement carries the same state as the feedback
        characteristic, so no connection is needed to stay up to date.
        Returns whether anything changed.
        """
        if not manufacturer_data:
            return False

        if monotonic() < self._settle_until:
            # A command was just written. The device may still be advertising
            # the state it had beforehand, which would revert the entity.
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

        self._last_advertisement = monotonic()
        return self._apply_state(state, "advertisement")

    async def _send_command(self, command: str):
        """Send a command do the device.

        :param command: The command to send.
        """
        if log_timing := _LOGGER.isEnabledFor(logging.DEBUG):
            start = monotonic()
            _LOGGER.debug("Started _send_command at %s", datetime.now().isoformat())

        self._set_active_operations(1)
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

        self._set_active_operations(-1)
        self._settle_until = monotonic() + COMMAND_SETTLE_SECONDS
        self._schedule_idle_disconnect()

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
        self._schedule_idle_disconnect()

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
