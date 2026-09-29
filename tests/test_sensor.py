"""Tests for Hatch Rest schedule sensors."""

from unittest.mock import MagicMock

import pytest
from homeassistant.const import EntityCategory

from custom_components.hatch_rest.api import _parse_schedule_block
from custom_components.hatch_rest.const import SCHEDULE_SLOTS
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.sensor import (
    HatchBabyRestScheduleSensor,
    HatchBabyRestTimerSensor,
    async_setup_entry,
)

# Taken from the parser rather than written out by hand. A fixture spelled
# out separately drifts the moment a field is renamed, and agrees with
# whatever the entity does with it -- which is how a KeyError reached a
# device with every test passing.
SCHEDULE_BLOCK = bytes.fromhex("01f80db2650728100e000000007f2dd1fd003e40")
SCHEDULE = {**_parse_schedule_block(SCHEDULE_BLOCK), "name": "Weekday Sleep"}


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

        schedules = [s for s in added if isinstance(s, HatchBabyRestScheduleSensor)]
        timers = [s for s in added if isinstance(s, HatchBabyRestTimerSensor)]

        assert [sensor._slot for sensor in schedules] == list(range(1, 11))
        assert len(timers) == 1

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

    def test_state_is_the_name_from_the_device(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the sensor reads as the schedule's name.

        Which says what it is for, where the time does not and the entity's
        own name says neither.
        """
        sensor = HatchBabyRestScheduleSensor(coordinator, 1)

        assert sensor.native_value == "Weekday Sleep"

    def test_an_unnamed_slot_falls_back_to_its_time(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a slot the device never named still reads as something real."""
        del coordinator.hatch_rest_device.schedules[1]["name"]

        assert HatchBabyRestScheduleSensor(coordinator, 1).native_value == "07:30"

    def test_the_time_rides_along(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test the time is exposed even though it is not the state."""
        attributes = HatchBabyRestScheduleSensor(coordinator, 1).extra_state_attributes

        assert attributes["time"] == "07:30"
        assert attributes["name"] == "Weekday Sleep"

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
            "name": "Weekday Sleep",
            "time": "07:30",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "duration_seconds": 3600,
            "raw": SCHEDULE_BLOCK.hex(),
            "color": (253, 209, 45),
            "brightness": 127,
            "sound": "rain",
            "volume": 40,
            "enabled": True,
            "flags": 0x40,
            "written_timestamp": SCHEDULE["written_timestamp"],
        }

    def test_attributes_keep_the_raw_flags_byte(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the flags byte is reported, not just its reading.

        Populated slots read 0xdf and empty ones 0x9f, which is what settled
        the enabled bit as 0x40. Worth keeping visible.
        """
        coordinator.hatch_rest_device.schedules[1]["flags"] = 0x9F

        attributes = HatchBabyRestScheduleSensor(coordinator, 1).extra_state_attributes

        assert attributes["flags"] == 0x9F

    def test_attributes_report_an_unnamed_sound_by_number(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a sound with no name still shows something."""
        coordinator.hatch_rest_device.schedules[1]["sound"] = None
        coordinator.hatch_rest_device.schedules[1]["sound_id"] = 8

        attributes = HatchBabyRestScheduleSensor(coordinator, 1).extra_state_attributes

        assert attributes["sound"] == 8

    def test_is_a_diagnostic_entity(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test schedules are filed as diagnostic rather than configuration."""
        sensor = HatchBabyRestScheduleSensor(coordinator, 1)

        assert sensor.entity_category is EntityCategory.DIAGNOSTIC


class TestHatchBabyRestTimerSensor:
    """Tests for HatchBabyRestTimerSensor."""

    def test_reports_the_minutes_left(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the sensor follows what the device layer counts down."""
        mock_coordinator.hatch_rest_device.timer_remaining = 12

        assert HatchBabyRestTimerSensor(mock_coordinator).native_value == 12

    def test_reports_nothing_when_no_timer_is_running(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test an idle device reports no value rather than zero.

        Zero minutes left is a timer about to fire, which is not the same as
        having none set.
        """
        mock_coordinator.hatch_rest_device.timer_remaining = None

        assert HatchBabyRestTimerSensor(mock_coordinator).native_value is None

    def test_unique_id_does_not_collide_with_a_schedule(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the timer is distinguishable from the ten schedules."""
        ids = {
            HatchBabyRestScheduleSensor(mock_coordinator, slot).unique_id
            for slot in range(1, SCHEDULE_SLOTS + 1)
        }
        ids.add(HatchBabyRestTimerSensor(mock_coordinator).unique_id)

        assert len(ids) == SCHEDULE_SLOTS + 1


class TestSensorEntityCategories:
    """Tests that the sensors declare a category Home Assistant will accept."""

    @pytest.mark.parametrize(
        "build",
        [
            lambda coordinator: HatchBabyRestScheduleSensor(coordinator, 1),
            HatchBabyRestTimerSensor,
        ],
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


class TestSensorsAgainstTheParser:
    """Tests that the sensors only read fields the parser produces."""

    def test_every_attribute_comes_from_a_real_block(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a schedule straight from the parser renders without a KeyError.

        The entity reads the parser's dictionary by key, so a field renamed
        on one side and not the other throws only once a real block reaches
        it -- which is not something a hand-written fixture would notice,
        since it gets renamed to match whatever the entity expects.
        """
        mock_coordinator.hatch_rest_device.schedules = {
            1: _parse_schedule_block(SCHEDULE_BLOCK)
        }
        sensor = HatchBabyRestScheduleSensor(mock_coordinator, 1)

        # No name has arrived for this one, so it falls back to the time.
        assert sensor.native_value == "07:30"
        # Every key the entity reaches for has to be one the parser wrote.
        assert sensor.extra_state_attributes["written_timestamp"] is not None

    def test_a_slot_known_only_by_name_does_not_throw(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a name arriving before its block leaves the sensor usable.

        Names come as their own notification, so a slot can hold nothing but
        a name for a moment -- or for good, if the block never parses.
        """
        mock_coordinator.hatch_rest_device.schedules = {1: {"name": "Bed Time"}}
        sensor = HatchBabyRestScheduleSensor(mock_coordinator, 1)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}
