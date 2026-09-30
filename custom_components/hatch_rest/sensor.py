"""Hatch Rest timer sensor."""

from datetime import datetime, timedelta
import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, PROGRAM_SLOTS
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# How often the timer sensor checks whether its minute has moved on. The
# countdown is local, so this sends nothing; it only has to be well under a
# minute for the value to change close to when it should.
TIMER_TICK = timedelta(seconds=15)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sleep timer."""
    coordinator = config_entry.runtime_data
    _remove_stale_entities(hass, coordinator.unique_id)
    async_add_entities([HatchBabyRestTimerSensor(coordinator)], update_before_add=False)


def _remove_stale_entities(hass: HomeAssistant, unique_id: str | None) -> None:
    """Drop entities earlier versions registered and this one does not provide.

    Left alone they sit in the registry as unavailable, and are easy to take
    for the real thing:

    - Program sensors: programs are switches now.
    - The sleep timer number: setting the timer has never been shown to work,
      so only this sensor reading it remains.
    """
    stale = [
        *(
            ("sensor", f"{unique_id}_program_{slot}")
            for slot in range(1, PROGRAM_SLOTS + 1)
        ),
        ("number", f"{unique_id}_timer"),
    ]
    registry = er.async_get(hass)
    for domain, stale_id in stale:
        if entity_id := registry.async_get_entity_id(domain, DOMAIN, stale_id):
            _LOGGER.debug("Removing stale entity %s", entity_id)
            registry.async_remove(entity_id)


class HatchBabyRestTimerSensor(HatchBabyRestEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """How much of the device's sleep timer is left.

    The device is asked once per connection and the answer counted down from
    there, so this moves without anything being sent. Coordinator updates
    alone come every 90 seconds, so the sensor also ticks on its own and
    writes its state whenever the minute it shows has moved on.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the timer sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_id}_timer_remaining"
        self._last_written: int | None = None

    async def async_added_to_hass(self) -> None:
        """Start ticking."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_tick, TIMER_TICK)
        )

    @callback
    def _async_tick(self, _now: datetime) -> None:
        """Write the state if the minutes left have changed since last time."""
        if self.native_value != self._last_written:
            self.async_write_ha_state()

    @callback
    def async_write_ha_state(self) -> None:
        """Write the state, remembering the value so ticks can skip repeats."""
        self._last_written = self.native_value
        super().async_write_ha_state()

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
