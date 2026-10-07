"""Tests for Hatch Rest sensors."""

import math
from datetime import timedelta
from time import monotonic
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.util import dt as dt_util

from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.sensor import (
    HatchBabyRestTimerSensor,
    async_setup_entry,
)


class TestSensorSetup:
    """Tests for the sensor platform's setup."""

    @pytest.mark.asyncio
    async def test_setup_adds_only_the_timer(
        self, hass, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test programs no longer come up as sensors."""
        config_entry = MagicMock()
        config_entry.runtime_data = mock_coordinator
        added = []

        await async_setup_entry(
            hass, config_entry, lambda entities, **_: added.extend(entities)
        )

        assert len(added) == 1
        assert isinstance(added[0], HatchBabyRestTimerSensor)


class TestHatchBabyRestTimerSensor:
    """Tests for HatchBabyRestTimerSensor."""

    @staticmethod
    def _run_timer(device, seconds: float | None) -> None:
        """Make the mocked device report a timer with this much left."""
        if seconds is None:
            device.timer_expires_at = None
            device.timer_remaining = None
        else:
            device.timer_expires_at = monotonic() + seconds
            device.timer_remaining = math.ceil(seconds)

    def test_is_a_timestamp(self, mock_coordinator: HatchBabyRestUpdateCoordinator):
        """Test Home Assistant is told to show it as a time."""
        sensor = HatchBabyRestTimerSensor(mock_coordinator)

        assert sensor.device_class is SensorDeviceClass.TIMESTAMP

    def test_shows_when_the_timer_runs_out(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a running timer reads as its end, to the second."""
        self._run_timer(mock_coordinator.hatch_rest_device, 21076)
        before = dt_util.utcnow()

        ends = HatchBabyRestTimerSensor(mock_coordinator).native_value

        assert ends is not None
        assert ends.microsecond == 0
        assert abs((ends - before).total_seconds() - 21076) <= 1

    def test_shows_nothing_with_no_timer(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test no timer is no end time."""
        self._run_timer(mock_coordinator.hatch_rest_device, None)

        assert HatchBabyRestTimerSensor(mock_coordinator).native_value is None

    def test_a_reread_a_second_out_keeps_the_same_end(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test reading the same timer again on reconnect changes nothing.

        Each read is to the whole second, so the same timer comes back a
        second either way.
        """
        sensor = HatchBabyRestTimerSensor(mock_coordinator)
        device = mock_coordinator.hatch_rest_device
        self._run_timer(device, 3600)
        first = sensor.native_value

        device.timer_expires_at += 1

        assert sensor.native_value == first

    def test_a_changed_timer_moves_the_end(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a timer set to something else shows its new end."""
        sensor = HatchBabyRestTimerSensor(mock_coordinator)
        device = mock_coordinator.hatch_rest_device
        self._run_timer(device, 3600)
        first = sensor.native_value

        self._run_timer(device, 900)

        assert first is not None
        assert sensor.native_value == first - timedelta(seconds=2700)

    def test_ticks_write_only_when_the_end_moves(
        self, hass, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a running timer is not a state change every second.

        It used to show the time left, which changed on every tick.
        """
        sensor = HatchBabyRestTimerSensor(mock_coordinator)
        device = mock_coordinator.hatch_rest_device

        with patch(
            "homeassistant.helpers.entity.Entity.async_write_ha_state"
        ) as written:
            self._run_timer(device, 12)
            sensor._async_tick(None)
            device.timer_remaining = 11
            sensor._async_tick(None)
            sensor._async_tick(None)
            self._run_timer(device, None)
            sensor._async_tick(None)

        assert written.call_count == 2


class TestSensorEntityCategories:
    """Tests that the sensors declare a category Home Assistant will accept."""

    @pytest.mark.parametrize(
        "build",
        [HatchBabyRestTimerSensor],
    )
    def test_no_sensor_claims_to_be_configuration(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator, build
    ):
        """Test no sensor uses the config category.

        SensorEntity.async_internal_added_to_hass raises outright on one that
        does -- a sensor reports state and cannot configure anything. It fails
        at the point of being added, so constructing the entity in a test says
        nothing about it; this asserts the rule directly instead.
        """
        assert build(mock_coordinator).entity_category is not EntityCategory.CONFIG
