"""Tests for Hatch Rest API."""

import asyncio
from collections.abc import Generator
from datetime import datetime
from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakConnectionError

from custom_components.hatch_rest.api import (
    HatchRestConnectionError,
    PyHatchBabyRestAsync,
    _assert_marker,
    _build_favorite_commands,
    _parse_favorite_block,
    _parse_schedule_block,
    _parse_state,
)
from custom_components.hatch_rest.const import (
    ADVERTISEMENT_COLOR_INDEX,
    ADVERTISEMENT_POWER_INDEX,
    ADVERTISEMENT_SOUND_INDEX,
    BLOCK_FAVORITE,
    CHAR_FEEDBACK,
    CHAR_LIST,
    CHAR_TX,
    FEEDBACK_COLOR_INDEX,
    FEEDBACK_POWER_INDEX,
    FEEDBACK_SOUND_INDEX,
    MARKER_COLOR,
    MAX_RECONNECT_DELAY_SECONDS,
    RECONNECT_DELAY_SECONDS,
    PyHatchBabyRestSound,
)

# Payloads captured from a Rest 1st Gen. The advertisement and the feedback
# characteristic were read from the same device within seconds of each other,
# and describe the same state.
ADVERTISEMENT = bytes.fromhex("5254f8001ccc43fdd12d7f53055445000000000050df6500")
FEEDBACK = bytes.fromhex("54f8001c9643fdd12d7f53055450df6500000000")


class TestAssertMarker:
    """Tests for _assert_marker helper."""

    def test_assert_marker_passes(self):
        """Test assertion passes with matching marker."""
        _assert_marker(b"\x00\x43\x53", 1, MARKER_COLOR)  # Should not raise

    def test_assert_marker_fails(self):
        """Test assertion fails with mismatched marker."""
        with pytest.raises(ValueError, match="data\\[1\\] 0x43 != 0x99"):
            _assert_marker(b"\x00\x43\x53", 1, 0x99)


class TestParseState:
    """Tests for _parse_state against payloads captured from a device."""

    def test_parse_feedback(self):
        """Test parsing the feedback characteristic."""
        assert _parse_state(
            FEEDBACK,
            FEEDBACK_COLOR_INDEX,
            FEEDBACK_SOUND_INDEX,
            FEEDBACK_POWER_INDEX,
        ) == {
            "color": (253, 209, 45),
            "brightness": 127,
            "sound": PyHatchBabyRestSound.ocean,
            "volume": 84,
            "power": False,
            "active_favorite": None,
        }

    def test_parse_advertisement_matches_feedback(self):
        """Test the advertisement describes the same state as the feedback."""
        assert _parse_state(
            ADVERTISEMENT,
            ADVERTISEMENT_COLOR_INDEX,
            ADVERTISEMENT_SOUND_INDEX,
            ADVERTISEMENT_POWER_INDEX,
        ) == _parse_state(
            FEEDBACK,
            FEEDBACK_COLOR_INDEX,
            FEEDBACK_SOUND_INDEX,
            FEEDBACK_POWER_INDEX,
        )

    @pytest.mark.parametrize(
        ("power_byte", "expected_power", "expected_favorite"),
        [
            (0x00, True, None),  # on, nothing selected
            (0x01, True, 1),  # on, playing favorite 1
            (0x06, True, 6),  # on, playing the last slot
            (0x07, True, None),  # past the last slot, so not a selection
            (0x1F, True, None),  # seen in the wild meaning none
            (0x80, False, None),  # off, favorite mode
            (0xC0, False, None),  # off, manual mode
            (0xDF, False, None),  # the captured payload: 0xdf & 0x3f == 0x1f
            (0x81, False, 1),  # off, but favorite 1 is still the selection
        ],
    )
    def test_parse_reads_favorite_from_the_power_byte(
        self, power_byte, expected_power, expected_favorite
    ):
        """Test the low bits of the power byte name the active favorite."""
        payload = bytearray(FEEDBACK)
        payload[FEEDBACK_POWER_INDEX + 1] = power_byte

        state = _parse_state(
            payload,
            FEEDBACK_COLOR_INDEX,
            FEEDBACK_SOUND_INDEX,
            FEEDBACK_POWER_INDEX,
        )

        assert state["power"] is expected_power
        assert state["active_favorite"] == expected_favorite

    def test_parse_rejects_misaligned_payload(self):
        """Test a payload without the expected markers is rejected."""
        with pytest.raises(ValueError):
            _parse_state(
                ADVERTISEMENT,
                FEEDBACK_COLOR_INDEX,
                FEEDBACK_SOUND_INDEX,
                FEEDBACK_POWER_INDEX,
            )


# A stored favorite as the device returns it: ocean at volume 84, colour
# (253, 209, 45) at brightness 127, enabled. Colour is blue first on the wire.
FAVORITE_BLOCK = bytes.fromhex("0105540000000000007f2dd1fd9603")


class TestParseFavoriteBlock:
    """Tests for the 15-byte block returned by PGB."""

    def test_reads_colour_as_rgb(self):
        """Test the blue-first wire order is turned back into RGB.

        The command that writes a favorite takes red first, so getting this
        backwards would swap red and blue with nothing to show for it.
        """
        favorite = _parse_favorite_block(FAVORITE_BLOCK)

        assert favorite["color"] == (253, 209, 45)
        # Guard against a palindrome hiding the bug.
        assert favorite["color"] != (45, 209, 253)

    def test_reads_the_remaining_fields(self):
        """Test sound, volume and brightness come back."""
        favorite = _parse_favorite_block(FAVORITE_BLOCK)

        assert favorite["sound"] == PyHatchBabyRestSound.ocean
        assert favorite["sound_id"] == PyHatchBabyRestSound.ocean
        assert favorite["volume"] == 84
        assert favorite["brightness"] == 127
        assert favorite["enabled"] is True

    def test_reads_a_disabled_slot(self):
        """Test the enabled bit of the flags byte."""
        payload = bytearray(FAVORITE_BLOCK)
        payload[13] = 0x16

        assert _parse_favorite_block(payload)["enabled"] is False

    def test_keeps_an_unknown_sound_id(self):
        """Test a sound with no name is still usable.

        The sound numbering has gaps, so a favorite can hold one this
        integration cannot name. The raw id is what gets written back, so
        losing it would corrupt the slot on the next write.
        """
        payload = bytearray(FAVORITE_BLOCK)
        payload[1] = 8  # absent from PyHatchBabyRestSound

        favorite = _parse_favorite_block(payload)

        assert favorite["sound"] is None
        assert favorite["sound_id"] == 8

    def test_rejects_a_short_payload(self):
        """Test a truncated block is refused rather than read past."""
        with pytest.raises(ValueError):
            _parse_favorite_block(FAVORITE_BLOCK[:10])

    def test_rejects_a_payload_without_the_header(self):
        """Test a block that is not a favorite is refused."""
        payload = bytearray(FAVORITE_BLOCK)
        payload[0] = 0x07

        with pytest.raises(ValueError):
            _parse_favorite_block(payload)


# A stored schedule as the device returns it: 07:30 on weekdays, rain at
# volume 40, colour (253, 209, 45) at brightness 127, enabled.
#  [01][modified LE x4][snd][vol][hr][min][00 x4][bri][B][G][R][00][days][flags]
SCHEDULE_BLOCK = bytes.fromhex(
    "01"  # header
    "f80db265"  # start, little endian: a timestamp reading 07:30 as UTC
    "07"  # sound: rain
    "28"  # volume 40
    "100e"  # duration, little endian: 3600 seconds
    "00000000"
    "7f"  # brightness 127
    "2dd1fd"  # colour, blue first: (253, 209, 45)
    "00"
    "3e"  # days: Mon-Fri
    "40"  # flags: enabled
)


