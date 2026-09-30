"""Tests for Hatch Rest integration setup."""

from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hatch_rest import (
    PLATFORMS,
    _remove_timer_number,
    async_setup_entry,
    async_unload_entry,
    options_update_listener,
)
from custom_components.hatch_rest.api import PyHatchBabyRestAsync
from custom_components.hatch_rest.const import DOMAIN, MANUFACTURER_ID
from custom_components.hatch_rest.coordinator import HatchBabyRestEntity

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
        service_info.time = monotonic()

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
            patch.object(
                PyHatchBabyRestAsync, "async_start", new_callable=AsyncMock
            ) as mock_start,
        ):
            result = await async_setup_entry(hass, mock_entry)
            await hass.async_block_till_done()

        assert result is True
        mock_forward.assert_called_once()
        mock_register.assert_called_once()
        # The advertisement was enough, so setup never opened a connection.
        mock_refresh.assert_not_called()
        # But it does start holding one, so commands do not have to wait for
        # a connect that can take ten seconds on a weak link.
        mock_start.assert_called_once()

        # Seeded with no connection, so no GATT read was needed.
        coordinator = mock_entry.runtime_data
        assert coordinator.data["color"] == (253, 209, 45)
        assert coordinator.data["brightness"] == 127
        assert coordinator.data["power"] is False

    @pytest.mark.asyncio
    async def test_setup_entry_stale_advertisement_loads_unavailable(
        self,
        hass: HomeAssistant,
        mock_entry: MockConfigEntry,
        mock_ble_device: BLEDevice,
    ):
        """Test a long-stale cached advertisement does not look like a live device.

        Home Assistant hands out the last advertisement it saw regardless of
        age, so a Hatch switched off days ago still seeds state at setup.
        Taking that as current left the entities available, reporting the
        state the device had when it was unplugged, until a poll eventually
        failed.
        """
        service_info = MagicMock()
        service_info.manufacturer_data = {MANUFACTURER_ID: ADVERTISEMENT}
        service_info.time = monotonic() - (2 * 24 * 60 * 60)

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
            ),
            patch(
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
            patch.object(PyHatchBabyRestAsync, "async_start", new_callable=AsyncMock),
        ):
            result = await async_setup_entry(hass, mock_entry)
            await hass.async_block_till_done()

        assert result is True
        coordinator = mock_entry.runtime_data
        # The state is still worth having, it is just not current.
        assert coordinator.hatch_rest_device.has_state is True
        assert HatchBabyRestEntity(coordinator).available is False

    @pytest.mark.asyncio
    async def test_setup_entry_unseen_device_loads_unavailable(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ):
        """Test a device that has not been heard from still sets up.

        An unplugged Hatch is indistinguishable from one that has simply not
        advertised yet. Holding the config entry in retry would delay startup
        and leave no entities at all, so setup proceeds and the entities
        report unavailable instead.
        """
        with (
            patch(
                "custom_components.hatch_rest.bluetooth.async_ble_device_from_address",
                return_value=None,
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
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ) as mock_forward,
            patch.object(
                PyHatchBabyRestAsync, "refresh_data", new_callable=AsyncMock
            ) as mock_refresh,
            patch.object(PyHatchBabyRestAsync, "async_start", new_callable=AsyncMock),
        ):
            result = await async_setup_entry(hass, mock_entry)
            await hass.async_block_till_done()

        assert result is True
        mock_forward.assert_called_once()
        # Setup must not wait on a connection to a device that may be off.
        mock_refresh.assert_not_called()

        coordinator = mock_entry.runtime_data
        # The entities exist and have data to read, but nothing is known.
        assert coordinator.data["power"] is None
        assert coordinator.hatch_rest_device.has_state is False

        entity = HatchBabyRestEntity(coordinator)
        assert entity.available is False

    @pytest.mark.asyncio
    async def test_setup_entry_unseen_device_keeps_its_name(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ):
        """Test the placeholder device is named after the config entry.

        Otherwise a Hatch that was unplugged across a restart would come back
        nameless in the device registry.
        """
        with (
            patch(
                "custom_components.hatch_rest.bluetooth.async_ble_device_from_address",
                return_value=None,
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
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
            patch.object(PyHatchBabyRestAsync, "async_start", new_callable=AsyncMock),
        ):
            await async_setup_entry(hass, mock_entry)
            await hass.async_block_till_done()

        coordinator = mock_entry.runtime_data
        assert coordinator.hatch_rest_device.address == "AA:BB:CC:DD:EE:FF"
        assert coordinator.hatch_rest_device.name == mock_entry.title

    @pytest.mark.asyncio
    async def test_advertisement_replaces_placeholder_device(
        self,
        hass: HomeAssistant,
        mock_entry: MockConfigEntry,
        mock_ble_device: BLEDevice,
    ):
        """Test the first advertisement supplies a connectable BLEDevice.

        The placeholder setup invents cannot be connected through, so it has
        to be replaced once the device turns up.
        """
        with (
            patch(
                "custom_components.hatch_rest.bluetooth.async_ble_device_from_address",
                return_value=None,
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
                "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
                new_callable=AsyncMock,
            ),
            patch.object(PyHatchBabyRestAsync, "async_start", new_callable=AsyncMock),
        ):
            await async_setup_entry(hass, mock_entry)
            await hass.async_block_till_done()

        coordinator = mock_entry.runtime_data
        placeholder = coordinator.hatch_rest_device.device

        service_info = MagicMock()
        service_info.device = mock_ble_device
        service_info.manufacturer_data = {MANUFACTURER_ID: ADVERTISEMENT}
        service_info.time = monotonic()
        coordinator.async_handle_advertisement(service_info, None)

        assert coordinator.hatch_rest_device.device is mock_ble_device
        assert coordinator.hatch_rest_device.device is not placeholder
        # And with state in hand the entities can report themselves usable.
        assert coordinator.hatch_rest_device.has_state is True
        assert HatchBabyRestEntity(coordinator).available is True


class TestRemoveTimerNumber:
    """Tests for the one-off removal of the sleep timer number."""

    def test_removes_the_number_and_nothing_else(self, hass: HomeAssistant):
        """Test the number goes, and the select and sensor replacing it stay."""
        registry = er.async_get(hass)
        number = registry.async_get_or_create("number", DOMAIN, "aabbccddeeff_timer")
        select = registry.async_get_or_create("select", DOMAIN, "aabbccddeeff_timer")
        sensor = registry.async_get_or_create(
            "sensor", DOMAIN, "aabbccddeeff_timer_remaining"
        )

        _remove_timer_number(hass, "aabbccddeeff")

        assert registry.async_get(number.entity_id) is None
        assert registry.async_get(select.entity_id) is not None
        assert registry.async_get(sensor.entity_id) is not None

    def test_does_nothing_once_it_is_gone(self, hass: HomeAssistant):
        """Test a registry with no number is left alone."""
        _remove_timer_number(hass, "aabbccddeeff")


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
