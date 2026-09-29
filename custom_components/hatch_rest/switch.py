"""Hatch Rest switch."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import FAVORITE_SLOTS, PROGRAM_SLOTS
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest switch."""
    coordinator = config_entry.runtime_data
    # The coordinator is already seeded from an advertisement, so updating
    # before add would only cost a connection: CoordinatorEntity.async_update
    # calls async_request_refresh, which reads state over GATT.
    async_add_entities(
        [
            HatchBabyRestSwitch(coordinator),
            *(
                HatchBabyRestFavoriteEnabledSwitch(coordinator, slot)
                for slot in range(1, FAVORITE_SLOTS + 1)
            ),
            *(
                HatchBabyRestProgramSwitch(coordinator, slot)
                for slot in range(1, PROGRAM_SLOTS + 1)
            ),
        ],
        update_before_add=False,
    )


class HatchBabyRestSwitch(HatchBabyRestEntity, SwitchEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Hatch Rest switch entity."""

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether the switch is on or not."""
        _LOGGER.debug("switch is_on = %s", self.coordinator.data.get("power"))
        return self.coordinator.data.get("power")

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Switch"
        return None

    async def async_turn_on(self, **_):
        """Turn on the Hatch Rest device."""
        if not self.is_on:
            _LOGGER.debug("switch setting on")
            await self._hatch_rest_device.turn_power_on()

    async def async_turn_off(self, **_):
        """Turn off the Hatch Rest device."""
        if self.is_on:
            _LOGGER.debug("switch setting off")
            await self._hatch_rest_device.turn_power_off()


class HatchBabyRestFavoriteEnabledSwitch(HatchBabyRestEntity, SwitchEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Whether one favorite is offered when cycling them on the device."""

    # These configure the device rather than operate it.
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator, slot: int) -> None:
        """Initialize the switch for one slot."""
        super().__init__(coordinator)
        self._slot = slot
        # HatchBabyRestEntity hands every entity the bare coordinator id, and
        # the power switch above already has it. Suffixing is what keeps
        # these six from colliding with it and with each other -- the power
        # switch keeps the unsuffixed id so its history survives.
        self._attr_unique_id = f"{coordinator.unique_id}_favorite_{slot}_enabled"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return (
                f"{self._hatch_rest_device.name.title()} Favorite {self._slot} Enabled"
            )
        return None

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether this favorite is offered on the device.

        None until the slot has been read, which is not the same as off: a
        favorite whose state is unknown should not look disabled.
        """
        favorite = self._hatch_rest_device.favorites.get(self._slot)
        if favorite is None:
            return None
        return favorite.get("enabled")

    async def async_turn_on(self, **_):
        """Start offering this favorite on the device."""
        _LOGGER.debug("switch enabling favorite %d", self._slot)
        await self._hatch_rest_device.async_set_favorite(self._slot, enabled=True)

    async def async_turn_off(self, **_):
        """Stop offering this favorite on the device."""
        _LOGGER.debug("switch disabling favorite %d", self._slot)
        await self._hatch_rest_device.async_set_favorite(self._slot, enabled=False)


class HatchBabyRestProgramSwitch(HatchBabyRestEntity, SwitchEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Whether one of the device's stored programs runs.

    Only the enabled flag can be changed. The commands that write a program's
    time, sound, colour or days are not documented anywhere we can check, so
    the rest of what the slot holds is reported and left alone.
    """

    # These configure the device rather than operate it.
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator, slot: int) -> None:
        """Initialize the switch for one slot."""
        super().__init__(coordinator)
        self._slot = slot
        # The same id the program sensors had, which is free to reuse: unique
        # ids only have to be unique within a platform.
        self._attr_unique_id = f"{coordinator.unique_id}_program_{slot}"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity.

        With the name the program was given on the device when there is one,
        since that says what it is for where "Program 3" does not. The entity
        id is fixed on first registration, so it does not move with this.
        """
        if not self._hatch_rest_device.name:
            return None
        name = f"{self._hatch_rest_device.name.title()} Program {self._slot}"
        program = self._hatch_rest_device.programs.get(self._slot)
        if program is not None and program.get("name"):
            name = f"{name} ({program['name']})"
        return name

    @property
    def _program(self) -> dict | None:
        """Return this slot's contents, if its block has been read.

        A slot can hold nothing but a name. Names arrive as their own
        notification, so one can land before the block it belongs to, or
        without it at all if the block never parses.
        """
        program = self._hatch_rest_device.programs.get(self._slot)
        if program is None or "time" not in program:
            return None
        return program

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether there is a program here to turn on or off.

        An empty slot has nothing to run, and enabling one does nothing.
        """
        program = self._program
        return super().available and not (program and program.get("empty"))

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether this program is enabled.

        None until the slot has been read, which is not the same as off.
        """
        program = self._program
        if program is None:
            return None
        return program.get("enabled")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the rest of what the program holds."""
        program = self._program
        if program is None:
            return {}

        return {
            "name": program.get("name"),
            "time": program["time"],
            "days": program["days"],
            "duration_seconds": program["duration_seconds"],
            # Kept because the layout here was worked out from real slots
            # against two published sources that had it wrong, and the bytes
            # nothing has accounted for are still in it.
            "raw": program["raw"],
            "color": program["color"],
            "brightness": program["brightness"],
            "sound": (
                program["sound"].name
                if program["sound"] is not None
                else program["sound_id"]
            ),
            "volume": program["volume"],
            "toddler_lock": program["toddler_lock"],
            # The byte ahead of the name. 0x02 is enabled; the rest of it is
            # not understood.
            "status": program.get("status"),
            # Nothing is known to read this byte, which every populated slot
            # seen holds as 0xdf whether it is enabled or not.
            "flags": program["flags"],
            "start_timestamp": program["start_timestamp"],
        }

    async def async_turn_on(self, **_):
        """Enable this program on the device."""
        _LOGGER.debug("switch enabling program %d", self._slot)
        await self._hatch_rest_device.async_set_program_enabled(self._slot, True)

    async def async_turn_off(self, **_):
        """Disable this program on the device."""
        _LOGGER.debug("switch disabling program %d", self._slot)
        await self._hatch_rest_device.async_set_program_enabled(self._slot, False)