class TestParseScheduleBlock:
    """Tests for the 20-byte block returned by EGB."""

    def test_reads_the_time_from_the_start_value(self):
        """Test the start time comes out of bytes 1-4, read as UTC.

        Both published sources call that field a modified timestamp and put
        the time in bytes 7-8. Those hold the duration, and reading them as a
        time gives things like 46:14.
        """
        schedule = _parse_schedule_block(SCHEDULE_BLOCK)

        assert schedule["time"] == "07:30"

    def test_the_time_does_not_move_with_the_local_zone(self):
        """Test the start value is read as UTC rather than converted.

        The device writes local wall clock into a field shaped like a unix
        timestamp, with no zone. Converting it would shift every schedule by
        the offset, and twice a year by an hour more.
        """
        import os
        import time

        was = os.environ.get("TZ")
        try:
            for zone in ("UTC", "America/Los_Angeles", "Australia/Sydney"):
                os.environ["TZ"] = zone
                time.tzset()
                assert _parse_schedule_block(SCHEDULE_BLOCK)["time"] == "07:30", zone
        finally:
            if was is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = was
            time.tzset()

    def test_reads_how_long_it_runs_for(self):
        """Test the duration comes out of bytes 7-8."""
        assert _parse_schedule_block(SCHEDULE_BLOCK)["duration_seconds"] == 3600

    @pytest.mark.parametrize(
        ("lock_bytes", "expected"),
        [("0000", False), ("ff01", True)],
    )
    def test_reads_the_toddler_lock(self, lock_bytes, expected):
        """Test the app's Toddler Lock toggle comes out of bytes 11-12.

        Found by diffing one slot across the app session that turned it on:
        nothing else in the block moved except the date half of the start
        value. It holds 0x01ff rather than 1, so only whether it is set is
        reported.
        """
        payload = bytearray(SCHEDULE_BLOCK)
        payload[11:13] = bytes.fromhex(lock_bytes)

        assert _parse_schedule_block(payload)["toddler_lock"] is expected

    def test_keeps_the_block_whole(self):
        """Test the raw bytes are carried through.

        This layout was worked out from real slots against two sources that
        had it wrong, and some bytes are still unaccounted for.
        """
        schedule = _parse_schedule_block(SCHEDULE_BLOCK)

        assert schedule["raw"] == SCHEDULE_BLOCK.hex()

    def test_reads_colour_as_rgb(self):
        """Test the blue-first wire order is turned back into RGB.

        Same trap as the favorite block, and the same guard against a
        palindrome hiding it.
        """
        schedule = _parse_schedule_block(SCHEDULE_BLOCK)

        assert schedule["color"] == (253, 209, 45)
        assert schedule["color"] != (45, 209, 253)

    def test_reads_the_days_bitmask(self):
        """Test bit 0 is Sunday, so 0x3e is Monday through Friday."""
        schedule = _parse_schedule_block(SCHEDULE_BLOCK)

        assert schedule["days"] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
        assert schedule["days_mask"] == 0x3E

    def test_reads_every_day(self):
        """Test a schedule that runs daily names all seven."""
        payload = bytearray(SCHEDULE_BLOCK)
        payload[18] = 0x7F

        assert _parse_schedule_block(payload)["days"] == [
            "Sun",
            "Mon",
            "Tue",
            "Wed",
            "Thu",
            "Fri",
            "Sat",
        ]

    def test_reports_the_flags_byte_raw(self):
        """Test the flags byte is exposed, not just its reading.

        Which bit means enabled is unsettled -- the notes say 0x40 for a
        schedule and 0x80 for a favorite -- so the raw byte is what will
        settle it against a disabled slot.
        """
        schedule = _parse_schedule_block(SCHEDULE_BLOCK)

        assert schedule["flags"] == 0x40
        assert schedule["enabled"] is True

    def test_reads_the_remaining_fields(self):
        """Test sound, volume and brightness come back."""
        schedule = _parse_schedule_block(SCHEDULE_BLOCK)

        assert schedule["sound"] == PyHatchBabyRestSound.rain
        assert schedule["volume"] == 40
        assert schedule["brightness"] == 127

    def test_rejects_a_short_payload(self):
        """Test a truncated block is refused rather than read past."""
        with pytest.raises(ValueError):
            _parse_schedule_block(SCHEDULE_BLOCK[:15])


class TestBuildFavoriteCommands:
    """Tests for the sequence that rewrites a stored favorite."""

    def test_sequence_and_order(self):
        """Test the six commands come out in the order the device expects."""
        assert _build_favorite_commands(
            3, (253, 209, 45), 127, 7, 84, enabled=True
        ) == [
            "PSB03",
            "PSCfdd12d7f",
            "PSN07",
            "PSV54",
            "PSLc0",
            "PSF",
        ]

    def test_colour_goes_out_red_first(self):
        """Test the write order is RGB, the reverse of how it comes back.

        Reading gives blue first. Writing in that same order would swap red
        and blue every time a favorite was saved.
        """
        commands = _build_favorite_commands(1, (0xAA, 0xBB, 0xCC), 0, 0, 0, True)

        assert commands[1] == "PSCaabbcc00"
        assert commands[1] != "PSCccbbaa00"

    def test_disabled_flag(self):
        """Test a disabled favorite is written with the other flag."""
        commands = _build_favorite_commands(1, (0, 0, 0), 0, 0, 0, enabled=False)

        assert commands[4] == "PSL80"

    def test_round_trips_a_decoded_block(self):
        """Test what was read back can be written out unchanged.

        This is the pairing that matters: the decoder reads blue first and the
        builder writes red first, so a round trip catches either being wrong.
        """
        favorite = _parse_favorite_block(FAVORITE_BLOCK)

        commands = _build_favorite_commands(
            2,
            favorite["color"],
            favorite["brightness"],
            favorite["sound_id"],
            favorite["volume"],
            favorite["enabled"],
        )

        # The colour bytes in the block, read straight off the wire.
        assert FAVORITE_BLOCK[12:9:-1].hex() == "fdd12d"
        assert commands[1] == "PSCfdd12d7f"


