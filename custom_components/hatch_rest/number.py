"""Hatch Rest sleep timer."""

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TIMER_MAX_MINUTES
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sleep timer."""
    coordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestTimerNumber(coordinator)], update_before_add=False)


class HatchBabyRestTimerNumber(HatchBabyRestEntity, NumberEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """How long the device's sleep timer is set to run for.

    This is what the timer was set to, not what is left of it -- a control
    that counted itself down would be a strange thing to drag. The time
    remaining is its own sensor.
    """

    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_max_value = TIMER_MAX_MINUTES
    _attr_native_step = 1

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the timer control."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_timer"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Sleep Timer"
        return None

    @property
    def native_value(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the timer the device is set to, zero when it has none."""
        total = self._hatch_rest_device.timer_total
        return 0 if total is None else total

    async def async_set_native_value(self, value: float) -> None:
        """Set the sleep timer, or cancel it with zero."""
        _LOGGER.debug("number setting sleep timer to %d minutes", int(value))
        await self._hatch_rest_device.async_set_timer(int(value))
