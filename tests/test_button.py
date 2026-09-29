"""Tests for Hatch Rest favorite save buttons."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EntityCategory

from custom_components.hatch_rest.button import (
    HatchBabyRestSaveFavoriteButton,
    async_setup_entry,
)
from custom_components.hatch_rest.const import FAVORITE_SLOTS
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator


class TestHatchBabyRestSaveFavoriteButton:
    """Tests for HatchBabyRestSaveFavoriteButton."""

    @pytest.fixture
    def coordinator(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestUpdateCoordinator:
        """Return a coordinator over a device that accepts saves."""
        mock_coordinator.hatch_rest_device.async_save_favorite = AsyncMock()
        return mock_coordinator

    @pytest.mark.asyncio
    async def test_setup_adds_one_button_per_slot(
        self, hass, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test every favorite gets its own button."""
        config_entry = MagicMock()
        config_entry.runtime_data = coordinator
        added = []

        await async_setup_entry(
            hass, config_entry, lambda entities, **_: added.extend(entities)
        )

        assert len(added) == FAVORITE_SLOTS
        assert [button._slot for button in added] == [1, 2, 3, 4, 5, 6]

    def test_unique_ids_do_not_collide(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test each slot's button is distinguishable.

        They all inherit the bare coordinator id, so without a suffix per
        slot Home Assistant would keep one button and drop five.
        """
        ids = {
            HatchBabyRestSaveFavoriteButton(coordinator, slot).unique_id
            for slot in range(1, FAVORITE_SLOTS + 1)
        }

        assert len(ids) == FAVORITE_SLOTS

    def test_name_says_which_slot_it_writes_to(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the button names the slot it overwrites.

        Overwriting a favorite cannot be undone, so the label has to be
        unambiguous on its own.
        """
        button = HatchBabyRestSaveFavoriteButton(coordinator, 3)

        assert button.name == "Hatch Rest Save to Favorite 3"

    def test_is_a_config_entity(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test the buttons sit with the device's configuration.

        They change how the device is set up rather than operate it, which
        also keeps them clear of the day to day controls.
        """
        button = HatchBabyRestSaveFavoriteButton(coordinator, 1)

        assert button.entity_category is EntityCategory.CONFIG

    @pytest.mark.asyncio
    async def test_press_saves_to_its_own_slot(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test pressing a button saves into that button's slot."""
        button = HatchBabyRestSaveFavoriteButton(coordinator, 5)

        await button.async_press()

        coordinator.hatch_rest_device.async_save_favorite.assert_awaited_once_with(5)

    def test_unavailable_while_the_device_is_quiet(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a button is not offered when there is nothing to save.

        Saving relies on the cached state, so pressing it while that is
        unknown would write whatever happened to be there.
        """
        coordinator.hatch_rest_device.seconds_since_state_update = MagicMock(
            return_value=float("inf")
        )

        assert HatchBabyRestSaveFavoriteButton(coordinator, 1).available is False
