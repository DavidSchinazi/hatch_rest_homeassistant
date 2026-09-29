"""Tests for Hatch Rest program sensors."""

from unittest.mock import MagicMock

import pytest
from homeassistant.const import EntityCategory

from custom_components.hatch_rest.api import _parse_program_block
from custom_components.hatch_rest.const import PROGRAM_SLOTS
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.sensor import (
    HatchBabyRestProgramSensor,
    HatchBabyRestTimerSensor,
    async_setup_entry,
)

# Taken from the parser rather than written out by hand. A fixture spelled
# out separately drifts the moment a field is renamed, and agrees with
# whatever the entity does with it -- which is how a KeyError reached a
# device with every test passing.
PROGRAM_BLOCK = bytes.fromhex("01f80db2650728100e000000007f2dd1fd003e40")
PROGRAM = {**_parse_program_block(PROGRAM_BLOCK), "name": "Weekday Sleep"}


class TestHatchBabyRestProgramSensor:
    """Tests for HatchBabyRestProgramSensor."""

    @pytest.fixture
    def coordinator(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestUpdateCoordinator:
        """Return a coordinator with one program read."""
        mock_coordinator.hatch_rest_device.programs = {1: dict(PROGRAM)}
        return mock_coordinator

    @pytest.mark.asyncio
    async def test_setup_adds_one_sensor_per_slot(
        self, hass, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test every program slot gets a sensor."""
        config_entry = MagicMock()
        config_entry.runtime_data = coordinator
        added = []

        await async_setup_entry(
            hass, config_entry, lambda entities, **_: added.extend(entities)
        )

        programs = [s for s in added if isinstance(s, HatchBabyRestProgramSensor)]
        timers = [s for s in added if isinstance(s, HatchBabyRestTimerSensor)]

        assert [sensor._slot for sensor in programs] == list(range(1, 11))
        assert len(timers) == 1

    def test_unique_ids_do_not_collide(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test each slot's sensor is distinguishable.

        They all inherit the bare coordinator id, so without a suffix per
        slot Home Assistant would keep one sensor and drop nine.
        """
        ids = {
            HatchBabyRestProgramSensor(coordinator, slot).unique_id
            for slot in range(1, PROGRAM_SLOTS + 1)
        }

        assert len(ids) == PROGRAM_SLOTS

    def test_state_is_the_name_from_the_device(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the sensor reads as the program's name.

        Which says what it is for, where the time does not and the entity's
        own name says neither.
        """
        sensor = HatchBabyRestProgramSensor(coordinator, 1)

        assert sensor.native_value == "Weekday Sleep"

    def test_an_unnamed_slot_falls_back_to_its_time(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a slot the device never named still reads as something real."""
        del coordinator.hatch_rest_device.programs[1]["name"]

        assert HatchBabyRestProgramSensor(coordinator, 1).native_value == "07:30"

    def test_the_time_rides_along(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test the time is exposed even though it is not the state."""
        attributes = HatchBabyRestProgramSensor(coordinator, 1).extra_state_attributes

        assert attributes["time"] == "07:30"
        assert attributes["name"] == "Weekday Sleep"

    def test_unread_slot_is_unknown(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test a slot nobody has asked about reports nothing.

        Which is not the same as a program that is not set.
        """
        sensor = HatchBabyRestProgramSensor(coordinator, 9)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}

    def test_attributes_report_the_rest_of_the_program(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the program's other fields are exposed."""
        attributes = HatchBabyRestProgramSensor(coordinator, 1).extra_state_attributes

        assert attributes == {
            "name": "Weekday Sleep",
            "time": "07:30",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "duration_seconds": 3600,
            "raw": PROGRAM_BLOCK.hex(),
            "color": (253, 209, 45),
            "brightness": 127,
            "sound": "rain",
            "volume": 40,
            "enabled": True,
            "toddler_lock": False,
            "flags": 0x40,
            "start_timestamp": PROGRAM["start_timestamp"],
        }

    def test_attributes_keep_the_raw_flags_byte(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the flags byte is reported, not just its reading.

        Populated slots read 0xdf and empty ones 0x9f, which is what settled
        the enabled bit as 0x40. Worth keeping visible.
        """
        coordinator.hatch_rest_device.programs[1]["flags"] = 0x9F

        attributes = HatchBabyRestProgramSensor(coordinator, 1).extra_state_attributes

        assert attributes["flags"] == 0x9F

    def test_attributes_report_an_unnamed_sound_by_number(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a sound with no name still shows something."""
        coordinator.hatch_rest_device.programs[1]["sound"] = None
        coordinator.hatch_rest_device.programs[1]["sound_id"] = 8

        attributes = HatchBabyRestProgramSensor(coordinator, 1).extra_state_attributes

        assert attributes["sound"] == 8

    def test_is_a_diagnostic_entity(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test programs are filed as diagnostic rather than configuration."""
        sensor = HatchBabyRestProgramSensor(coordinator, 1)

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

    def test_unique_id_does_not_collide_with_a_program(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the timer is distinguishable from the ten programs."""
        ids = {
            HatchBabyRestProgramSensor(mock_coordinator, slot).unique_id
            for slot in range(1, PROGRAM_SLOTS + 1)
        }
        ids.add(HatchBabyRestTimerSensor(mock_coordinator).unique_id)

        assert len(ids) == PROGRAM_SLOTS + 1


class TestSensorEntityCategories:
    """Tests that the sensors declare a category Home Assistant will accept."""

    @pytest.mark.parametrize(
        "build",
        [
            lambda coordinator: HatchBabyRestProgramSensor(coordinator, 1),
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
        """Test a program straight from the parser renders without a KeyError.

        The entity reads the parser's dictionary by key, so a field renamed
        on one side and not the other throws only once a real block reaches
        it -- which is not something a hand-written fixture would notice,
        since it gets renamed to match whatever the entity expects.
        """
        mock_coordinator.hatch_rest_device.programs = {
            1: _parse_program_block(PROGRAM_BLOCK)
        }
        sensor = HatchBabyRestProgramSensor(mock_coordinator, 1)

        # No name has arrived for this one, so it falls back to the time.
        assert sensor.native_value == "07:30"
        # Every key the entity reaches for has to be one the parser wrote.
        assert sensor.extra_state_attributes["start_timestamp"] is not None

    def test_a_slot_known_only_by_name_does_not_throw(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a name arriving before its block leaves the sensor usable.

        Names come as their own notification, so a slot can hold nothing but
        a name for a moment -- or for good, if the block never parses.
        """
        mock_coordinator.hatch_rest_device.programs = {1: {"name": "Bed Time"}}
        sensor = HatchBabyRestProgramSensor(mock_coordinator, 1)

        assert sensor.native_value is None
        assert sensor.extra_state_attributes == {}