class TestPyHatchBabyRestAsync:
    """Tests for PyHatchBabyRestAsync."""

    @pytest.fixture
    def api(self, mock_ble_device: BLEDevice) -> Generator[PyHatchBabyRestAsync]:
        """Create API instance, cancelling any pending idle disconnect."""
        api = PyHatchBabyRestAsync(mock_ble_device)
        yield api
        api._cancel_reconnect()

    def test_init(self, api: PyHatchBabyRestAsync, mock_ble_device: BLEDevice):
        """Test API initialization."""
        assert api.device == mock_ble_device
        assert api.address == mock_ble_device.address
        assert api.color is None
        assert api.brightness is None
        assert api.sound is None
        assert api.volume is None
        assert api.power is None

    def test_name_property(self, api: PyHatchBabyRestAsync):
        """Test name property returns device name."""
        assert api.name == "Hatch Rest"

    @pytest.mark.asyncio
    async def test_client_connect_success(self, api: PyHatchBabyRestAsync):
        """Test successful client connection."""
        mock_client = MagicMock()
        mock_client.is_connected = True

        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            await api._client_connect()
            assert api._client == mock_client

    @pytest.mark.asyncio
    async def test_client_connect_failure(self, api: PyHatchBabyRestAsync):
        """Test client connection failure is handled."""
        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            side_effect=BleakConnectionError("Connection failed"),
        ):
            await api._client_connect()
            assert api._client is None

    @pytest.mark.asyncio
    async def test_connect_subscribes_to_feedback(self, api: PyHatchBabyRestAsync):
        """Test connecting subscribes to state pushed over the connection."""
        mock_client = AsyncMock()
        mock_client.is_connected = True
        mock_client.services = MagicMock()

        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            await api._client_connect()

        subscribed = [call.args[0] for call in mock_client.start_notify.await_args_list]
        assert CHAR_FEEDBACK in subscribed
        # Favorite replies arrive on their own characteristic.
        assert CHAR_LIST in subscribed

    @pytest.mark.asyncio
    async def test_connect_survives_a_device_without_favorites(
        self, api: PyHatchBabyRestAsync
    ):
        """Test failing to subscribe for favorites still leaves state working.

        The two subscriptions are independent: a device that will not talk
        about favorites must still report what it is doing.
        """
        mock_client = AsyncMock()
        mock_client.is_connected = True
        mock_client.services = MagicMock()

        async def start_notify(char, _handler):
            if char == CHAR_LIST:
                raise BleakConnectionError("no such characteristic")

        mock_client.start_notify = AsyncMock(side_effect=start_notify)

        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            await api._client_connect()

        assert api._client is mock_client
        subscribed = [call.args[0] for call in mock_client.start_notify.await_args_list]
        assert CHAR_FEEDBACK in subscribed

    @pytest.mark.asyncio
    async def test_connect_survives_a_device_that_cannot_notify(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a device without notifications is still usable.

        Not every device supports them, and advertisements still carry state
        once the connection is released, so this must not fail the connect.
        """
        mock_client = AsyncMock()
        mock_client.is_connected = True
        mock_client.services = MagicMock()
        mock_client.start_notify = AsyncMock(
            side_effect=Exception("characteristic does not support notifications")
        )

        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            await api._client_connect()

        assert api._client is mock_client

    def test_notification_updates_state(self, api: PyHatchBabyRestAsync):
        """Test state pushed over the connection reaches the listener."""
        listener = MagicMock()
        api.set_state_changed_callback(listener)

        api._notification_received(MagicMock(), bytearray(FEEDBACK))

        assert api.color == (253, 209, 45)
        assert api.brightness == 127
        assert api.sound == PyHatchBabyRestSound.ocean
        assert api.volume == 84
        assert api.power is False
        listener.assert_called_once()

    def test_unparseable_notification_is_ignored(self, api: PyHatchBabyRestAsync):
        """Test a payload that does not parse leaves state alone."""
        api._notification_received(MagicMock(), bytearray(b"\x00\x01\x02"))

        assert api.has_state is False

    @pytest.mark.asyncio
    async def test_notification_in_flight_does_not_revert_a_command(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a notification predating the write cannot undo it.

        Until write_gatt_char returns the device has not acknowledged the
        command, so anything it reports still describes the state before it.
        """
        api.power = False
        seen = []

        async def connect_and_notify():
            # Arrives while the command is still in flight.
            api._notification_received(MagicMock(), bytearray(FEEDBACK))
            seen.append(api.power)
            api._client = AsyncMock()

        with (
            patch.object(api, "_client_connect", connect_and_notify),
            patch.object(api, "_client_disconnect", new_callable=AsyncMock),
        ):
            await api.turn_power_on()

        assert seen == [True]
        assert api.power is True

    @pytest.mark.asyncio
    async def test_notification_after_a_command_is_applied(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the notification confirming a command is not thrown away.

        The device reports about once a second over the connection, and those
        reports are the only confirmation the command actually landed.
        """
        with (
            patch.object(api, "_client_connect", new_callable=AsyncMock),
            patch.object(api, "_client_disconnect", new_callable=AsyncMock),
        ):
            api._client = AsyncMock()
            await api.turn_power_on()

        assert api.power is True

        # FEEDBACK describes the device as powered off.
        api._notification_received(MagicMock(), bytearray(FEEDBACK))

        assert api.power is False

    @pytest.mark.asyncio
    async def test_client_connect_times_out(self, api: PyHatchBabyRestAsync):
        """Test a connection that never completes is given up on.

        establish_connection retries internally with no overall deadline, so
        without a timeout an unreachable device blocks setup and every other
        caller waiting behind this one.
        """

        async def never_connects(*args, **kwargs):
            await asyncio.sleep(3600)

        with (
            patch(
                "custom_components.hatch_rest.api.establish_connection",
                never_connects,
            ),
            patch("custom_components.hatch_rest.api.CONNECT_TIMEOUT_SECONDS", 0.05),
        ):
            await asyncio.wait_for(api._client_connect(), timeout=5)

        assert api._client is None
        assert api._connecting is False

    @pytest.mark.asyncio
    async def test_client_connect_timeout_releases_waiters(
        self, api: PyHatchBabyRestAsync
    ):
        """Test callers queued behind a stuck connection are released."""

        async def never_connects(*args, **kwargs):
            await asyncio.sleep(3600)

        with (
            patch(
                "custom_components.hatch_rest.api.establish_connection",
                never_connects,
            ),
            patch("custom_components.hatch_rest.api.CONNECT_TIMEOUT_SECONDS", 0.05),
        ):
            first = asyncio.create_task(api._client_connect())
            await asyncio.sleep(0)  # let the first caller claim the connect
            second = asyncio.create_task(api._client_connect())

            await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

        assert api._client is None

    @pytest.mark.asyncio
    async def test_client_disconnect_when_idle(self, api: PyHatchBabyRestAsync):
        """Test client disconnects when no active operations."""
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        api._client = mock_client
        api._active_operations = 0

        await api._client_disconnect()
        mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_client_no_disconnect_when_busy(self, api: PyHatchBabyRestAsync):
        """Test client doesn't disconnect with active operations."""
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        api._client = mock_client
        api._active_operations = 1

        await api._client_disconnect()
        mock_client.disconnect.assert_not_called()

    @pytest.mark.asyncio
    async def test_refresh_data_parses_response(self, api: PyHatchBabyRestAsync):
        """Test refresh_data correctly parses device response."""
        # Simulated raw response from device
        # Format: [..., 0x43, R, G, B, brightness, 0x53, sound, volume, 0x50, power_byte]
        raw_response = bytearray(
            [
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,  # padding (indices 0-4)
                0x43,  # color marker (index 5)
                0xFF,
                0x80,
                0x40,
                0x64,  # R, G, B, brightness (indices 6-9)
                0x53,  # audio marker (index 10)
                0x05,
                0x64,  # sound (ocean=5), volume (100) (indices 11-12)
                0x50,  # power marker (index 13)
                0x00,  # power byte (on when bit not set) (index 14)
            ]
        )

        mock_client = AsyncMock()
        mock_client.read_gatt_char = AsyncMock(return_value=raw_response)
        api._client = mock_client

        with patch.object(api, "_client_connect", new_callable=AsyncMock):
            with patch.object(api, "_client_disconnect", new_callable=AsyncMock):
                await api.refresh_data()

        assert api.color == (255, 128, 64)
        assert api.brightness == 100
        assert api.sound == PyHatchBabyRestSound.ocean
        assert api.volume == 100
        assert api.power is True

    @pytest.mark.asyncio
    async def test_refresh_data_raises_when_connect_fails(
        self, api: PyHatchBabyRestAsync
    ):
        """Test refresh_data reports an unreachable device rather than hiding it."""
        api._client = None

        async def fail_to_connect():
            api._client = None

        with (
            patch.object(api, "_client_connect", side_effect=fail_to_connect),
            pytest.raises(HatchRestConnectionError, match="could not be connected"),
        ):
            await api.refresh_data()

        assert api._active_operations == 0

    @pytest.mark.asyncio
    async def test_refresh_data_raises_when_read_fails(self, api: PyHatchBabyRestAsync):
        """Test a failed characteristic read surfaces as a connection error."""
        mock_client = AsyncMock()
        mock_client.read_gatt_char = AsyncMock(side_effect=BleakConnectionError("boom"))
        api._client = mock_client

        with (
            patch.object(api, "_client_connect", new_callable=AsyncMock),
            pytest.raises(HatchRestConnectionError, match="could not be read"),
        ):
            await api.refresh_data()

        assert api._active_operations == 0

    @pytest.mark.asyncio
    async def test_refresh_data_warns_once_while_unreachable(
        self, api: PyHatchBabyRestAsync, caplog: pytest.LogCaptureFixture
    ):
        """Test a device that stays unplugged is only warned about once."""

        async def fail_to_connect():
            api._client = None

        with patch.object(api, "_client_connect", side_effect=fail_to_connect):
            for _ in range(3):
                with pytest.raises(HatchRestConnectionError):
                    await api.refresh_data()

        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "is unreachable" in r.getMessage()
        ]
        assert len(warnings) == 1

    @pytest.mark.asyncio
    async def test_refresh_data_warns_again_after_recovery(
        self, api: PyHatchBabyRestAsync, caplog: pytest.LogCaptureFixture
    ):
        """Test a device that comes back and leaves again warns a second time."""

        async def fail_to_connect():
            api._client = None

        with patch.object(api, "_client_connect", side_effect=fail_to_connect):
            with pytest.raises(HatchRestConnectionError):
                await api.refresh_data()

            # A notification or advertisement arriving means it is back.
            api._apply_state(
                {
                    "color": (1, 2, 3),
                    "brightness": 4,
                    "sound": PyHatchBabyRestSound.rain,
                    "volume": 5,
                    "power": True,
                    "active_favorite": None,
                },
                "notification",
            )

            with pytest.raises(HatchRestConnectionError):
                await api.refresh_data()

        warnings = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "is unreachable" in r.getMessage()
        ]
        assert len(warnings) == 2

    @pytest.mark.asyncio
    async def test_turn_power_on(self, api: PyHatchBabyRestAsync):
        """Test turn_power_on sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.turn_power_on()
            mock_send.assert_called_once_with("SI01")

    @pytest.mark.asyncio
    async def test_turn_power_off(self, api: PyHatchBabyRestAsync):
        """Test turn_power_off sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.turn_power_off()
            mock_send.assert_called_once_with("SI00")

    @pytest.mark.asyncio
    async def test_set_sound(self, api: PyHatchBabyRestAsync):
        """Test set_sound sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_sound(PyHatchBabyRestSound.rain)
            mock_send.assert_called_once_with("SN07")  # rain = 7

    @pytest.mark.asyncio
    async def test_set_volume(self, api: PyHatchBabyRestAsync):
        """Test set_volume sends correct command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_volume(128)
            mock_send.assert_called_once_with("SV80")  # 128 in hex

    @pytest.mark.asyncio
    async def test_set_color(self, api: PyHatchBabyRestAsync):
        """Test set_color sends correct command."""
        api.brightness = 100
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_color(255, 128, 64)
            mock_send.assert_called_once_with("SCff804064")

    @pytest.mark.asyncio
    async def test_set_brightness(self, api: PyHatchBabyRestAsync):
        """Test set_brightness sends correct command."""
        api.color = (255, 128, 64)
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_brightness(200)
            mock_send.assert_called_once_with("SCff8040c8")  # 200 in hex = c8

    @pytest.mark.asyncio
    async def test_set_color_then_brightness_keeps_color(
        self, api: PyHatchBabyRestAsync
    ):
        """Test consecutive color and brightness calls do not fight.

        Both are written with the same SC command, each filling in the other
        value from the cache, so setting one must not revert the other.
        """
        api.color = (255, 255, 255)
        api.brightness = 255

        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_color(215, 150, 255)
            await api.set_brightness(181)

        assert mock_send.call_args_list[0].args[0] == "SCd796ffff"
        assert mock_send.call_args_list[1].args[0] == "SCd796ffb5"
        assert api.color == (215, 150, 255)
        assert api.brightness == 181

    @pytest.mark.asyncio
    async def test_commands_update_cached_state(self, api: PyHatchBabyRestAsync):
        """Test commands update cached state without reading it back."""
        with patch.object(api, "_send_command", new_callable=AsyncMock):
            await api.turn_power_on()
            assert api.power is True

            await api.turn_power_off()
            assert api.power is False

            await api.set_sound(PyHatchBabyRestSound.rain)
            assert api.sound == PyHatchBabyRestSound.rain

            await api.set_volume(128)
            assert api.volume == 128

    @pytest.mark.asyncio
    async def test_set_brightness_without_cached_color(self, api: PyHatchBabyRestAsync):
        """Test brightness can be set before any color is known."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_brightness(200)

        mock_send.assert_called_once_with("SCffffffc8")

    @pytest.mark.asyncio
    async def test_send_command_does_not_read_back(self, api: PyHatchBabyRestAsync):
        """Test _send_command does not connect again just to re-read state."""
        mock_client = AsyncMock()
        api._client = mock_client

        with (
            patch.object(api, "_client_connect", new_callable=AsyncMock),
            patch.object(api, "_client_disconnect", new_callable=AsyncMock),
            patch.object(api, "refresh_data", new_callable=AsyncMock) as mock_refresh,
        ):
            await api._send_command("SI01")

        mock_refresh.assert_not_called()
        mock_client.read_gatt_char.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_command_writes_to_characteristic(
        self, api: PyHatchBabyRestAsync
    ):
        """Test _send_command writes to correct characteristic."""
        mock_client = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        api._client = mock_client

        with (
            patch.object(api, "_client_connect", new_callable=AsyncMock),
            patch.object(api, "_client_disconnect", new_callable=AsyncMock),
        ):
            await api._send_command("SI01")

        mock_client.write_gatt_char.assert_called_once_with(
            char_specifier=CHAR_TX,
            data=bytearray("SI01", "utf-8"),
            response=True,
        )

    @pytest.mark.asyncio
    async def test_set_active_favorite(self, api: PyHatchBabyRestAsync):
        """Test playing a favorite sends the select command."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_active_favorite(3)
            mock_send.assert_called_once_with("SP03")

        assert api.active_favorite == 3

    @pytest.mark.asyncio
    async def test_set_active_favorite_none_deselects(self, api: PyHatchBabyRestAsync):
        """Test deselecting sends slot zero."""
        with patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send:
            await api.set_active_favorite(None)
            mock_send.assert_called_once_with("SP00")

        assert api.active_favorite is None

    @pytest.mark.asyncio
    async def test_set_active_favorite_settles_like_other_commands(
        self, api: PyHatchBabyRestAsync
    ):
        """Test playing a favorite holds off advertisements.

        Unlike reading a stored favorite, this changes colour, sound and
        volume at once, so a stale advertisement would undo it.
        """
        api._settle_until = 0.0
        mock_client = AsyncMock()
        api._client = mock_client

        with patch.object(api, "_client_connect", new_callable=AsyncMock):
            await api.set_active_favorite(1)

        assert api._settle_until > 0.0

    @pytest.mark.asyncio
    async def test_refresh_favorite_stores_what_came_back(
        self, api: PyHatchBabyRestAsync
    ):
        """Test asking for a slot files the reply against that slot."""

        async def answer(command):
            assert command == "PGB03"
            api._list_notification_received(None, bytearray(FAVORITE_BLOCK))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            favorite = await api.async_refresh_favorite(3)

        assert favorite is not None
        assert favorite["color"] == (253, 209, 45)
        assert api.favorites[3]["volume"] == 84

    @pytest.mark.asyncio
    @pytest.mark.parametrize("header", [b"\x07", b"\x04", b"\x05", b"\x85"])
    async def test_refresh_favorite_records_a_name(
        self, api: PyHatchBabyRestAsync, header
    ):
        """Test a name is recognised whatever byte introduces it.

        The notes give 0x07, but schedules on real devices answer with 0x04,
        0x05 and 0x85, so the leading byte is no way to tell.
        """

        async def answer(command):
            api._list_notification_received(None, bytearray(FAVORITE_BLOCK))
            api._list_notification_received(None, bytearray(header + b"Bedtime\x00"))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_favorite(2)

        assert api.favorites[2]["name"] == "Bedtime"

    @pytest.mark.asyncio
    async def test_refresh_schedule_records_a_name(self, api: PyHatchBabyRestAsync):
        """Test a schedule's name is kept too, against its own slot."""

        async def answer(command):
            api._list_notification_received(None, bytearray(SCHEDULE_BLOCK))
            api._list_notification_received(
                None, bytearray(b"\x85Weekday Sleep\x00\xff\xff")
            )
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_schedule(3)

        assert api.schedules[3]["name"] == "Weekday Sleep"
        assert api.favorites == {}

    @pytest.mark.asyncio
    async def test_refresh_favorite_gives_up_when_nothing_answers(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a slot that never replies does not wait forever."""
        with (
            patch.object(api, "_write_list_command", new_callable=AsyncMock),
            patch("custom_components.hatch_rest.api.LIST_REPLY_TIMEOUT_SECONDS", 0.01),
        ):
            assert await api.async_refresh_favorite(1) is None

        assert api.favorites == {}

    @pytest.mark.asyncio
    async def test_refresh_favorites_asks_for_every_slot(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the sweep covers all six slots, in order."""
        asked = []

        async def answer(command):
            asked.append(command)
            api._list_notification_received(None, bytearray(FAVORITE_BLOCK))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_favorites()

        assert asked == [f"PGB{slot:02X}" for slot in range(1, 7)]
        assert sorted(api.favorites) == [1, 2, 3, 4, 5, 6]

    @pytest.mark.asyncio
    async def test_refresh_favorite_waits_for_the_acknowledgement(
        self, api: PyHatchBabyRestAsync
    ):
        """Test an exchange is not considered over until the device says so.

        Every command is acknowledged with "OK" after its data, so the ack is
        what makes it safe to send the next one. Returning on the block alone
        would let the following request go out while replies to this one were
        still arriving.
        """
        with (
            patch.object(api, "_write_list_command", new_callable=AsyncMock),
            patch("custom_components.hatch_rest.api.LIST_REPLY_TIMEOUT_SECONDS", 0.05),
            patch("custom_components.hatch_rest.api.LIST_ACK_TIMEOUT_SECONDS", 0.05),
        ):
            # The block arrives but the acknowledgement never does.
            async def block_only(command):
                api._list_notification_received(None, bytearray(FAVORITE_BLOCK))

            with patch.object(api, "_write_list_command", side_effect=block_only):
                assert await api.async_refresh_favorite(1) is None

        # The contents were still recorded, since they did arrive.
        assert api.favorites[1]["volume"] == 84

    @staticmethod
    def _favorite_answers(api: PyHatchBabyRestAsync, block: bytes = FAVORITE_BLOCK):
        """Return a stand-in device that answers favorite commands."""
        sent = []

        async def answer(command):
            sent.append(command)
            if command.startswith("PGB"):
                api._list_notification_received(None, bytearray(block))
            api._list_notification_received(None, bytearray(b"OK"))

        return sent, answer

    @pytest.mark.asyncio
    async def test_set_favorite_keeps_what_was_not_given(
        self, api: PyHatchBabyRestAsync
    ):
        """Test changing one field leaves the rest of the slot alone.

        The commit writes every field at once, so anything not carried over
        from the current contents would be lost.
        """
        sent, answer = self._favorite_answers(api)

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_set_favorite(2, brightness=10)

        # Colour, sound and volume are the ones the block already held.
        assert "PSCfdd12d0a" in sent
        assert "PSN05" in sent
        assert "PSV54" in sent
        assert "PSLc0" in sent

    @pytest.mark.asyncio
    async def test_set_favorite_reads_the_slot_first_if_it_has_to(
        self, api: PyHatchBabyRestAsync
    ):
        """Test an unread slot is fetched before being rewritten."""
        sent, answer = self._favorite_answers(api)

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_set_favorite(4, volume=20)

        assert sent[0] == "PGB04"

    @pytest.mark.asyncio
    async def test_set_favorite_refuses_a_slot_it_cannot_read(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a slot that will not report is not written blind.

        Writing it would commit defaults over whatever it actually held.
        """
        with (
            patch.object(api, "_write_list_command", new_callable=AsyncMock),
            patch("custom_components.hatch_rest.api.LIST_REPLY_TIMEOUT_SECONDS", 0.01),
            patch("custom_components.hatch_rest.api.LIST_ACK_TIMEOUT_SECONDS", 0.01),
            pytest.raises(HatchRestConnectionError, match="favorite 5"),
        ):
            await api.async_set_favorite(5, volume=20)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slot", [0, 7, -1])
    async def test_set_favorite_rejects_a_slot_out_of_range(
        self, api: PyHatchBabyRestAsync, slot
    ):
        """Test only the six real slots are accepted."""
        with pytest.raises(ValueError, match="not between"):
            await api.async_set_favorite(slot, volume=1)

    @pytest.mark.asyncio
    async def test_save_favorite_writes_the_current_state(
        self, api: PyHatchBabyRestAsync
    ):
        """Test saving takes what the device is playing right now."""
        sent, answer = self._favorite_answers(api)
        api._apply_state(
            {
                "color": (10, 20, 30),
                "brightness": 40,
                "sound": PyHatchBabyRestSound.wind,
                "volume": 50,
                "power": True,
                "active_favorite": None,
            },
            "notification",
        )

        with (
            patch.object(api, "_write_list_command", side_effect=answer),
            patch.object(api, "set_active_favorite", new_callable=AsyncMock),
        ):
            await api.async_save_favorite(6)

        assert "PSB06" in sent
        assert "PSC0a141e28" in sent
        assert f"PSN{PyHatchBabyRestSound.wind:02x}" in sent
        assert "PSV32" in sent

    @pytest.mark.asyncio
    async def test_save_favorite_leaves_the_enabled_flag_alone(
        self, api: PyHatchBabyRestAsync
    ):
        """Test saving into a slot does not start offering it on the device.

        The captured block is an enabled slot; a disabled one must stay
        disabled rather than quietly join the touch ring rotation.
        """
        disabled = bytearray(FAVORITE_BLOCK)
        disabled[13] = 0x16
        sent, answer = self._favorite_answers(api, bytes(disabled))
        api._apply_state(
            {
                "color": (1, 2, 3),
                "brightness": 4,
                "sound": PyHatchBabyRestSound.rain,
                "volume": 5,
                "power": True,
                "active_favorite": None,
            },
            "notification",
        )

        with (
            patch.object(api, "_write_list_command", side_effect=answer),
            patch.object(api, "set_active_favorite", new_callable=AsyncMock),
        ):
            await api.async_save_favorite(5)

        assert "PSL80" in sent
        assert "PSLc0" not in sent

    @pytest.mark.asyncio
    async def test_save_favorite_selects_what_it_just_stored(
        self, api: PyHatchBabyRestAsync
    ):
        """Test saving leaves that favorite showing as the one playing.

        The device does not treat storing a favorite as selecting it, so it
        goes on reporting whichever was playing before -- or none, if the
        state had been set by hand, which is exactly the case where someone
        has just built something worth saving.
        """
        _, answer = self._favorite_answers(api)
        api._apply_state(
            {
                "color": (1, 2, 3),
                "brightness": 4,
                "sound": PyHatchBabyRestSound.rain,
                "volume": 5,
                "power": True,
                "active_favorite": None,
            },
            "notification",
        )

        with (
            patch.object(api, "_write_list_command", side_effect=answer),
            patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send,
        ):
            await api.async_save_favorite(4)

        mock_send.assert_awaited_once_with("SP04")
        assert api.active_favorite == 4

    @pytest.mark.asyncio
    async def test_save_favorite_selects_only_after_storing(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a save that failed does not select the slot anyway.

        Selecting one that was not written would play the old contents while
        claiming the new ones had been stored.
        """
        api._apply_state(
            {
                "color": (1, 2, 3),
                "brightness": 4,
                "sound": PyHatchBabyRestSound.rain,
                "volume": 5,
                "power": True,
                "active_favorite": None,
            },
            "notification",
        )

        async def never_answer(command):
            return

        with (
            patch.object(api, "_write_list_command", side_effect=never_answer),
            patch("custom_components.hatch_rest.api.LIST_REPLY_TIMEOUT_SECONDS", 0.01),
            patch("custom_components.hatch_rest.api.LIST_ACK_TIMEOUT_SECONDS", 0.01),
            patch.object(api, "_send_command", new_callable=AsyncMock) as mock_send,
            pytest.raises(HatchRestConnectionError),
        ):
            await api.async_save_favorite(4)

        mock_send.assert_not_awaited()
        assert api.active_favorite is None

    @pytest.mark.asyncio
    async def test_save_favorite_refuses_when_nothing_is_known(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a device that has not reported is not snapshotted.

        There would be nothing to snapshot, and writing the empty cache would
        blank the slot.
        """
        assert api.has_state is False

        with pytest.raises(HatchRestConnectionError, match="nothing to save"):
            await api.async_save_favorite(1)

    @pytest.mark.asyncio
    async def test_set_favorite_stops_before_committing_if_a_command_is_lost(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a dropped command aborts the write rather than pressing on.

        Nothing is stored until the commit, so stopping short of it leaves
        the slot as it was. Carrying on would be worse: had the lost command
        been the one selecting the slot, the commit would land on whichever
        slot the device still had selected.
        """
        sent = []

        async def answer(command):
            sent.append(command)
            if command.startswith("PGB"):
                api._list_notification_received(None, bytearray(FAVORITE_BLOCK))
            if command.startswith("PSN"):
                # This one goes unanswered.
                return
            api._list_notification_received(None, bytearray(b"OK"))

        with (
            patch.object(api, "_write_list_command", side_effect=answer),
            patch("custom_components.hatch_rest.api.LIST_ACK_TIMEOUT_SECONDS", 0.01),
            pytest.raises(HatchRestConnectionError, match="did not acknowledge PSN"),
        ):
            await api.async_set_favorite(2, brightness=10)

        assert "PSF" not in sent

    @pytest.mark.asyncio
    async def test_set_favorite_warns_when_the_slot_did_not_take(
        self, api: PyHatchBabyRestAsync, caplog: pytest.LogCaptureFixture
    ):
        """Test a slot that reads back differently is reported.

        The byte layout here is reverse engineered, so a write that lands
        somewhere unintended should say so rather than pass quietly.
        """
        _, answer = self._favorite_answers(api)

        with patch.object(api, "_write_list_command", side_effect=answer):
            # The device keeps answering with the original block, so the
            # brightness that was asked for is not what comes back.
            await api.async_set_favorite(2, brightness=10)

        assert "did not take what was asked for" in caplog.text

    @pytest.mark.asyncio
    async def test_set_favorite_is_quiet_when_it_took(
        self, api: PyHatchBabyRestAsync, caplog: pytest.LogCaptureFixture
    ):
        """Test writing back what the slot already held warns about nothing."""
        _, answer = self._favorite_answers(api)

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_set_favorite(2, brightness=127)

        assert "did not take what was asked for" not in caplog.text

    def test_favorite_reply_is_read_while_a_command_is_in_flight(
        self, api: PyHatchBabyRestAsync
    ):
        """Test favorite replies are not dropped by the state-update gate.

        That gate stops a stale reading of the device reverting an optimistic
        update. A favorite reply is an answer to a question we asked, so
        dropping it would hang the caller waiting for it.
        """
        api._commands_in_flight = 1
        api._slot_in_flight = 4
        api._block_kind_in_flight = BLOCK_FAVORITE

        api._list_notification_received(None, bytearray(FAVORITE_BLOCK))

        assert api.favorites[4]["volume"] == 84

    @pytest.mark.asyncio
    async def test_refresh_schedule_stores_what_came_back(
        self, api: PyHatchBabyRestAsync
    ):
        """Test asking for a schedule files the reply against that slot."""

        async def answer(command):
            assert command == "EGB04"
            api._list_notification_received(None, bytearray(SCHEDULE_BLOCK))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            schedule = await api.async_refresh_schedule(4)

        assert schedule is not None
        assert api.schedules[4]["volume"] == 40
        # And it did not end up filed as a favorite.
        assert api.favorites == {}

    @staticmethod
    def _at(api: PyHatchBabyRestAsync, *when: int):
        """Pretend it is a given local time, and record what is written.

        Naive on purpose. A local wall clock with no zone is the thing under
        test here, because it is what the device is told.
        """
        moment = datetime(*when)  # noqa: DTZ001
        sent = []

        async def answer(command):
            sent.append(command)
            api._list_notification_received(None, bytearray(b"OK"))

        return (
            sent,
            answer,
            patch(
                "custom_components.hatch_rest.api.datetime",
                **{"now.return_value": moment},
            ),
        )

    @pytest.mark.asyncio
    async def test_sync_clock_sends_the_local_wall_clock(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the device is told the time with no zone attached.

        Which is how it stores a schedule's start time, so converting would
        be converting to nothing.
        """
        sent, answer, clock = self._at(api, 2026, 9, 29, 14, 5, 3)

        with clock, patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_sync_clock()

        assert sent == ["ST20260929140503U"]

    @pytest.mark.asyncio
    async def test_sync_clock_happens_once_a_day(self, api: PyHatchBabyRestAsync):
        """Test a device already told today is not told again."""
        sent, answer, clock = self._at(api, 2026, 9, 29, 14, 5, 3)

        with clock, patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_sync_clock()
            await api.async_sync_clock()

        assert len(sent) == 1

    @pytest.mark.asyncio
    async def test_sync_clock_waits_out_the_small_hours(
        self, api: PyHatchBabyRestAsync
    ):
        """Test nothing is sent between midnight and half past two.

        In that window the local clock is ambiguous on the day the clocks go
        back and absent on the day they go forward, so a time sent then can
        be an hour out.
        """
        sent, answer, clock = self._at(api, 2026, 10, 25, 2, 29, 59)

        with clock, patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_sync_clock()

        assert sent == []
        assert api._clock_synced_on is None

    @pytest.mark.asyncio
    async def test_sync_clock_resumes_after_half_past_two(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the wait is over on the minute rather than the hour."""
        sent, answer, clock = self._at(api, 2026, 10, 25, 2, 30, 0)

        with clock, patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_sync_clock()

        assert sent == ["ST20261025023000U"]

    @pytest.mark.asyncio
    async def test_sync_clock_retries_if_it_was_not_acknowledged(
        self, api: PyHatchBabyRestAsync
    ):
        """Test an unacknowledged clock is not counted as set.

        There is no command to read the clock back, so the acknowledgement is
        the only confirmation there is.
        """
        _, _, clock = self._at(api, 2026, 9, 29, 14, 5, 3)

        with (
            clock,
            patch.object(api, "_write_list_command", new_callable=AsyncMock),
            patch("custom_components.hatch_rest.api.LIST_ACK_TIMEOUT_SECONDS", 0.01),
        ):
            await api.async_sync_clock()

        assert api._clock_synced_on is None

    @pytest.mark.asyncio
    async def test_the_sweep_sets_the_clock_before_reading(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the clock is set as part of connecting, ahead of the reads."""
        asked = []

        async def answer(command):
            asked.append(command)
            if command.startswith("PGB"):
                api._list_notification_received(None, bytearray(FAVORITE_BLOCK))
            elif command.startswith("EGB"):
                api._list_notification_received(None, bytearray(SCHEDULE_BLOCK))
            elif command in ("GI", "GD"):
                api._list_notification_received(None, bytearray(b"FF"))
            api._list_notification_received(None, bytearray(b"OK"))

        _, _, clock = self._at(api, 2026, 9, 29, 14, 5, 3)
        with clock, patch.object(api, "_write_list_command", side_effect=answer):
            await api._sweep()

        assert asked[0] == "ST20260929140503U"

    @pytest.mark.asyncio
    async def test_reading_a_block_tells_home_assistant(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the answer is published rather than just stored.

        Favorites and schedules do not go through _apply_state, which is what
        normally publishes, and that only fires when the device's own state
        changes. An idle Hatch can go minutes without one, so an answer that
        is only stored leaves the entities reading unknown until something
        unrelated happens to move.
        """
        published = MagicMock()
        api.set_state_changed_callback(published)

        async def answer(command):
            api._list_notification_received(None, bytearray(SCHEDULE_BLOCK))
            api._list_notification_received(None, bytearray(b"\x85Bed Time\x00"))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_schedule(2)

        # Once for the block, once for the name that followed it.
        assert published.call_count == 2

    @pytest.mark.asyncio
    async def test_refresh_schedules_asks_for_every_slot(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the sweep covers all ten slots, in order."""
        asked = []

        async def answer(command):
            asked.append(command)
            api._list_notification_received(None, bytearray(SCHEDULE_BLOCK))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_schedules()

        assert asked == [f"EGB{slot:02X}" for slot in range(1, 11)]
        assert sorted(api.schedules) == list(range(1, 11))

    @pytest.mark.asyncio
    async def test_a_schedule_reply_is_not_read_as_a_favorite(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a block of the wrong length is dropped, not misread.

        Both kinds arrive under the same 0x01 header. Reading a 20 byte
        schedule with the favorite layout would silently produce a plausible
        favorite from the wrong bytes.
        """

        async def answer(command):
            api._list_notification_received(None, bytearray(SCHEDULE_BLOCK))
            api._list_notification_received(None, bytearray(b"OK"))

        with (
            patch.object(api, "_write_list_command", side_effect=answer),
            patch("custom_components.hatch_rest.api.LIST_REPLY_TIMEOUT_SECONDS", 0.01),
        ):
            assert await api.async_refresh_favorite(1) is None

        assert api.favorites == {}

    @pytest.mark.asyncio
    async def test_a_favorite_reply_is_not_read_as_a_schedule(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the same guard the other way round."""

        async def answer(command):
            api._list_notification_received(None, bytearray(FAVORITE_BLOCK))
            api._list_notification_received(None, bytearray(b"OK"))

        with (
            patch.object(api, "_write_list_command", side_effect=answer),
            patch("custom_components.hatch_rest.api.LIST_REPLY_TIMEOUT_SECONDS", 0.01),
        ):
            assert await api.async_refresh_schedule(1) is None

        assert api.schedules == {}

    @pytest.mark.asyncio
    async def test_the_sweep_reads_favorites_and_schedules(
        self, api: PyHatchBabyRestAsync
    ):
        """Test one connection reads everything the device stores."""
        asked = []

        async def answer(command):
            asked.append(command)
            if command.startswith("PGB"):
                api._list_notification_received(None, bytearray(FAVORITE_BLOCK))
            else:
                api._list_notification_received(None, bytearray(SCHEDULE_BLOCK))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api._sweep()

        assert len([c for c in asked if c.startswith("PGB")]) == 6
        assert len([c for c in asked if c.startswith("EGB")]) == 10
        assert len(api.favorites) == 6
        assert len(api.schedules) == 10

    @staticmethod
    def _timer_answers(api: PyHatchBabyRestAsync):
        """Return a stand-in device that answers timer commands as idle."""
        sent = []

        async def answer(command):
            sent.append(command)
            if command in ("GI", "GD"):
                api._list_notification_received(None, bytearray(b"FF"))
            api._list_notification_received(None, bytearray(b"OK"))

        return sent, answer

    @pytest.mark.asyncio
    async def test_set_timer_sends_seconds(self, api: PyHatchBabyRestAsync):
        """Test minutes are converted, since the device is set in seconds.

        It reports what is left in minutes, so the two directions differ.
        """
        sent, answer = self._timer_answers(api)

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_set_timer(15)

        # 15 minutes is 900 seconds, which is 0x0384.
        assert sent[0] == "SD0384"

    @pytest.mark.asyncio
    async def test_set_timer_reads_it_back(self, api: PyHatchBabyRestAsync):
        """Test the device is asked what it made of the setting.

        What it reports for the total has never been seen with a timer
        actually running, so this is how that gets found out.
        """
        sent, answer = self._timer_answers(api)

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_set_timer(15)

        assert "GI" in sent

    @pytest.mark.asyncio
    async def test_set_timer_zero_cancels(self, api: PyHatchBabyRestAsync):
        """Test zero is sent as a duration rather than refused."""
        sent, answer = self._timer_answers(api)

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_set_timer(0)

        assert sent[0] == "SD0000"
        assert api.timer_remaining is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("minutes", [-1, 121])
    async def test_set_timer_refuses_what_the_device_cannot_hold(
        self, api: PyHatchBabyRestAsync, minutes
    ):
        """Test a duration outside the range is refused before being sent."""
        with pytest.raises(ValueError, match="not between"):
            await api.async_set_timer(minutes)

    @pytest.mark.asyncio
    async def test_set_timer_raises_when_it_is_not_acknowledged(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a timer the device never acknowledged is reported, not assumed."""
        with (
            patch.object(api, "_write_list_command", new_callable=AsyncMock),
            patch("custom_components.hatch_rest.api.LIST_ACK_TIMEOUT_SECONDS", 0.01),
            pytest.raises(HatchRestConnectionError, match="did not accept"),
        ):
            await api.async_set_timer(15)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("idle_reply", [b"FF", b"00"])
    async def test_refresh_timer_reads_an_idle_device(
        self, api: PyHatchBabyRestAsync, idle_reply
    ):
        """Test both ways a device says it has no timer.

        Three of four devices here answer FF, which is what the protocol
        notes describe. The fourth answers 00, and reading that as a duration
        would report a timer permanently sitting at zero.
        """
        asked = []

        async def answer(command):
            asked.append(command)
            api._list_notification_received(None, bytearray(idle_reply))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_timer()

        assert asked == ["GI"]
        assert api.timer_remaining is None
        assert api.timer_total is None

    @pytest.mark.asyncio
    async def test_refresh_timer_treats_nothing_left_as_no_timer(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a timer with zero minutes left is not a timer."""

        async def answer(command):
            api._list_notification_received(
                None, bytearray(b"0020" if command == "GI" else b"0000")
            )
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_timer()

        assert api.timer_remaining is None

    @pytest.mark.asyncio
    async def test_refresh_timer_reads_the_remaining_minutes(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a running timer is read as hex minutes."""
        asked = []

        async def answer(command):
            asked.append(command)
            if command == "GI":
                api._list_notification_received(None, bytearray(b"0020"))
            else:
                api._list_notification_received(None, bytearray(b"0076"))
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_timer()

        assert asked == ["GI", "GD"]
        # 0x76 is 118, not 76 -- the reply is hex.
        assert api.timer_remaining == 118

    @pytest.mark.asyncio
    async def test_timer_counts_down_without_asking_again(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the remaining time moves on its own between reads.

        The device is asked once per connection; pestering it every poll for
        something that only moves one way would be a round trip wasted.
        """

        async def answer(command):
            api._list_notification_received(
                None, bytearray(b"0020" if command == "GI" else b"000a")
            )
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_timer()

        assert api.timer_remaining == 10

        # Five minutes later, without having asked anything.
        api._timer_expires_at -= 5 * 60
        assert api.timer_remaining == 5

    def test_timer_never_reads_below_zero(self, api: PyHatchBabyRestAsync):
        """Test an expired timer reads as zero rather than negative."""
        api._timer_expires_at = monotonic() - 600

        assert api.timer_remaining == 0

    @pytest.mark.asyncio
    async def test_refresh_timer_survives_an_answer_it_cannot_read(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a reply that is not hex leaves the timer unknown."""

        async def answer(command):
            api._list_notification_received(
                None, bytearray(b"0020" if command == "GI" else b"what")
            )
            api._list_notification_received(None, bytearray(b"OK"))

        with patch.object(api, "_write_list_command", side_effect=answer):
            await api.async_refresh_timer()

        assert api.timer_remaining is None

    def test_favorite_block_with_nothing_in_flight_is_ignored(
        self, api: PyHatchBabyRestAsync
    ):
        """Test an unsolicited block is not filed against a guessed slot."""
        api._slot_in_flight = None

        api._list_notification_received(None, bytearray(FAVORITE_BLOCK))

        assert api.favorites == {}

    @pytest.mark.asyncio
    async def test_favorite_command_does_not_open_the_settle_window(
        self, api: PyHatchBabyRestAsync
    ):
        """Test reading a favorite does not suppress real state updates.

        Commands hold off advertisements because they change what the device
        is doing. Reading a stored favorite does not, so blinding ourselves
        for two seconds either side of it would cost updates for nothing.
        """
        mock_client = AsyncMock()
        api._client = mock_client
        api._settle_until = 0.0

        with patch.object(api, "_client_connect", new_callable=AsyncMock):
            await api._write_list_command("PGB01")

        mock_client.write_gatt_char.assert_awaited_once()
        assert api._settle_until == 0.0
        assert api._commands_in_flight == 0
        assert api._active_operations == 0

    def test_no_sweep_when_the_device_cannot_report_favorites(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a device that refused the subscription is not swept.

        Every slot would time out, six times over, on every reconnect.
        """
        api._list_supported = False

        api._start_sweep()

        assert api._sweep_task is None

    def test_has_state_and_advertisement_age(self, api: PyHatchBabyRestAsync):
        """Test state and freshness are only known after a parse."""
        assert api.has_state is False
        assert api.seconds_since_state_update() == float("inf")

        assert api.update_from_advertisement(ADVERTISEMENT) is True

        assert api.has_state is True
        assert api.seconds_since_state_update() < 1

    def test_advertisement_age_comes_from_when_it_arrived(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a replayed advertisement is aged from when it was picked up.

        Home Assistant keeps handing out the last advertisement it saw, so
        seeding from one at startup must not reset the clock or a device
        switched off days ago looks like it just reported in.
        """
        two_days_ago = monotonic() - (2 * 24 * 60 * 60)

        assert api.update_from_advertisement(ADVERTISEMENT, two_days_ago) is True

        assert api.has_state is True
        assert api.seconds_since_state_update() > 24 * 60 * 60

    @pytest.mark.asyncio
    async def test_command_does_not_make_a_silent_device_look_fresh(
        self, api: PyHatchBabyRestAsync
    ):
        """Test commanding a device is not evidence it is still there.

        Commands apply optimistically so entities follow them at once, but
        that is this end talking. Letting it count as a report would keep an
        unplugged Hatch looking alive for as long as it was being poked.
        """
        api.update_from_advertisement(ADVERTISEMENT, monotonic() - 600)
        before = api.seconds_since_state_update()

        with patch.object(api, "_send_command", new_callable=AsyncMock):
            await api.turn_power_on()

        assert api.power is True
        assert api.seconds_since_state_update() >= before

    def test_unparseable_advertisement_leaves_state_unknown(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a payload that does not parse does not count as state."""
        assert api.update_from_advertisement(b"\x00\x01\x02") is False

        assert api.has_state is False
        assert api.seconds_since_state_update() == float("inf")

    @pytest.mark.asyncio
    async def test_advertisement_does_not_revert_fresh_command(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a stale advertisement does not undo what was just written."""
        with (
            patch.object(api, "_client_connect", new_callable=AsyncMock),
            patch.object(api, "_client_disconnect", new_callable=AsyncMock),
        ):
            api._client = AsyncMock()
            await api.turn_power_on()

        assert api.power is True

        # ADVERTISEMENT still describes the device as powered off, which
        # would revert the entity if it were applied.
        assert api.update_from_advertisement(ADVERTISEMENT) is False
        assert api.power is True

        # Once the command has settled, the advertisement is authoritative.
        api._settle_until = 0.0
        assert api.update_from_advertisement(ADVERTISEMENT) is True
        assert api.power is False

    @pytest.mark.asyncio
    async def test_send_command_keeps_the_connection(self, api: PyHatchBabyRestAsync):
        """Test a command leaves the connection up for the next one."""
        mock_client = AsyncMock()
        api._client = mock_client

        with (
            patch.object(api, "_client_connect", new_callable=AsyncMock),
            patch.object(api, "_client_disconnect", new_callable=AsyncMock) as mock_dc,
        ):
            await api._send_command("SI01")

        mock_dc.assert_not_called()
        assert api._client is mock_client

    @pytest.mark.asyncio
    async def test_start_connects_and_stop_disconnects(self, api: PyHatchBabyRestAsync):
        """Test the connection is established up front and released on stop."""
        mock_client = AsyncMock()
        mock_client.is_connected = True
        mock_client.services = MagicMock()

        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            await api.async_start()

        assert api._client is mock_client

        await api.async_stop()

        assert api._keep_connected is False
        mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_connect_is_retried(self, api: PyHatchBabyRestAsync):
        """Test a device that will not connect is tried again later."""
        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            side_effect=BleakConnectionError("nope"),
        ):
            await api.async_start()

        assert api._client is None
        assert api._reconnect_timer is not None

        await api.async_stop()

        assert api._reconnect_timer is None

    @pytest.mark.asyncio
    async def test_dropped_connection_is_reconnected(self, api: PyHatchBabyRestAsync):
        """Test losing the link schedules a reconnect.

        Notifications stop with the connection, so it has to come back.
        """
        api._keep_connected = True

        api._client_disconnected(MagicMock())

        assert api._client is None
        assert api._reconnect_timer is not None

        api._cancel_reconnect()

    @pytest.mark.asyncio
    async def test_drop_reconnects_without_backing_off(self, api: PyHatchBabyRestAsync):
        """Test a dropped link is retried promptly, not after a backoff.

        Holding connections starves the proxy's scanner, so a disconnected
        device is not covered by advertisements either. Backing off would
        leave it unseen. Only connects that fail outright escalate.
        """
        api._keep_connected = True
        api._reconnect_delay = MAX_RECONNECT_DELAY_SECONDS

        with patch.object(api, "_schedule_reconnect") as mock_schedule:
            api._client_disconnected(MagicMock())

        mock_schedule.assert_called_once_with(RECONNECT_DELAY_SECONDS)

    @pytest.mark.asyncio
    async def test_failed_poll_does_not_retire_pending_reconnect(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a failing poll leaves the scheduled reconnect in place.

        Only _connect_and_retry schedules reconnects, so a poll that quietly
        cancelled the pending one and then failed itself would end
        reconnection permanently -- observed in the wild as a device that
        stayed on backstop polling until Home Assistant was restarted.
        """
        api._keep_connected = True

        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            side_effect=BleakConnectionError("nope"),
        ):
            await api.async_start()
            assert api._reconnect_timer is not None
            pending = api._reconnect_timer

            with pytest.raises(HatchRestConnectionError):
                await api.refresh_data()

        assert api._reconnect_timer is pending
        assert not pending.cancelled()

        await api.async_stop()

    @pytest.mark.asyncio
    async def test_successful_connect_retires_pending_reconnect(
        self, api: PyHatchBabyRestAsync
    ):
        """Test a connection that succeeds cancels the retry it made moot."""
        api._keep_connected = True

        with patch(
            "custom_components.hatch_rest.api.establish_connection",
            new_callable=AsyncMock,
            side_effect=BleakConnectionError("nope"),
        ):
            await api.async_start()

        assert api._reconnect_timer is not None

        mock_client = AsyncMock()
        mock_client.is_connected = True
        with (
            patch(
                "custom_components.hatch_rest.api.establish_connection",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch.object(api, "_start_notifications", new_callable=AsyncMock),
        ):
            await api._client_connect()

        assert api._client is mock_client
        assert api._reconnect_timer is None

    @pytest.mark.asyncio
    async def test_no_reconnect_after_stop(self, api: PyHatchBabyRestAsync):
        """Test a disconnect during shutdown does not resurrect the link."""
        api._keep_connected = False

        api._client_disconnected(MagicMock())

        assert api._reconnect_timer is None

    def test_active_operations_tracking(self, api: PyHatchBabyRestAsync):
        """Test active operations counter."""
        assert api._active_operations == 0
        api._set_active_operations(1)
        assert api._active_operations == 1
        api._set_active_operations(1)
        assert api._active_operations == 2
        api._set_active_operations(-1)
        assert api._active_operations == 1
