"""Tests for Hatch Rest favorite select entity."""

from unittest.mock import AsyncMock

import pytest

from custom_components.hatch_rest.const import PyHatchBabyRestSound
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.select import (
    OPTION_NONE,
    HatchBabyRestFavoriteSelect,
)


class TestHatchBabyRestFavoriteSelect:
    """Tests for HatchBabyRestFavoriteSelect."""

    @pytest.fixture
    def select_entity(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestFavoriteSelect:
        """Create the select entity over a device with no favorites read yet."""
        mock_coordinator.hatch_rest_device.favorites = {}
        mock_coordinator.hatch_rest_device.set_active_favorite = AsyncMock()
        return HatchBabyRestFavoriteSelect(mock_coordinator)

    def test_unique_id_is_suffixed(self, select_entity: HatchBabyRestFavoriteSelect):
        """Test the entity does not reuse the bare coordinator id.

        Every other entity does, which only works while no two share a
        platform.
        """
        assert select_entity.unique_id == "aabbccddeeff_favorite"

    def test_options_before_anything_has_been_read(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test all six slots are offered even with no names yet."""
        assert select_entity.options == [
            OPTION_NONE,
            "Favorite 1",
            "Favorite 2",
            "Favorite 3",
            "Favorite 4",
            "Favorite 5",
            "Favorite 6",
        ]

    def test_options_use_names_once_known(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test a named slot is offered under its name."""
        select_entity._hatch_rest_device.favorites = {
            2: {"name": "Bedtime"},
            5: {"name": "Nap"},
        }

        assert select_entity.options == [
            OPTION_NONE,
            "Favorite 1",
            "Bedtime",
            "Favorite 3",
            "Favorite 4",
            "Nap",
            "Favorite 6",
        ]

    def test_duplicate_names_fall_back_to_slot_numbers(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test two slots sharing a name do not become one ambiguous option.

        Home Assistant matches the option the user picked by string, so a
        duplicate would make one of the two unreachable.
        """
        select_entity._hatch_rest_device.favorites = {
            1: {"name": "Sleep"},
            4: {"name": "Sleep"},
        }

        options = select_entity.options

        assert options.count("Sleep") == 0
        assert "Favorite 1" in options
        assert "Favorite 4" in options
        assert len(set(options)) == len(options)

    def test_current_option_is_none_when_nothing_is_playing(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test no active favorite reads as the None option."""
        select_entity.coordinator.data["active_favorite"] = None

        assert select_entity.current_option == OPTION_NONE

    def test_current_option_follows_the_device(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test the active favorite is reflected back."""
        select_entity._hatch_rest_device.favorites = {3: {"name": "Bedtime"}}
        select_entity.coordinator.data["active_favorite"] = 3

        assert select_entity.current_option == "Bedtime"

    def test_attributes_report_what_each_favorite_holds(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test the slot contents are exposed for reading."""
        select_entity._hatch_rest_device.favorites = {
            1: {
                "name": "Bedtime",
                "color": (253, 209, 45),
                "brightness": 127,
                "sound": PyHatchBabyRestSound.ocean,
                "sound_id": 5,
                "volume": 84,
                "enabled": True,
            }
        }

        favorite = select_entity.extra_state_attributes["favorites"][1]

        assert favorite == {
            "name": "Bedtime",
            "color": (253, 209, 45),
            "brightness": 127,
            "sound": "ocean",
            "volume": 84,
            "enabled": True,
        }

    def test_attributes_report_an_unnamed_sound_by_number(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test a sound with no name still shows something."""
        select_entity._hatch_rest_device.favorites = {1: {"sound": None, "sound_id": 8}}

        assert select_entity.extra_state_attributes["favorites"][1]["sound"] == 8

    @pytest.mark.asyncio
    async def test_selecting_a_favorite_plays_it(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test choosing an option asks the device for that slot."""
        select_entity._hatch_rest_device.favorites = {2: {"name": "Bedtime"}}

        await select_entity.async_select_option("Bedtime")

        select_entity._hatch_rest_device.set_active_favorite.assert_awaited_once_with(2)

    @pytest.mark.asyncio
    async def test_selecting_none_deselects(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test the None option clears the active favorite."""
        await select_entity.async_select_option(OPTION_NONE)

        select_entity._hatch_rest_device.set_active_favorite.assert_awaited_once_with(
            None
        )

    @pytest.mark.asyncio
    async def test_selecting_an_unknown_option_is_refused(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test a stale option is not silently mapped to some slot."""
        with pytest.raises(ValueError, match="not one of"):
            await select_entity.async_select_option("Bedtime")

        select_entity._hatch_rest_device.set_active_favorite.assert_not_awaited()
