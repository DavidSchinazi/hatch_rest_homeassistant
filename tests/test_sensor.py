"""Tests for Hatch Rest sensors."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from custom_components.hatch_rest.const import DOMAIN, PROGRAM_SLOTS
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

    @pytest.mark.asyncio
    async def test_setup_removes_the_old_program_sensors(
        self, hass, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test sensors registered by earlier versions do not linger."""
        registry = er.async_get(hass)
        for slot in range(1, PROGRAM_SLOTS + 1):
            registry.async_get_or_create(
                "sensor", DOMAIN, f"aabbccddeeff_program_{slot}"
            )
        registry.async_get_or_create("number", DOMAIN, "aabbccddeeff_timer")
        timer = registry.async_get_or_create(
            "sensor", DOMAIN, "aabbccddeeff_timer_remaining"
        )
        config_entry = MagicMock()
        config_entry.runtime_data = mock_coordinator

        await async_setup_entry(hass, config_entry, lambda entities, **_: None)

        for slot in range(1, PROGRAM_SLOTS + 1):
            assert (
                registry.async_get_entity_id(
                    "sensor", DOMAIN, f"aabbccddeeff_program_{slot}"
                )
                is None
            )
        # The sleep timer control is back, so its entity is kept.
        assert registry.async_get_entity_id("number", DOMAIN, "aabbccddeeff_timer")
        assert registry.async_get(timer.entity_id) is not None


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

    def test_ticks_write_only_when_the_minute_moves(
        self, hass, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the countdown shows between coordinator updates, without spam.

        Coordinator updates come every 90 seconds, which on its own made the
        sensor count down in steps of one or two minutes.
        """
        sensor = HatchBabyRestTimerSensor(mock_coordinator)
        device = mock_coordinator.hatch_rest_device

        with patch(
            "homeassistant.helpers.entity.Entity.async_write_ha_state"
        ) as written:
            device.timer_remaining = 12
            sensor._async_tick(None)
            sensor._async_tick(None)
            device.timer_remaining = 11
            sensor._async_tick(None)
            device.timer_remaining = None
            sensor._async_tick(None)

        assert written.call_count == 3


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
