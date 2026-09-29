"""Hatch Rest schedule sensors."""

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up a sensor for each of the device's schedules."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        [
            HatchBabyRestScheduleSensor(coordinator, slot)
            for slot in range(1, SCHEDULE_SLOTS + 1)
        ],
        update_before_add=False,
    )


class HatchBabyRestScheduleSensor(HatchBabyRestEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """What one of the device's stored schedules is set to.

    Read only. The commands that write a schedule's time, sound, colour or
    days are not documented anywhere we can check, so this reports what the
    device holds and changes nothing.
    """

    _attr_entity_category = EntityCategory.CONFIG

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
        """Return the time of day this schedule runs at.

        None until the slot has been read, which is not the same as a
        schedule that is not set.
        """
        schedule = self._schedule
        if schedule is None:
            return None
        return f"{schedule['hour']:02d}:{schedule['minute']:02d}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the rest of what the schedule holds."""
        schedule = self._schedule
        if schedule is None:
            return {}

        return {
            "days": schedule["days"],
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
