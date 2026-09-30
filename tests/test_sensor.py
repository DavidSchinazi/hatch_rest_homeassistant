"""Tests for Hatch Rest sensors."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import EntityCategory

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

    @pytest.mark.parametrize(
        ("seconds", "shown"),
        [
            (21076, "5:51:16"),
            (3600, "1:00:00"),
            (252, "0:04:12"),
            (1, "0:00:01"),
            (None, "Off"),
        ],
    )
    def test_shows_hours_minutes_and_seconds_or_off(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator, seconds, shown
    ):
        """Test the time left reads as H:MM:SS, and Off with no timer."""
        mock_coordinator.hatch_rest_device.timer_remaining = seconds

        assert HatchBabyRestTimerSensor(mock_coordinator).native_value == shown

    def test_ticks_write_only_when_what_it_shows_moves(
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
