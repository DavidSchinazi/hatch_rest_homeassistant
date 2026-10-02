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


def _favorite_read(favorite: dict) -> bool:
    """Return whether a favorite's contents have been read from the device."""
    return "color" in favorite


def _favorite_options(favorites: dict[int, dict]) -> list[SelectOptionDict]:
    """Return the favorites read, labelled by what they hold."""
    options = []
    for slot in range(1, FAVORITE_SLOTS + 1):
        favorite = favorites.get(slot, {})
        if not _favorite_read(favorite):
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
    return options


def _program_options(
    programs: dict[int, dict], *, include_empty: bool
) -> list[SelectOptionDict]:
    """Return the programs read, labelled by name and time."""
    options = []
    for slot in range(1, PROGRAM_SLOTS + 1):
        program = programs.get(slot, {})
        if "empty" not in program:
            continue
        if program["empty"]:
            if not include_empty:
                continue
            label = f"{slot}: empty"
        else:
            state = "on" if program.get("enabled") else "off"
            label = f"{slot}: {program.get('name') or 'unnamed'} ({program['time']}, {state})"
        options.append(SelectOptionDict(value=str(slot), label=label))
    return options


def _favorite_copy(favorite: dict) -> dict[str, Any]:
    """Return a favorite read from one device as async_set_favorite takes it."""
    return {
        "enabled": favorite["enabled"],
        "color": favorite["color"],
        "brightness": favorite["brightness"],
        # The raw id, so a sound with no name here is copied as it is.
        "sound": favorite["sound_id"],
        "volume": favorite["volume"],
    }


def _program_copy(slot: int, program: dict) -> dict[str, Any]:
    """Return a program read from one device as async_set_program takes it.

    Every field is given, the name included: one left out would be kept from
    the slot being written over rather than taken from the one copied.
    """
    start = program["start_timestamp"] % 86400
    return {
        "enabled": program["enabled"],
        "name": program.get("name") or f"Program {slot}",
        "start": time(start // 3600, start // 60 % 60, start % 60),
        "duration_seconds": program["duration_seconds"],
        "days_mask": program["days_mask"],
        "color": program["color"],
        "brightness": program["brightness"],
        "sound": program["sound_id"],
        "volume": program["volume"],
        "toddler_lock": program["toddler_lock"],
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
        # The device copied from, by its entry.
        self._source: ConfigEntry | None = None

    @property
    def _device(self) -> PyHatchBabyRestAsync:
        return self.config_entry.runtime_data.hatch_rest_device

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose whether to edit a favorite or a program, or copy them."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="init",
            menu_options=["favorites", "programs", "copy_favorites", "copy_programs"],
        )

    async def async_step_favorites(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which favorite to edit."""
        favorites = self._device.favorites
        if not any(_favorite_read(favorite) for favorite in favorites.values()):
            return self.async_abort(reason="favorites_unread")

        if user_input is not None:
            self._slot = int(user_input["favorite"])
            return await self.async_step_favorite()

        options = _favorite_options(favorites)
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

        options = _program_options(programs, include_empty=True)
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

    def _other_devices(self) -> dict[str, ConfigEntry]:
        """Return the other Hatches set up and connected, by entry id."""
        return {
            entry.entry_id: entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.entry_id != self.config_entry.entry_id
            and entry.state is ConfigEntryState.LOADED
        }

    async def _async_step_source(
        self, step_id: str, next_step, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Choose which other Hatch to copy from."""
        others = self._other_devices()
        if not others:
            return self.async_abort(reason="no_other_devices")

        if user_input is not None:
            self._source = others[user_input["source"]]
            return await next_step()

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required("source"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=entry_id, label=entry.title)
                                for entry_id, entry in others.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def _async_step_copy_slots(
        self,
        step_id: str,
        options: list[SelectOptionDict],
        write,
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Choose which slots to copy, all of them to begin with, and copy them.

        Each goes to the same slot it came from. A write that fails stops the
        copy there, since whatever kept it from the device is likely to keep
        the rest from it too; those already copied are unticked, so trying
        again carries on from the one that failed.
        """
        assert self._source is not None
        errors: dict[str, str] = {}
        placeholders = {"source": self._source.title, "failed": ""}
        selected = [option["value"] for option in options]

        if user_input is not None:
            selected = list(user_input["slots"])
            if not selected:
                errors["base"] = "nothing_selected"
            for slot in list(selected):
                try:
                    await write(int(slot))
                except (HatchRestConnectionError, ValueError) as err:
                    _LOGGER.warning("Copying %s %s failed: %s", step_id, slot, err)
                    errors["base"] = "copy_failed"
                    placeholders["failed"] = slot
                    break
                selected.remove(slot)
            if not errors:
                return self.async_create_entry(data=dict(self.config_entry.options))

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required("slots"): SelectSelector(
                            SelectSelectorConfig(
                                options=options,
                                multiple=True,
                                mode=SelectSelectorMode.LIST,
                            )
                        )
                    }
                ),
                {"slots": selected},
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_copy_favorites(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which other Hatch to copy favorites from."""
        return await self._async_step_source(
            "copy_favorites", self.async_step_copy_favorite_slots, user_input
        )

    async def async_step_copy_favorite_slots(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which favorites to copy, and copy them."""
        assert self._source is not None
        if self._source.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="source_not_loaded")
        favorites = self._source.runtime_data.hatch_rest_device.favorites
        options = _favorite_options(favorites)
        if not options:
            return self.async_abort(
                reason="source_favorites_unread",
                description_placeholders={"source": self._source.title},
            )

        async def write(slot: int) -> None:
            await self._device.async_set_favorite(
                slot, **_favorite_copy(favorites[slot])
            )

        return await self._async_step_copy_slots(
            "copy_favorite_slots", options, write, user_input
        )

    async def async_step_copy_programs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which other Hatch to copy programs from."""
        return await self._async_step_source(
            "copy_programs", self.async_step_copy_program_slots, user_input
        )

    async def async_step_copy_program_slots(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which programs to copy, and copy them.

        Only programs that hold something are offered: there is no known way
        to empty a slot, so an empty one cannot be copied over a full one.
        """
        assert self._source is not None
        if self._source.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="source_not_loaded")
        programs = self._source.runtime_data.hatch_rest_device.programs
        if not any("empty" in program for program in programs.values()):
            return self.async_abort(
                reason="source_programs_unread",
                description_placeholders={"source": self._source.title},
            )
        # Whether a program is enabled is read separately from the rest, and
        # copying one without it would turn it off or on by guesswork.
        options = [
            option
            for option in _program_options(programs, include_empty=False)
            if "enabled" in programs[int(option["value"])]
        ]
        if not options:
            return self.async_abort(
                reason="source_programs_empty",
                description_placeholders={"source": self._source.title},
            )

        async def write(slot: int) -> None:
            await self._device.async_set_program(
                slot, **_program_copy(slot, programs[slot])
            )

        return await self._async_step_copy_slots(
            "copy_program_slots", options, write, user_input
        )
