"""Hatch Rest program and timer sensors."""

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import PROGRAM_SLOTS
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the program sensors and the sleep timer."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        [
            *(
                HatchBabyRestProgramSensor(coordinator, slot)
                for slot in range(1, PROGRAM_SLOTS + 1)
            ),
            HatchBabyRestTimerSensor(coordinator),
        ],
        update_before_add=False,
    )


class HatchBabyRestTimerSensor(HatchBabyRestEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """How much of the device's sleep timer is left.

    The device is asked once per connection and the answer counted down from
    there, so this moves without anything being sent.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the timer sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_timer_remaining"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Timer Remaining"
        return None

    @property
    def native_value(self) -> int | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the minutes left, or nothing when no timer is running."""
        return self._hatch_rest_device.timer_remaining


class HatchBabyRestProgramSensor(HatchBabyRestEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """What one of the device's stored programs is set to.

    Read only. The commands that write a program's time, sound, colour or
    days are not documented anywhere we can check, so this reports what the
    device holds and changes nothing.
    """

    # Diagnostic rather than config: a sensor cannot configure anything, and
    # Home Assistant refuses to add one that claims it can.
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator, slot: int) -> None:
        """Initialize the sensor for one slot."""
        super().__init__(coordinator)
        self._slot = slot
        self._attr_unique_id = f"{coordinator.unique_id}_program_{slot}"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Program {self._slot}"
        return None

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
    def native_value(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name the program was given on the device.

        The name says what a program is for in a way its time does not, and
        the entity is already called "Program 3", which says neither. Six of
        forty slots here have no name; those fall back to the time rather
        than to a placeholder, since it is at least real.

        None until the slot has been read, which is not the same as a slot
        with nothing in it.
        """
        program = self._program
        if program is None:
            return None
        return program.get("name") or program["time"]

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
            "enabled": program["enabled"],
            "toddler_lock": program["toddler_lock"],
            # Reported because which bit means enabled is still unsettled,
            # and because a disabled slot is what will settle it.
            "flags": program["flags"],
            "start_timestamp": program["start_timestamp"],
        }
