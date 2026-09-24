"""Hatch Rest integration."""

import logging

from homeassistant import config_entries, core
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothScanningMode,
)
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.exceptions import ConfigEntryNotReady

from .api import PyHatchBabyRestAsync
from .const import (
    ACTIVE_SCAN_DURATION_SECONDS,
    ACTIVE_SCAN_INTERVAL_SECONDS,
    MANUFACTURER_ID,
)
from .coordinator import HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT, Platform.MEDIA_PLAYER, Platform.SWITCH]


# async_setup_entry handles the setup of individual configuration
# entries created by users via the UI (i.e., Config Entry)
async def async_setup_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up the Hatch Rest component."""

    address = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper())
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find Hatch Rest device with address {address}"
        )
    hatch_rest_device = PyHatchBabyRestAsync(ble_device)
    coordinator = HatchBabyRestUpdateCoordinator(
        hass,
        entry.unique_id,
        hatch_rest_device,
        config_entry=entry,
    )
    entry.runtime_data = coordinator

    # Seed from the most recent advertisement if there is one, so startup does
    # not need a connection. This runs before the callback is registered,
    # because registering replays the cached advertisement immediately and
    # would leave nothing for the seed to apply.
    service_info = bluetooth.async_last_service_info(
        hass, address.upper(), connectable=True
    )
    if service_info is not None:
        hatch_rest_device.update_from_advertisement(
            service_info.manufacturer_data.get(MANUFACTURER_ID)
        )

    # Keep state up to date from advertisements, which need no connection.
    # The state lives in the manufacturer data, which is too big to share a
    # legacy advertising PDU with the name and service data the device also
    # sends, so it arrives in the scan response and needs active scanning.
    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            coordinator.async_handle_advertisement,
            BluetoothCallbackMatcher(address=address.upper(), connectable=True),
            BluetoothScanningMode.ACTIVE,
            # An AUTO mode scanner only turns active for a registered address
            # on a schedule, which defaults to 10s every 5 minutes. State only
            # reaches us in the scan response, so ask for the tightest cadence
            # allowed. A scanner pinned to active or passive mode ignores this.
            scan_interval=ACTIVE_SCAN_INTERVAL_SECONDS,
            scan_duration=ACTIVE_SCAN_DURATION_SECONDS,
        )
    )
    entry.async_on_unload(hatch_rest_device.async_stop)

    if hatch_rest_device.has_state:
        _LOGGER.debug("Seeded initial state from advertisement for %s", address)
        coordinator.async_set_updated_data(coordinator.get_current_data())
    else:
        # No usable advertisement, so read over GATT instead. This raises
        # ConfigEntryNotReady on failure, so setup is retried later.
        await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload Hatch Rest config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def options_update_listener(
    hass: core.HomeAssistant, config_entry: config_entries.ConfigEntry
):
    """Handle options update."""
    await hass.config_entries.async_reload(config_entry.entry_id)
