"""Tests for Hatch Rest API."""

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakConnectionError

from custom_components.hatch_rest.api import (
    PyHatchBabyRestAsync,
    _assert_marker,
    _parse_state,
)
from custom_components.hatch_rest.const import (
    ADVERTISEMENT_COLOR_INDEX,
    ADVERTISEMENT_POWER_INDEX,
    ADVERTISEMENT_SOUND_INDEX,
    CHAR_TX,
    FEEDBACK_COLOR_INDEX,
    FEEDBACK_POWER_INDEX,
    FEEDBACK_SOUND_INDEX,
    MARKER_COLOR,
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

    def test_parse_rejects_misaligned_payload(self):
        """Test a payload without the expected markers is rejected."""
        with pytest.raises(ValueError):
            _parse_state(
                ADVERTISEMENT,
                FEEDBACK_COLOR_INDEX,
                FEEDBACK_SOUND_INDEX,
                FEEDBACK_POWER_INDEX,
            )


class TestPyHatchBabyRestAsync:
    """Tests for PyHatchBabyRestAsync."""

    @pytest.fixture
    def api(self, mock_ble_device: BLEDevice) -> Generator[PyHatchBabyRestAsync]:
        """Create API instance, cancelling any pending idle disconnect."""
        api = PyHatchBabyRestAsync(mock_ble_device)
        yield api
        api._cancel_idle_disconnect()

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
    async def test_send_command_schedules_idle_disconnect(
        self, api: PyHatchBabyRestAsync
    ):
        """Test the connection is left open for a later idle disconnect."""
        mock_client = AsyncMock()
        api._client = mock_client

        with (
            patch.object(api, "_client_connect", new_callable=AsyncMock),
            patch.object(api, "_client_disconnect", new_callable=AsyncMock) as mock_dc,
        ):
            await api._send_command("SI01")
            assert api._disconnect_timer is not None
            mock_dc.assert_not_called()

            api._cancel_idle_disconnect()

        assert api._disconnect_timer is None

    @pytest.mark.asyncio
    async def test_client_connect_cancels_idle_disconnect(
        self, api: PyHatchBabyRestAsync
    ):
        """Test new work cancels a pending idle disconnect."""
        mock_client = MagicMock()
        mock_client.is_connected = True
        api._client = mock_client
        api._schedule_idle_disconnect()

        await api._client_connect()

        assert api._disconnect_timer is None

    @pytest.mark.asyncio
    async def test_async_stop_disconnects(self, api: PyHatchBabyRestAsync):
        """Test async_stop cancels the timer and disconnects."""
        mock_client = AsyncMock()
        api._client = mock_client
        api._schedule_idle_disconnect()

        await api.async_stop()

        assert api._disconnect_timer is None
        mock_client.disconnect.assert_called_once()

    def test_active_operations_tracking(self, api: PyHatchBabyRestAsync):
        """Test active operations counter."""
        assert api._active_operations == 0
        api._set_active_operations(1)
        assert api._active_operations == 1
        api._set_active_operations(1)
        assert api._active_operations == 2
        api._set_active_operations(-1)
        assert api._active_operations == 1
