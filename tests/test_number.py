"""Tests for the Hatch Rest sleep timer control."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberDeviceClass
from homeassistant.const import UnitOfTime

from custom_components.hatch_rest.const import TIMER_MAX_MINUTES
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.number import (
    HatchBabyRestTimerNumber,
    async_setup_entry,
)


class TestHatchBabyRestTimerNumber:
    """Tests for HatchBabyRestTimerNumber."""

    @pytest.fixture
    def timer(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestTimerNumber:
        """Create the control over a device that accepts a timer."""
        mock_coordinator.hatch_rest_device.async_set_timer = AsyncMock()
        return HatchBabyRestTimerNumber(mock_coordinator)

    @pytest.mark.asyncio
    async def test_setup_adds_one_control(
        self, hass, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the platform adds the timer."""
        config_entry = MagicMock()
        config_entry.runtime_data = mock_coordinator
        added = []

        await async_setup_entry(
            hass, config_entry, lambda entities, **_: added.extend(entities)
        )

        assert len(added) == 1
        assert isinstance(added[0], HatchBabyRestTimerNumber)

    def test_reads_as_what_the_timer_is_set_to(self, timer: HatchBabyRestTimerNumber):
        """Test the control shows the duration, not the time remaining.

        A control that counted itself down would be a strange thing to drag;
        what is left of the timer is its own sensor.
        """
        timer._hatch_rest_device.timer_total = 30
        timer._hatch_rest_device.timer_remaining = 12

        assert timer.native_value == 30

    def test_no_timer_reads_as_zero(self, timer: HatchBabyRestTimerNumber):
        """Test a device with no timer shows zero rather than nothing.

        Zero is what cancels one, so it is a real position on this control
        rather than an absence.
        """
        timer._hatch_rest_device.timer_total = None

        assert timer.native_value == 0

    def test_range_and_units(self, timer: HatchBabyRestTimerNumber):
        """Test the control is in minutes and stops where the device does."""
        assert timer.native_unit_of_measurement == UnitOfTime.MINUTES
        assert timer.device_class is NumberDeviceClass.DURATION
        assert timer.native_min_value == 0
        assert timer.native_max_value == TIMER_MAX_MINUTES

    @pytest.mark.asyncio
    async def test_setting_passes_whole_minutes(self, timer: HatchBabyRestTimerNumber):
        """Test the control's float is handed over as minutes."""
        await timer.async_set_native_value(15.0)

        timer._hatch_rest_device.async_set_timer.assert_awaited_once_with(15)

    @pytest.mark.asyncio
    async def test_zero_cancels(self, timer: HatchBabyRestTimerNumber):
        """Test dragging to zero is passed through rather than ignored."""
        await timer.async_set_native_value(0)

        timer._hatch_rest_device.async_set_timer.assert_awaited_once_with(0)

    def test_unique_id_does_not_collide_with_the_remaining_sensor(
        self, timer: HatchBabyRestTimerNumber, mock_coordinator
    ):
        """Test the control and the sensor are distinguishable."""
        from custom_components.hatch_rest.sensor import HatchBabyRestTimerSensor

        assert timer.unique_id != HatchBabyRestTimerSensor(mock_coordinator).unique_id
