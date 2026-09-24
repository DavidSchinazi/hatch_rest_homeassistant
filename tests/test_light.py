"""Tests for Hatch Rest light entity."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR
from homeassistant.components.light.const import ColorMode
from homeassistant.helpers.restore_state import RestoredExtraData
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.hatch_rest.const import DEFAULT_ON_BRIGHTNESS
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.light import HatchBabyRestLight


class TestHatchBabyRestLight:
    """Tests for HatchBabyRestLight."""

    @pytest.fixture
    def light_entity(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestLight:
        """Create light entity."""
        return HatchBabyRestLight(mock_coordinator)

    def test_brightness(self, light_entity: HatchBabyRestLight):
        """Test brightness property."""
        assert light_entity.brightness == 128

    def test_brightness_none(self, light_entity: HatchBabyRestLight):
        """Test brightness when not set."""
        light_entity.coordinator.data["brightness"] = None
        assert light_entity.brightness is None

    def test_color_mode(self, light_entity: HatchBabyRestLight):
        """Test color mode is RGB."""
        assert light_entity.color_mode == ColorMode.RGB

    def test_supported_color_modes(self, light_entity: HatchBabyRestLight):
        """Test supported color modes."""
        assert light_entity.supported_color_modes == {ColorMode.RGB}

    def test_rgb_color(self, light_entity: HatchBabyRestLight):
        """Test RGB color property."""
        assert light_entity.rgb_color == (255, 128, 64)

    def test_is_on_when_power_and_brightness(self, light_entity: HatchBabyRestLight):
        """Test is_on when power is on and brightness > 0."""
        light_entity.coordinator.data["power"] = True
        light_entity.coordinator.data["brightness"] = 100
        assert light_entity.is_on is True

    def test_is_off_when_power_off(self, light_entity: HatchBabyRestLight):
        """Test is_on when power is off."""
        light_entity.coordinator.data["power"] = False
        light_entity.coordinator.data["brightness"] = 100
        assert light_entity.is_on is False

    def test_is_off_when_brightness_zero(self, light_entity: HatchBabyRestLight):
        """Test is_on when brightness is 0."""
        light_entity.coordinator.data["power"] = True
        light_entity.coordinator.data["brightness"] = 0
        assert light_entity.is_on is False

    def test_is_off_when_brightness_none(self, light_entity: HatchBabyRestLight):
        """Test is_on when brightness is None."""
        light_entity.coordinator.data["power"] = True
        light_entity.coordinator.data["brightness"] = None
        assert light_entity.is_on is False

    def test_name(self, light_entity: HatchBabyRestLight):
        """Test name property."""
        assert light_entity.name == "Hatch Rest Light"

    def test_name_when_device_name_none(self, light_entity: HatchBabyRestLight):
        """Test name when device name is None."""
        light_entity._hatch_rest_device.name = None
        assert light_entity.name is None

    @pytest.mark.asyncio
    async def test_async_turn_on_basic(self, light_entity: HatchBabyRestLight):
        """Test turning on the light."""
        light_entity._hatch_rest_device.power = False
        light_entity._hatch_rest_device.turn_power_on = AsyncMock()

        await light_entity.async_turn_on()

        light_entity._hatch_rest_device.turn_power_on.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_with_brightness(
        self, light_entity: HatchBabyRestLight
    ):
        """Test turning on with brightness."""
        light_entity._hatch_rest_device.power = True
        light_entity._hatch_rest_device.set_brightness = AsyncMock()

        await light_entity.async_turn_on(**{ATTR_BRIGHTNESS: 200})

        light_entity._hatch_rest_device.set_brightness.assert_called_once_with(200)

    @pytest.mark.asyncio
    async def test_async_turn_on_with_rgb(self, light_entity: HatchBabyRestLight):
        """Test turning on with RGB color."""
        light_entity._hatch_rest_device.power = True
        light_entity._hatch_rest_device.set_color = AsyncMock()

        await light_entity.async_turn_on(**{ATTR_RGB_COLOR: (255, 0, 128)})

        light_entity._hatch_rest_device.set_color.assert_called_once_with(255, 0, 128)

    @pytest.mark.asyncio
    async def test_async_turn_on_with_brightness_and_rgb(
        self, light_entity: HatchBabyRestLight
    ):
        """Test setting both takes a single command."""
        light_entity._hatch_rest_device.set_color_and_brightness = AsyncMock()
        light_entity._hatch_rest_device.set_brightness = AsyncMock()
        light_entity._hatch_rest_device.set_color = AsyncMock()

        await light_entity.async_turn_on(
            **{ATTR_BRIGHTNESS: 181, ATTR_RGB_COLOR: (215, 150, 255)}
        )

        light_entity._hatch_rest_device.set_color_and_brightness.assert_called_once_with(
            215, 150, 255, 181
        )
        light_entity._hatch_rest_device.set_brightness.assert_not_called()
        light_entity._hatch_rest_device.set_color.assert_not_called()

    @pytest.mark.asyncio
    async def test_brightness_survives_a_restart(
        self, light_entity: HatchBabyRestLight
    ):
        """Test the brightness to come back to is restored after a restart."""
        light_entity._last_on_brightness = DEFAULT_ON_BRIGHTNESS
        stored = RestoredExtraData({"last_on_brightness": 64})

        with (
            patch.object(
                HatchBabyRestLight,
                "async_get_last_extra_data",
                AsyncMock(return_value=stored),
            ),
            # Subscribing to the coordinator is not what is under test, and
            # leaves its refresh timer behind.
            patch.object(
                CoordinatorEntity, "async_added_to_hass", new_callable=AsyncMock
            ),
        ):
            await light_entity.async_added_to_hass()

        assert light_entity._last_on_brightness == 64

    def test_extra_restore_state_data(self, light_entity: HatchBabyRestLight):
        """Test what gets written out for the next run."""
        light_entity._last_on_brightness = 64

        assert light_entity.extra_restore_state_data.as_dict() == {
            "last_on_brightness": 64
        }

    @pytest.mark.asyncio
    async def test_toggle_off_then_on_comes_back(
        self, light_entity: HatchBabyRestLight
    ):
        """Test a plain off/on toggle relights the light.

        Turning off writes a brightness of zero and the device keeps no
        memory of what it was, so turning on without a brightness has to
        supply the one it had.
        """
        device = light_entity._hatch_rest_device
        device.set_brightness = AsyncMock()
        device.set_color_and_brightness = AsyncMock()
        device.power = True

        # The light was on at 128, per the coordinator fixture.
        await light_entity.async_turn_off()
        device.set_brightness.assert_called_once_with(0)

        # The device now reports zero brightness, still powered.
        light_entity.coordinator.data["brightness"] = 0
        device.set_brightness.reset_mock()

        await light_entity.async_turn_on()

        device.set_brightness.assert_called_once_with(128)

    @pytest.mark.asyncio
    async def test_turn_on_while_lit_sends_nothing(
        self, light_entity: HatchBabyRestLight
    ):
        """Test turning on a light that is already lit is left alone."""
        device = light_entity._hatch_rest_device
        device.set_brightness = AsyncMock()
        device.set_color_and_brightness = AsyncMock()
        device.turn_power_on = AsyncMock()
        device.power = True

        await light_entity.async_turn_on()

        device.set_brightness.assert_not_called()
        device.set_color_and_brightness.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_on_from_dark_uses_a_default(
        self, light_entity: HatchBabyRestLight
    ):
        """Test a light never seen lit still turns on."""
        device = light_entity._hatch_rest_device
        device.set_brightness = AsyncMock()
        device.power = True
        light_entity._last_on_brightness = DEFAULT_ON_BRIGHTNESS
        light_entity.coordinator.data["brightness"] = 0

        await light_entity.async_turn_on()

        device.set_brightness.assert_called_once_with(DEFAULT_ON_BRIGHTNESS)

    @pytest.mark.asyncio
    async def test_async_turn_on_powers_on_if_needed(
        self, light_entity: HatchBabyRestLight
    ):
        """Test turn_on powers device on if off."""
        light_entity._hatch_rest_device.power = False
        light_entity._hatch_rest_device.turn_power_on = AsyncMock()
        light_entity._hatch_rest_device.set_brightness = AsyncMock()

        await light_entity.async_turn_on(**{ATTR_BRIGHTNESS: 100})

        light_entity._hatch_rest_device.turn_power_on.assert_called_once()
        light_entity._hatch_rest_device.set_brightness.assert_called_once_with(100)

    @pytest.mark.asyncio
    async def test_async_turn_off(self, light_entity: HatchBabyRestLight):
        """Test turning off the light."""
        light_entity._hatch_rest_device.set_brightness = AsyncMock()

        await light_entity.async_turn_off()

        light_entity._hatch_rest_device.set_brightness.assert_called_once_with(0)
