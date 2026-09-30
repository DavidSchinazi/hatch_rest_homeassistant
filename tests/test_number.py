"""Tests for the Hatch Rest sleep timer control."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.number import (
    HatchBabyRestTimerNumber,
    async_setup_entry,
)


class TestHatchBabyRestTimerNumber:
    """Tests for HatchBabyRestTimerNumber."""

    @pytest.fixture
    def number(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestTimerNumber:
        """Create the timer control."""
        mock_coordinator.hatch_rest_device.async_set_timer = AsyncMock()
        return HatchBabyRestTimerNumber(mock_coordinator)

    @pytest.mark.asyncio
    async def test_setup_adds_one_control(
        self, hass, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the platform adds the timer control."""
        config_entry = MagicMock()
        config_entry.runtime_data = mock_coordinator
        added = []

        await async_setup_entry(
            hass, config_entry, lambda entities, **_: added.extend(entities)
        )

        assert len(added) == 1
        assert isinstance(added[0], HatchBabyRestTimerNumber)

    def test_keeps_the_id_it_had_before(self, number: HatchBabyRestTimerNumber):
        """Test it comes back as the entity it was before it was taken out."""
        assert number.unique_id == "aabbccddeeff_timer"
        assert number.name == "Hatch Rest Sleep Timer"

    def test_shows_what_is_left(self, number: HatchBabyRestTimerNumber):
        """Test the value is the minutes left, and zero with no timer."""
        number._hatch_rest_device.timer_remaining = 42
        assert number.native_value == 42

        number._hatch_rest_device.timer_remaining = None
        assert number.native_value == 0

    def test_reaches_as_far_as_four_hex_digits_of_seconds(
        self, number: HatchBabyRestTimerNumber
    ):
        """Test the app's nine hours fit, and the ceiling is what SD holds."""
        assert number.native_max_value == 1092
        assert number.native_min_value == 0

    @pytest.mark.asyncio
    async def test_setting_it_sends_seconds(self, number: HatchBabyRestTimerNumber):
        """Test minutes from the control go to the device as seconds."""
        await number.async_set_native_value(90)

        number._hatch_rest_device.async_set_timer.assert_awaited_once_with(5400)

    def test_ticks_write_only_when_the_minute_moves(
        self, number: HatchBabyRestTimerNumber
    ):
        """Test it counts down between coordinator updates, like the sensor."""
        device = number._hatch_rest_device

        with patch(
            "homeassistant.helpers.entity.Entity.async_write_ha_state"
        ) as written:
            device.timer_remaining = 12
            number._async_tick(None)
            number._async_tick(None)
            device.timer_remaining = 11
            number._async_tick(None)

        assert written.call_count == 2
