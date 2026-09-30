"""Hatch Rest timer sensor."""

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import TIMER_OFF
from .coordinator import HatchBabyRestTimerEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sleep timer."""
    coordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestTimerSensor(coordinator)], update_before_add=False)


def format_timer(seconds: int | None) -> str:
    """Return a time left as H:MM:SS, or Off when there is none."""
    if not seconds:
        return TIMER_OFF
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


class HatchBabyRestTimerSensor(HatchBabyRestTimerEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """How much of the device's sleep timer is left, as H:MM:SS or Off.

    Text rather than a duration, which is the one thing Home Assistant will
    not show as hours, minutes and seconds together, nor as Off. That makes it
    one state change a second while a timer runs, and nothing to graph.
    Counts down on its own; see HatchBabyRestTimerEntity.
    """

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the timer sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_timer_remaining"

    def _timer_value(self) -> str:
        """Return the time left, as this sensor shows it."""
        return self.native_value

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Timer Remaining"
        return None

    @property
    def native_value(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the time left, or Off when no timer is running."""
        return format_timer(self._hatch_rest_device.timer_remaining)
