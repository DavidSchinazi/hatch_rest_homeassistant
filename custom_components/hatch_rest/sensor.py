"""Hatch Rest timer sensor."""

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, PROGRAM_SLOTS
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sleep timer."""
    coordinator = config_entry.runtime_data
    _remove_program_sensors(hass, coordinator.unique_id)
    async_add_entities([HatchBabyRestTimerSensor(coordinator)], update_before_add=False)


def _remove_program_sensors(hass: HomeAssistant, unique_id: str | None) -> None:
    """Drop the program sensors earlier versions registered.

    Programs are switches now. Left alone, the old sensors would sit in the
    registry as entities no longer provided by the integration.
    """
    registry = er.async_get(hass)
    for slot in range(1, PROGRAM_SLOTS + 1):
        if entity_id := registry.async_get_entity_id(
            "sensor", DOMAIN, f"{unique_id}_program_{slot}"
        ):
            _LOGGER.debug("Removing program sensor %s", entity_id)
            registry.async_remove(entity_id)


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
