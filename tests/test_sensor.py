"""Tests for Hatch Rest schedule sensors."""

from unittest.mock import MagicMock

import pytest
from homeassistant.const import EntityCategory

from custom_components.hatch_rest.const import (
    SCHEDULE_SLOTS,
    PyHatchBabyRestSound,
)
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.sensor import (
    HatchBabyRestScheduleSensor,
    async_setup_entry,
)

SCHEDULE = {
    "hour": 7,
    "minute": 30,
    "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
    "days_mask": 0x3E,
    "color": (253, 209, 45),
    "brightness": 127,
    "sound": PyHatchBabyRestSound.rain,
    "sound_id": 7,
    "volume": 40,
    "enabled": True,
    "flags": 0x40,
    "modified_timestamp": 1738000000,
}


class TestHatchBabyRestScheduleSensor:
    """Tests for HatchBabyRestScheduleSensor."""

    @pytest.fixture
    def coordinator(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestUpdateCoordinator:
        """Return a coordinator with one schedule read."""
        mock_coordinator.hatch_rest_device.schedules = {1: dict(SCHEDULE)}
        return mock_coordinator

    @pytest.mark.asyncio
    async def test_setup_adds_one_sensor_per_slot(
        self, hass, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test every schedule slot gets a sensor."""
        config_entry = MagicMock()
        config_entry.runtime_data = coordinator
        added = []

        await async_setup_entry(
            hass, config_entry, lambda entities, **_: added.extend(entities)
        )

        assert len(added) == SCHEDULE_SLOTS
        assert [sensor._slot for sensor in added] == list(range(1, 11))

    def test_unique_ids_do_not_collide(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test each slot's sensor is distinguishable.

        They all inherit the bare coordinator id, so without a suffix per
        slot Home Assistant would keep one sensor and drop nine.
        """
        ids = {
            HatchBabyRestScheduleSensor(coordinator, slot).unique_id
            for slot in range(1, SCHEDULE_SLOTS + 1)
        }

        assert len(ids) == SCHEDULE_SLOTS

    def test_state_is_the_time_of_day(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the sensor reads as the time the schedule runs."""
        assert HatchBabyRestScheduleSensor(coordinator, 1).native_value == "07:30"

    def test_midnight_keeps_its_leading_zeros(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a single digit hour or minute is padded."""
        coordinator.hatch_rest_device.schedules[1]["hour"] = 0
        coordinator.hatch_rest_device.schedules[1]["minute"] = 5

        assert HatchBabyRestScheduleSensor(coordinator, 1).native_value == "00:05"

    def test_unread_slot_is_unknown(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test a slot nobody has asked about reports nothing.

        Which is not the same as a schedule that is not set.
        """
        sensor = HatchBabyRestScheduleSensor(coordinator, 9)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    def test_attributes_report_the_rest_of_the_schedule(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the schedule's other fields are exposed."""
        attributes = HatchBabyRestScheduleSensor(coordinator, 1).extra_state_attributes

        assert attributes == {
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "color": (253, 209, 45),
            "brightness": 127,
            "sound": "rain",
            "volume": 40,
            "enabled": True,
            "flags": 0x40,
            "modified_timestamp": 1738000000,
        }

    def test_attributes_keep_the_raw_flags_byte(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the flags byte is reported, not just its reading.

        Which bit means enabled is unsettled between 0x40 and 0x80, and a
        disabled slot's raw byte is what will settle it.
        """
        coordinator.hatch_rest_device.schedules[1]["flags"] = 0x80

        attributes = HatchBabyRestScheduleSensor(coordinator, 1).extra_state_attributes

        assert attributes["flags"] == 0x80

    def test_attributes_report_an_unnamed_sound_by_number(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a sound with no name still shows something."""
        coordinator.hatch_rest_device.schedules[1]["sound"] = None
        coordinator.hatch_rest_device.schedules[1]["sound_id"] = 8

        attributes = HatchBabyRestScheduleSensor(coordinator, 1).extra_state_attributes

        assert attributes["sound"] == 8

    def test_is_a_config_entity(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test schedules sit with the device's configuration."""
        sensor = HatchBabyRestScheduleSensor(coordinator, 1)

        assert sensor.entity_category is EntityCategory.CONFIG
