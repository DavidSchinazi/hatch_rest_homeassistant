"""Tests for Hatch Rest coordinator."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hatch_rest.api import PyHatchBabyRestAsync

# A feedback payload captured from a Rest 1st Gen.
FEEDBACK = bytes.fromhex("54f8001c9643fdd12d7f53055450df6500000000")
from custom_components.hatch_rest.const import (
    ADVERTISEMENT_STALE_SECONDS,
    DOMAIN,
    MANUFACTURER_ID,
    PyHatchBabyRestSound,
)
from custom_components.hatch_rest.coordinator import (
    HatchBabyRestEntity,
    HatchBabyRestUpdateCoordinator,
)


class TestHatchBabyRestUpdateCoordinator:
    """Tests for HatchBabyRestUpdateCoordinator."""

    def test_init(self, hass: HomeAssistant, mock_hatch_api: AsyncMock):
        """Test coordinator initialization."""
        coordinator = HatchBabyRestUpdateCoordinator(
            hass,
            unique_id="aabbccddeeff",
            hatch_rest_device=mock_hatch_api,
        )

        assert coordinator.unique_id == "aabbccddeeff"
        assert coordinator.hatch_rest_device == mock_hatch_api
        assert coordinator.name == DOMAIN
        assert coordinator.update_interval == timedelta(seconds=90)

    @pytest.mark.asyncio
    async def test_update_skips_connecting_while_advertisements_are_fresh(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a recent advertisement means no connection is opened."""
        device = mock_coordinator.hatch_rest_device
        device.seconds_since_state_update = MagicMock(return_value=5.0)

        data = await mock_coordinator._async_update_data()

        device.refresh_data.assert_not_called()
        assert data == mock_coordinator.get_current_data()

    @pytest.mark.asyncio
    async def test_update_connects_once_advertisements_go_stale(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a device that stopped advertising is read over GATT."""
        device = mock_coordinator.hatch_rest_device
        device.seconds_since_state_update = MagicMock(
            return_value=ADVERTISEMENT_STALE_SECONDS + 1
        )

        await mock_coordinator._async_update_data()

        device.refresh_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_publishes_state_before_the_write_lands(
        self, hass: HomeAssistant, mock_ble_device
    ):
        """Test an entity follows a command without waiting for the device.

        Connecting has been seen to take 11s, so the state a command asks for
        is published as soon as it is asked for, not once the device has
        acknowledged it.
        """
        device = PyHatchBabyRestAsync(mock_ble_device)
        coordinator = HatchBabyRestUpdateCoordinator(
            hass,
            unique_id="aabbccddeeff",
            hatch_rest_device=device,
        )
        device.power = False

        connected = asyncio.Event()

        async def blocked_connect():
            await connected.wait()
            device._client = AsyncMock()

        with patch.object(device, "_client_connect", blocked_connect):
            command = asyncio.create_task(device.turn_power_on())
            await asyncio.sleep(0)

            # Still connecting, but the entity already knows.
            assert command.done() is False
            assert coordinator.data["power"] is True

            connected.set()
            await asyncio.wait_for(command, timeout=5)

        device._cancel_reconnect()

    @pytest.mark.asyncio
    async def test_advertisement_during_a_command_does_not_revert_it(
        self, hass: HomeAssistant, mock_ble_device
    ):
        """Test a stale advertisement mid-command does not undo the command.

        The settle window has to cover the connection too, not just the
        moment after the write.
        """
        device = PyHatchBabyRestAsync(mock_ble_device)
        coordinator = HatchBabyRestUpdateCoordinator(
            hass,
            unique_id="aabbccddeeff",
            hatch_rest_device=device,
        )
        service_info = MagicMock()
        service_info.manufacturer_data = {
            MANUFACTURER_ID: bytes.fromhex(
                "5254f8001ccc43fdd12d7f53055445000000000050df6500"
            )
        }

        connected = asyncio.Event()

        async def blocked_connect():
            await connected.wait()
            device._client = AsyncMock()

        with patch.object(device, "_client_connect", blocked_connect):
            command = asyncio.create_task(device.turn_power_on())
            await asyncio.sleep(0)

            # The device is still advertising that it is off.
            coordinator.async_handle_advertisement(service_info, MagicMock())
            assert coordinator.data["power"] is True

            connected.set()
            await asyncio.wait_for(command, timeout=5)

        device._cancel_reconnect()

    @pytest.mark.asyncio
    async def test_notification_keeps_state_fresh(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator, mock_ble_device
    ):
        """Test a connected device is not polled for state it already pushes.

        A connected device stops advertising, so notifications have to count
        as fresh state or the backstop would connect for a read it does not
        need.
        """
        device = PyHatchBabyRestAsync(mock_ble_device)
        device.refresh_data = AsyncMock()
        mock_coordinator.hatch_rest_device = device

        device._notification_received(MagicMock(), bytearray(FEEDBACK))

        assert device.seconds_since_state_update() < 1
        await mock_coordinator._async_update_data()
        device.refresh_data.assert_not_called()

    def test_handle_advertisement_updates_listeners(
        self, hass: HomeAssistant, mock_ble_device
    ):
        """Test an advertisement updates state without a connection."""
        coordinator = HatchBabyRestUpdateCoordinator(
            hass,
            unique_id="aabbccddeeff",
            hatch_rest_device=PyHatchBabyRestAsync(mock_ble_device),
        )
        service_info = MagicMock()
        service_info.manufacturer_data = {
            MANUFACTURER_ID: bytes.fromhex(
                "5254f8001ccc43fdd12d7f53055445000000000050df6500"
            )
        }

        coordinator.async_handle_advertisement(service_info, MagicMock())

        assert coordinator.data["color"] == (253, 209, 45)
        assert coordinator.data["brightness"] == 127
        assert coordinator.data["sound"] == PyHatchBabyRestSound.ocean
        assert coordinator.data["volume"] == 84
        assert coordinator.data["power"] is False

    def test_handle_advertisement_ignores_unchanged(
        self, hass: HomeAssistant, mock_ble_device
    ):
        """Test a repeated advertisement does not re-notify listeners."""
        coordinator = HatchBabyRestUpdateCoordinator(
            hass,
            unique_id="aabbccddeeff",
            hatch_rest_device=PyHatchBabyRestAsync(mock_ble_device),
        )
        service_info = MagicMock()
        service_info.manufacturer_data = {
            MANUFACTURER_ID: bytes.fromhex(
                "5254f8001ccc43fdd12d7f53055445000000000050df6500"
            )
        }
        coordinator.async_handle_advertisement(service_info, MagicMock())

        listener = MagicMock()
        unsub = coordinator.async_add_listener(listener)
        listener.reset_mock()
        coordinator.async_handle_advertisement(service_info, MagicMock())
        unsub()

        listener.assert_not_called()

    def test_get_current_data(self, mock_coordinator: HatchBabyRestUpdateCoordinator):
        """Test get_current_data returns device state."""
        data = mock_coordinator.get_current_data()

        assert data["brightness"] == 128
        assert data["color"] == (255, 128, 64)
        assert data["power"] is True
        assert data["sound"] == PyHatchBabyRestSound.ocean
        assert data["volume"] == 100

    @pytest.mark.asyncio
    async def test_async_update_data_success(
        self, hass: HomeAssistant, mock_hatch_api: AsyncMock
    ):
        """Test successful data update."""
        coordinator = HatchBabyRestUpdateCoordinator(
            hass,
            unique_id="aabbccddeeff",
            hatch_rest_device=mock_hatch_api,
        )

        data = await coordinator._async_update_data()

        mock_hatch_api.refresh_data.assert_called_once()
        assert data["brightness"] == 128
        assert data["color"] == (255, 128, 64)
        assert data["power"] is True

    @pytest.mark.asyncio
    async def test_async_update_data_failure_with_cache(
        self, hass: HomeAssistant, mock_hatch_api: AsyncMock
    ):
        """Test update failure returns cached data when available."""
        coordinator = HatchBabyRestUpdateCoordinator(
            hass,
            unique_id="aabbccddeeff",
            hatch_rest_device=mock_hatch_api,
        )

        # Set up cached data
        coordinator.data = {
            "brightness": 50,
            "color": (100, 100, 100),
            "power": False,
            "sound": PyHatchBabyRestSound.rain,
            "volume": 50,
        }

        mock_hatch_api.refresh_data.side_effect = Exception("Connection failed")

        data = await coordinator._async_update_data()

        # Should return cached data
        assert data["brightness"] == 50
        assert data["power"] is False

    @pytest.mark.asyncio
    async def test_async_update_data_failure_without_cache(
        self, hass: HomeAssistant, mock_hatch_api: AsyncMock
    ):
        """Test update failure raises when no cached data."""
        coordinator = HatchBabyRestUpdateCoordinator(
            hass,
            unique_id="aabbccddeeff",
            hatch_rest_device=mock_hatch_api,
        )
        coordinator.data = None

        mock_hatch_api.refresh_data.side_effect = Exception("Connection failed")

        with pytest.raises(UpdateFailed, match="Device update failed"):
            await coordinator._async_update_data()


class TestHatchBabyRestEntity:
    """Tests for HatchBabyRestEntity."""

    def test_init(self, mock_coordinator: HatchBabyRestUpdateCoordinator):
        """Test entity initialization."""
        entity = HatchBabyRestEntity(mock_coordinator)

        assert entity._hatch_rest_device == mock_coordinator.hatch_rest_device
        assert entity._attr_unique_id == "aabbccddeeff"

    def test_device_info(self, mock_coordinator: HatchBabyRestUpdateCoordinator):
        """Test device_info property."""
        entity = HatchBabyRestEntity(mock_coordinator)

        device_info = entity.device_info

        assert device_info["manufacturer"] == "Hatch"
        assert device_info["model"] == "Rest"
        assert device_info["name"] == "Hatch Rest"
        assert ("hatch_rest", "aabbccddeeff") in device_info["identifiers"]

    def test_device_info_missing_address_raises(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test device_info raises when address is missing."""
        mock_coordinator.hatch_rest_device.address = None
        entity = HatchBabyRestEntity(mock_coordinator)

        with pytest.raises(ValueError, match="Missing bluetooth address"):
            _ = entity.device_info

    def test_device_info_missing_unique_id_raises(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test device_info raises when unique_id is missing."""
        mock_coordinator.unique_id = None
        entity = HatchBabyRestEntity(mock_coordinator)

        with pytest.raises(ValueError, match="Missing bluetooth address"):
            _ = entity.device_info

    def test_device_name(self, mock_coordinator: HatchBabyRestUpdateCoordinator):
        """Test device_name property."""
        entity = HatchBabyRestEntity(mock_coordinator)

        assert entity.device_name == "Hatch Rest"
