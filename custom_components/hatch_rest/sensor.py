"""Hatch Rest timer sensor."""

import logging
from datetime import datetime, timedelta
from time import monotonic

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import HatchBabyRestTimerEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# How far a fresh read of the timer can move its end before the sensor shows
# the new one. Each connection reads it again, to the whole second, so the
# same timer would otherwise come back a second either way.
TIMER_END_TOLERANCE_SECONDS = 5


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sleep timer."""
    coordinator = config_entry.runtime_data
    async_add_entities([HatchBabyRestTimerSensor(coordinator)], update_before_add=False)


class HatchBabyRestTimerSensor(HatchBabyRestTimerEntity, SensorEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """When the device's sleep timer runs out, or unknown with none running.

    A timestamp rather than the time left, so it changes when a timer starts,
    stops or is changed, not once a second while it runs. Home Assistant shows
    it counting down ("in 5 hours") on its own.

    Still ticks, so that it clears the moment the timer runs out rather than
    at the next coordinator update; see HatchBabyRestTimerEntity.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the timer sensor."""
        super().__init__(coordinator)
        # Kept from when it showed the time left, so the entity carries on.
        self._attr_unique_id = f"{coordinator.unique_id}_timer_remaining"
        # The end last shown, and the monotonic expiry it was worked out from.
        self._ends_at: datetime | None = None
        self._ends_from: float | None = None

    def _timer_value(self) -> datetime | None:
        """Return the end of the timer, as this sensor shows it."""
        return self.native_value

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Timer Ends"
        return None

    @property
    def native_value(self) -> datetime | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return when the timer runs out, or None when none is running.

        Worked out once per read of the timer, not on every call: turning the
        monotonic expiry into wall-clock time anew each tick would wobble.
        """
        device = self._hatch_rest_device
        expires_at = device.timer_expires_at
        if device.timer_remaining is None or expires_at is None:
            self._ends_at = self._ends_from = None
            return None

        if (
            self._ends_from is None
            or abs(expires_at - self._ends_from) > TIMER_END_TOLERANCE_SECONDS
        ):
            self._ends_from = expires_at
            self._ends_at = (
                dt_util.utcnow() + timedelta(seconds=expires_at - monotonic())
            ).replace(microsecond=0)
        return self._ends_at
