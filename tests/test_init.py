"""Tests for Hatch Rest integration setup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hatch_rest import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
    options_update_listener,
)
from custom_components.hatch_rest.api import PyHatchBabyRestAsync
from custom_components.hatch_rest.const import DOMAIN, MANUFACTURER_ID

# An advertisement captured from a Rest 1st Gen.
ADVERTISEMENT = bytes.fromhex("5254f8001ccc43fdd12d7f53055445000000000050df6500")


class TestAsyncSetupEntry:
    """Tests for async_setup_entry."""

    @pytest.fixture
    def mock_entry(self, hass: HomeAssistant) -> MockConfigEntry:
        """Create a real config entry.

        A MagicMock would accept anything async_setup_entry does to it,
        including async_on_unload, so use an entry that behaves like one.
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="aabbccddeeff",
            data={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"},
        )
        entry.add_to_hass(hass)
        return entry

    @pytest.mark.asyncio
    async def test_setup_entry_seeds_from_advertisement(
        self,
        hass: HomeAssistant,
        mock_entry: MockConfigEntry,
        mock_ble_device: BLEDevice,
    ):
        """Test setup takes its initial state from a cached advertisement."""
        service_info = MagicMock()
        service_info.manufacturer_data = {MANUFACTURER_ID: ADVERTISEMENT}

        with (
            patch(
                "custom_components.hatch_rest.bluetooth.async_ble_device_from_address",
                return_value=mock_ble_device,
            ),
            patch(
                "custom_components.hatch_rest.bluetooth.async_last_service_info",
                return_value=service_info,
            ),
            patch(
                "custom_components.hatch_rest.bluetooth.async_register_callback",
                return_value=lambda: None,
            ) as mock_register,
            patch(
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ) as mock_forward,
            patch.object(
                PyHatchBabyRestAsync, "refresh_data", new_callable=AsyncMock
            ) as mock_refresh,
        ):
            result = await async_setup_entry(hass, mock_entry)

        assert result is True
        mock_forward.assert_called_once()
        mock_register.assert_called_once()
        # The advertisement was enough, so setup never opened a connection.
        mock_refresh.assert_not_called()

        # Seeded with no connection, so no GATT read was needed.
        coordinator = mock_entry.runtime_data
        assert coordinator.data["color"] == (253, 209, 45)
        assert coordinator.data["brightness"] == 127
        assert coordinator.data["power"] is False

    @pytest.mark.asyncio
    async def test_setup_entry_device_not_found(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ):
        """Test setup entry fails when device not found."""
        with (
            patch(
                "custom_components.hatch_rest.bluetooth.async_ble_device_from_address",
                return_value=None,
            ),
            pytest.raises(ConfigEntryNotReady, match="Could not find"),
        ):
            await async_setup_entry(hass, mock_entry)

    @pytest.mark.asyncio
    async def test_setup_entry_refresh_fails(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ):
        """Test setup entry fails when initial refresh fails."""
        mock_ble_device = MagicMock()
        mock_ble_device.address = "AA:BB:CC:DD:EE:FF"
        mock_ble_device.name = "Hatch Rest"

        mock_api = MagicMock()
        mock_api.device = mock_ble_device
        mock_api.address = mock_ble_device.address
        mock_api.name = "Hatch Rest"
        mock_api.brightness = None
        mock_api.color = None
        mock_api.power = None
        mock_api.sound = None
        mock_api.volume = None
        mock_api.refresh_data = AsyncMock(side_effect=Exception("Connection failed"))
        # Nothing was learned from an advertisement, so setup has to connect.
        mock_api.has_state = False
        mock_api.seconds_since_advertisement = MagicMock(return_value=float("inf"))

        # Without a cached advertisement setup falls back to reading over
        # GATT, which is allowed to fail and be retried later.
        mock_entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        with (
            patch(
                "custom_components.hatch_rest.bluetooth.async_ble_device_from_address",
                return_value=mock_ble_device,
            ),
            patch(
                "custom_components.hatch_rest.bluetooth.async_last_service_info",
                return_value=None,
            ),
            patch(
                "custom_components.hatch_rest.bluetooth.async_register_callback",
                return_value=lambda: None,
            ),
            patch(
                "custom_components.hatch_rest.PyHatchBabyRestAsync",
                return_value=mock_api,
            ),
            pytest.raises(ConfigEntryNotReady),
        ):
            await async_setup_entry(hass, mock_entry)


class TestAsyncUnloadEntry:
    """Tests for async_unload_entry."""

    @pytest.mark.asyncio
    async def test_unload_entry(self, hass: HomeAssistant):
        """Test unloading entry."""
        mock_entry = MagicMock(spec=ConfigEntry)
        mock_entry.entry_id = "test_entry"

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_unload:
            result = await async_unload_entry(hass, mock_entry)

        assert result is True
        mock_unload.assert_called_once_with(mock_entry, PLATFORMS)


class TestOptionsUpdateListener:
    """Tests for options_update_listener."""

    @pytest.mark.asyncio
    async def test_options_update_reloads_entry(self, hass: HomeAssistant):
        """Test options update triggers reload."""
        mock_entry = MagicMock(spec=ConfigEntry)
        mock_entry.entry_id = "test_entry"

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            new_callable=AsyncMock,
        ) as mock_reload:
            await options_update_listener(hass, mock_entry)

        mock_reload.assert_called_once_with("test_entry")
