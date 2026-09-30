"""Hatch Rest sleep timer control."""

import logging

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TIMER_MAX_SECONDS
from .coordinator import HatchBabyRestTimerEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sleep timer control."""
    coordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestTimerNumber(coordinator)], update_before_add=False)


class HatchBabyRestTimerNumber(HatchBabyRestTimerEntity, NumberEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """The sleep timer: set it to start one, or to zero to cancel.

    Shows what is left rather than what it was set to, because what is left
    is all the device reports -- GI, which the notes call the total, does not
    track the timer. A box rather than a slider, since the value moves on its
    own -- it counts down like the sensor, see HatchBabyRestTimerEntity.
    """

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_max_value = TIMER_MAX_SECONDS // 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the timer control."""
        super().__init__(coordinator)
        # The id the control had before it was taken out, so it comes back
        # as the same entity.
        self._attr_unique_id = f"{coordinator.unique_id}_timer"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Sleep Timer"
        return None

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the minutes left, zero when no timer is running."""
        return self._hatch_rest_device.timer_remaining or 0

    def _timer_value(self) -> float | None:
        """Return the minutes left, as this control shows them."""
        return self.native_value

    async def async_set_native_value(self, value: float) -> None:
        """Start a sleep timer of this many minutes, or cancel it with zero."""
        _LOGGER.debug("number setting sleep timer to %d minutes", int(value))
        await self._hatch_rest_device.async_set_timer(int(value) * 60)
