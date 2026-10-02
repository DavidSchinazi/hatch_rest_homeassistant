"""Hatch Rest config flow."""

import dataclasses
from datetime import time, timedelta
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS, CONF_SENSOR_TYPE
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    ColorRGBSelector,
    DurationSelector,
    DurationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)

from .api import HatchRestConnectionError, PyHatchBabyRestAsync
from .const import (
    DOMAIN,
    FAVORITE_SLOTS,
    MANUFACTURER_ID,
    PROGRAM_DAYS,
    PROGRAM_DURATION_MAX_SECONDS,
    PROGRAM_NAME_MAX_LENGTH,
    PROGRAM_SLOTS,
    PyHatchBabyRestSound,
)

_LOGGER = logging.getLogger(__name__)


# Much of this is sourced from the Switchbot official component
def format_unique_id(address: str) -> str:
    """Format the unique ID for a Hatch Rest."""
    return address.replace(":", "").lower()


def short_address(address: str) -> str:
    """Convert a Bluetooth address to a short address."""
    results = address.replace("-", ":").split(":")
    return f"{results[-2].upper()}{results[-1].upper()}"[-4:]


@dataclasses.dataclass
class DiscoveredDevice:
    """Discovered device information."""

    name: str
    discovery_info: BluetoothServiceInfoBleak
    hatch_rest_device: PyHatchBabyRestAsync


class HatchBabyRestConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Hatch Rest config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the favorite and program editor, behind Configure."""
        return HatchBabyRestOptionsFlow()

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_device: DiscoveredDevice | None = None
        self._discovered_devices: dict[str, DiscoveredDevice] = {}
        self._device_name: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the Bluetooth discovery step."""
        _LOGGER.debug("Discovered Hatch Rest %s", discovery_info.as_dict())
        await self.async_set_unique_id(format_unique_id(discovery_info.address))
        self._abort_if_unique_id_configured()

        try:
            ble_device = async_ble_device_from_address(
                self.hass, discovery_info.address, connectable=True
            )
            if not ble_device:
                raise ValueError("BLEDevice does not exist")  # noqa: TRY301
            # The name comes from the advertisement, so discovery needs no
            # connection. Opening one here would compete for the proxy's
            # connection slots with the devices already set up.
            hatch_rest_device = PyHatchBabyRestAsync(ble_device)
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Unexpected error during async_step_bluetooth: %r", e)
            return self.async_abort(reason="unknown")

        self._device_name = hatch_rest_device.name
        self._discovered_device = DiscoveredDevice(
            name=hatch_rest_device.name or "",
            discovery_info=discovery_info,
            hatch_rest_device=hatch_rest_device,
        )

        self.context["title_placeholders"] = {
            "name": self._device_name or "",
            "address": short_address(discovery_info.address),
        }

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Blueooth confirmation step."""
        if user_input is not None:
            return await self._async_create_entry_from_discovery(user_input)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self.context["title_placeholders"]["name"]
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """User input step."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(
                short_address(address), raise_on_progress=False
            )
            self._abort_if_unique_id_configured()
            discovery = self._discovered_devices[address]

            self.context["title_placeholders"] = {"name": discovery.name}

            self._discovered_device = discovery

            return await self._async_create_entry_from_discovery(user_input)

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue

            if MANUFACTURER_ID not in discovery_info.manufacturer_data:
                continue

            try:
                ble_device = async_ble_device_from_address(
                    self.hass, discovery_info.address
                )
                if not ble_device:
                    raise ValueError("BLEDevice does not exist")  # noqa: TRY301
                hatch_rest_device = PyHatchBabyRestAsync(ble_device)
            except Exception as e:  # noqa: BLE001
                _LOGGER.debug("Unexpected error during async_step_user: %r", e)
                return self.async_abort(reason="unknown")
            name = hatch_rest_device.name
            self._discovered_devices[address] = DiscoveredDevice(
                name or "", discovery_info, hatch_rest_device
            )

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        titles = {
            address: discovery.name
            for (address, discovery) in self._discovered_devices.items()
        }
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(titles)}),
        )

    async def _async_create_entry_from_discovery(
        self, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        if self._discovered_device:
            address = self._discovered_device.discovery_info.address
        return self.async_create_entry(
            title=self._device_name or "",
            data={
                **user_input,
                CONF_ADDRESS: address,
                CONF_SENSOR_TYPE: "switch",  # is this even required? I have other platforms supported
            },
        )


# What a slot that has never held a program starts as in the editor.
NEW_PROGRAM = {
    "enabled": False,
    "start": "07:00:00",
    "duration": {"hours": 1, "minutes": 0, "seconds": 0},
    "days": list(PROGRAM_DAYS),
    "color": [255, 255, 255],
    "brightness": 128,
    "sound": PyHatchBabyRestSound.none.name,
    "volume": 64,
    "toddler_lock": False,
}

PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required("enabled"): BooleanSelector(),
        vol.Required("name"): TextSelector(),
        vol.Required("start"): TimeSelector(),
        vol.Required("duration"): DurationSelector(
            DurationSelectorConfig(enable_day=False)
        ),
        vol.Required("days"): SelectSelector(
            SelectSelectorConfig(
                options=list(PROGRAM_DAYS),
                multiple=True,
                mode=SelectSelectorMode.LIST,
            )
        ),
        vol.Required("color"): ColorRGBSelector(),
        vol.Required("brightness"): NumberSelector(
            NumberSelectorConfig(min=0, max=255, mode=NumberSelectorMode.SLIDER)
        ),
        vol.Required("sound"): SelectSelector(
            SelectSelectorConfig(
                options=[sound.name for sound in PyHatchBabyRestSound],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required("volume"): NumberSelector(
            NumberSelectorConfig(min=0, max=255, mode=NumberSelectorMode.SLIDER)
        ),
        vol.Required("toddler_lock"): BooleanSelector(),
    }
)


def _program_form_values(slot: int, program: dict) -> dict[str, Any]:
    """Return a stored program as the editor's fields show it."""
    name = program.get("name") or f"Program {slot}"
    if program["empty"]:
        return {**NEW_PROGRAM, "name": name}

    start = program["start_timestamp"] % 86400
    duration = program["duration_seconds"]
    sound = program["sound"]
    return {
        "enabled": program["enabled"],
        "name": name,
        "start": f"{start // 3600:02d}:{start // 60 % 60:02d}:{start % 60:02d}",
        "duration": {
            "hours": duration // 3600,
            "minutes": duration // 60 % 60,
            "seconds": duration % 60,
        },
        "days": list(program["days"]),
        "color": list(program["color"]),
        "brightness": program["brightness"],
        # A sound with no name here cannot be shown in the list, so it reads
        # as none; it is only changed if the form is saved.
        "sound": sound.name if sound is not None else PyHatchBabyRestSound.none.name,
        "volume": program["volume"],
        "toddler_lock": program["toddler_lock"],
    }


def _program_changes(form: dict[str, Any]) -> dict[str, Any]:
    """Return the editor's fields as async_set_program takes them.

    Raises ValueError, with the field at fault first, for what the device
    cannot hold.
    """
    duration = int(timedelta(**form["duration"]).total_seconds())
    if duration > PROGRAM_DURATION_MAX_SECONDS:
        raise ValueError("duration", "duration_too_long")
    name = form["name"].strip()
    if not (0 < len(name) <= PROGRAM_NAME_MAX_LENGTH and name.isascii()):
        raise ValueError("name", "name_invalid")

    return {
        "enabled": form["enabled"],
        "name": name,
        "start": time.fromisoformat(form["start"]),
        "duration_seconds": duration,
        "days_mask": sum(1 << PROGRAM_DAYS.index(day) for day in form["days"]),
        "color": tuple(int(part) for part in form["color"]),
        "brightness": int(form["brightness"]),
        "sound": PyHatchBabyRestSound[form["sound"]],
        "volume": int(form["volume"]),
        "toddler_lock": form["toddler_lock"],
    }


FAVORITE_SCHEMA = vol.Schema(
    {
        vol.Required("enabled"): BooleanSelector(),
        vol.Required("color"): ColorRGBSelector(),
        vol.Required("brightness"): NumberSelector(
            NumberSelectorConfig(min=0, max=255, mode=NumberSelectorMode.SLIDER)
        ),
        vol.Required("sound"): SelectSelector(
            SelectSelectorConfig(
                options=[sound.name for sound in PyHatchBabyRestSound],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Required("volume"): NumberSelector(
            NumberSelectorConfig(min=0, max=255, mode=NumberSelectorMode.SLIDER)
        ),
    }
)


def _favorite_form_values(favorite: dict) -> dict[str, Any]:
    """Return a stored favorite as the editor's fields show it."""
    sound = favorite["sound"]
    return {
        "enabled": favorite["enabled"],
        "color": list(favorite["color"]),
        "brightness": favorite["brightness"],
        # As for a program: a sound with no name here reads as none, and is
        # only changed if the form is saved.
        "sound": sound.name if sound is not None else PyHatchBabyRestSound.none.name,
        "volume": favorite["volume"],
    }


def _favorite_changes(form: dict[str, Any]) -> dict[str, Any]:
    """Return the editor's fields as async_set_favorite takes them."""
    return {
        "enabled": form["enabled"],
        "color": tuple(int(part) for part in form["color"]),
        "brightness": int(form["brightness"]),
        "sound": PyHatchBabyRestSound[form["sound"]],
        "volume": int(form["volume"]),
    }


class HatchBabyRestOptionsFlow(OptionsFlow):
    """Edit the favorites and programs stored on a Hatch Rest.

    Choose which kind, pick a slot, then a form filled in with what it holds
    now. Saving writes the whole slot to the device and reads it back;
    nothing is stored in the entry's options.
    """

    def __init__(self) -> None:
        """Initialize the editor."""
        self._slot: int | None = None

    @property
    def _device(self) -> PyHatchBabyRestAsync:
        return self.config_entry.runtime_data.hatch_rest_device

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose whether to edit a favorite or a program."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="init", menu_options=["favorites", "programs"]
        )

    async def async_step_favorites(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which favorite to edit."""
        favorites = self._device.favorites
        if not any("color" in favorite for favorite in favorites.values()):
            return self.async_abort(reason="favorites_unread")

        if user_input is not None:
            self._slot = int(user_input["favorite"])
            return await self.async_step_favorite()

        options = []
        for slot in range(1, FAVORITE_SLOTS + 1):
            favorite = favorites.get(slot, {})
            if "color" not in favorite:
                continue
            sound = favorite["sound"]
            sound_label = (
                sound.name if sound is not None else f"sound {favorite['sound_id']}"
            )
            red, green, blue = favorite["color"]
            state = "on" if favorite["enabled"] else "off"
            label = (
                f"{slot}: {favorite.get('name') or f'Favorite {slot}'} "
                f"(#{red:02X}{green:02X}{blue:02X}, {sound_label}, {state})"
            )
            options.append(SelectOptionDict(value=str(slot), label=label))

        return self.async_show_form(
            step_id="favorites",
            data_schema=vol.Schema(
                {
                    vol.Required("favorite"): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_favorite(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit one favorite, starting from what the device holds."""
        assert self._slot is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await self._device.async_set_favorite(
                    self._slot, **_favorite_changes(user_input)
                )
            except (HatchRestConnectionError, ValueError) as err:
                _LOGGER.warning("Writing favorite %d failed: %s", self._slot, err)
                errors["base"] = "favorite_write_failed"
            else:
                return self.async_create_entry(data=dict(self.config_entry.options))

        values = user_input or _favorite_form_values(self._device.favorites[self._slot])
        return self.async_show_form(
            step_id="favorite",
            data_schema=self.add_suggested_values_to_schema(FAVORITE_SCHEMA, values),
            errors=errors,
            description_placeholders={"slot": str(self._slot)},
        )

    async def async_step_programs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which program to edit."""
        programs = self._device.programs
        if not any("empty" in programs.get(slot, {}) for slot in programs):
            return self.async_abort(reason="programs_unread")

        if user_input is not None:
            self._slot = int(user_input["program"])
            return await self.async_step_program()

        options = []
        for slot in range(1, PROGRAM_SLOTS + 1):
            program = programs.get(slot, {})
            if "empty" not in program:
                continue
            if program["empty"]:
                label = f"{slot}: empty"
            else:
                state = "on" if program.get("enabled") else "off"
                label = f"{slot}: {program.get('name') or 'unnamed'} ({program['time']}, {state})"
            options.append(SelectOptionDict(value=str(slot), label=label))

        return self.async_show_form(
            step_id="programs",
            data_schema=vol.Schema(
                {
                    vol.Required("program"): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_program(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit one program, starting from what the device holds."""
        assert self._slot is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                changes = _program_changes(user_input)
            except ValueError as err:
                field, reason = err.args
                errors[field] = reason
            else:
                try:
                    await self._device.async_set_program(self._slot, **changes)
                except (HatchRestConnectionError, ValueError) as err:
                    _LOGGER.warning("Writing program %d failed: %s", self._slot, err)
                    errors["base"] = "write_failed"
                else:
                    return self.async_create_entry(data=dict(self.config_entry.options))

        values = user_input or _program_form_values(
            self._slot, self._device.programs[self._slot]
        )
        return self.async_show_form(
            step_id="program",
            data_schema=self.add_suggested_values_to_schema(PROGRAM_SCHEMA, values),
            errors=errors,
            description_placeholders={"slot": str(self._slot)},
        )
