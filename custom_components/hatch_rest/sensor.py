"""Hatch Rest schedule and timer sensors."""

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SCHEDULE_SLOTS
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the schedule sensors and the sleep timer."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        [
            *(
                HatchBabyRestScheduleSensor(coordinator, slot)
                for slot in range(1, SCHEDULE_SLOTS + 1)
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


class HatchBabyRestScheduleSensor(HatchBabyRestEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """What one of the device's stored schedules is set to.

    Read only. The commands that write a schedule's time, sound, colour or
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
        self._attr_unique_id = f"{coordinator.unique_id}_schedule_{slot}"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Schedule {self._slot}"
        return None

    @property
    def _schedule(self) -> dict | None:
        """Return this slot's contents, if they have been read."""
        return self._hatch_rest_device.schedules.get(self._slot)

    @property
    def native_value(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name the schedule was given on the device.

        The time of day would be the obvious thing to report, but where the
        device keeps it is not yet known -- see the raw attribute. The name
        is real, and enough to tell one slot from another.

        None until the slot has been read, which is not the same as a slot
        with nothing in it.
        """
        schedule = self._schedule
        if schedule is None:
            return None
        return schedule.get("name") or "Unused"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the rest of what the schedule holds."""
        schedule = self._schedule
        if schedule is None:
            return {}

        return {
            "days": schedule["days"],
            # The whole block, because the time of day is in here somewhere
            # and the bytes the protocol notes point at hold something else.
            "raw": schedule["raw"],
            "color": schedule["color"],
            "brightness": schedule["brightness"],
            "sound": (
                schedule["sound"].name
                if schedule["sound"] is not None
                else schedule["sound_id"]
            ),
            "volume": schedule["volume"],
            "enabled": schedule["enabled"],
            # Reported because which bit means enabled is still unsettled,
            # and because a disabled slot is what will settle it.
            "flags": schedule["flags"],
            "modified_timestamp": schedule["modified_timestamp"],
        }
