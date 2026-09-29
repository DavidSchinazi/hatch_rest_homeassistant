"""Tests for Hatch Rest switch entity."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EntityCategory

from custom_components.hatch_rest.api import _parse_program_block
from custom_components.hatch_rest.const import FAVORITE_SLOTS, PROGRAM_SLOTS
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.switch import (
    HatchBabyRestFavoriteEnabledSwitch,
    HatchBabyRestProgramSwitch,
    HatchBabyRestSwitch,
    async_setup_entry,
)

# Taken from the parser rather than written out by hand. A fixture spelled
# out separately drifts the moment a field is renamed, and agrees with
# whatever the entity does with it -- which is how a KeyError reached a
# device with every test passing.
PROGRAM_BLOCK = bytes.fromhex("01f80db2650728100e000000007f2dd1fd003e40")
PROGRAM = {**_parse_program_block(PROGRAM_BLOCK), "name": "Weekday Sleep"}


class TestHatchBabyRestSwitch:
    """Tests for HatchBabyRestSwitch."""

    @pytest.fixture
    def switch_entity(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestSwitch:
        """Create switch entity."""
        return HatchBabyRestSwitch(mock_coordinator)

    def test_is_on_true(self, switch_entity: HatchBabyRestSwitch):
        """Test is_on when power is True."""
        switch_entity.coordinator.data["power"] = True
        assert switch_entity.is_on is True

    def test_is_on_false(self, switch_entity: HatchBabyRestSwitch):
        """Test is_on when power is False."""
        switch_entity.coordinator.data["power"] = False
        assert switch_entity.is_on is False

    def test_is_on_none(self, switch_entity: HatchBabyRestSwitch):
        """Test is_on when power is None."""
        switch_entity.coordinator.data["power"] = None
        assert switch_entity.is_on is None

    def test_name(self, switch_entity: HatchBabyRestSwitch):
        """Test name property."""
        assert switch_entity.name == "Hatch Rest Switch"

    def test_name_when_device_name_none(self, switch_entity: HatchBabyRestSwitch):
        """Test name when device name is None."""
        switch_entity._hatch_rest_device.name = None
        assert switch_entity.name is None

    @pytest.mark.asyncio
    async def test_async_turn_on_when_off(self, switch_entity: HatchBabyRestSwitch):
        """Test turning on when switch is off."""
        switch_entity.coordinator.data["power"] = False
        switch_entity._hatch_rest_device.turn_power_on = AsyncMock()

        await switch_entity.async_turn_on()

        switch_entity._hatch_rest_device.turn_power_on.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_on_when_already_on(
        self, switch_entity: HatchBabyRestSwitch
    ):
        """Test turning on when switch is already on does nothing."""
        switch_entity.coordinator.data["power"] = True
        switch_entity._hatch_rest_device.turn_power_on = AsyncMock()

        await switch_entity.async_turn_on()

        switch_entity._hatch_rest_device.turn_power_on.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_turn_off_when_on(self, switch_entity: HatchBabyRestSwitch):
        """Test turning off when switch is on."""
        switch_entity.coordinator.data["power"] = True
        switch_entity._hatch_rest_device.turn_power_off = AsyncMock()

        await switch_entity.async_turn_off()

        switch_entity._hatch_rest_device.turn_power_off.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off_when_already_off(
        self, switch_entity: HatchBabyRestSwitch
    ):
        """Test turning off when switch is already off does nothing."""
        switch_entity.coordinator.data["power"] = False
        switch_entity._hatch_rest_device.turn_power_off = AsyncMock()

        await switch_entity.async_turn_off()

        switch_entity._hatch_rest_device.turn_power_off.assert_not_called()


class TestHatchBabyRestFavoriteEnabledSwitch:
    """Tests for HatchBabyRestFavoriteEnabledSwitch."""

    @pytest.fixture
    def coordinator(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestUpdateCoordinator:
        """Return a coordinator over a device with one slot read."""
        mock_coordinator.hatch_rest_device.favorites = {
            1: {"enabled": True},
            2: {"enabled": False},
        }
        mock_coordinator.hatch_rest_device.async_set_favorite = AsyncMock()
        return mock_coordinator

    @pytest.mark.asyncio
    async def test_setup_adds_the_power_switch_and_one_per_slot(
        self, hass, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the platform carries the power switch plus every favorite."""
        config_entry = MagicMock()
        config_entry.runtime_data = coordinator
        added = []

        await async_setup_entry(
            hass, config_entry, lambda entities, **_: added.extend(entities)
        )

        favorites = [
            s for s in added if isinstance(s, HatchBabyRestFavoriteEnabledSwitch)
        ]
        programs = [s for s in added if isinstance(s, HatchBabyRestProgramSwitch)]

        assert len(added) == 1 + FAVORITE_SLOTS + PROGRAM_SLOTS
        assert isinstance(added[0], HatchBabyRestSwitch)
        assert [switch._slot for switch in favorites] == [1, 2, 3, 4, 5, 6]
        assert [switch._slot for switch in programs] == list(range(1, 11))

    def test_unique_ids_do_not_collide_with_the_power_switch(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test every switch on this platform is distinguishable.

        They all inherit the bare coordinator id, and the power switch keeps
        it, so without a suffix Home Assistant would keep one of the seven
        and drop the rest.
        """
        ids = (
            [HatchBabyRestSwitch(coordinator).unique_id]
            + [
                HatchBabyRestFavoriteEnabledSwitch(coordinator, slot).unique_id
                for slot in range(1, FAVORITE_SLOTS + 1)
            ]
            + [
                HatchBabyRestProgramSwitch(coordinator, slot).unique_id
                for slot in range(1, PROGRAM_SLOTS + 1)
            ]
        )

        assert len(set(ids)) == 1 + FAVORITE_SLOTS + PROGRAM_SLOTS

    def test_power_switch_keeps_its_original_id(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the existing switch is not renamed by this addition.

        Changing it would orphan the entity already in the registry and lose
        its history.
        """
        assert HatchBabyRestSwitch(coordinator).unique_id == "aabbccddeeff"

    def test_reports_whether_the_favorite_is_offered(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the switch follows the slot's enabled flag."""
        assert HatchBabyRestFavoriteEnabledSwitch(coordinator, 1).is_on is True
        assert HatchBabyRestFavoriteEnabledSwitch(coordinator, 2).is_on is False

    def test_unread_slot_is_unknown_rather_than_off(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a slot nobody has asked about does not look disabled."""
        assert HatchBabyRestFavoriteEnabledSwitch(coordinator, 6).is_on is None

    @pytest.mark.asyncio
    async def test_turning_on_changes_only_the_flag(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test enabling leaves the favorite's contents alone.

        The commit rewrites every field, so the device layer fills the rest
        from the slot -- nothing else may be passed here.
        """
        await HatchBabyRestFavoriteEnabledSwitch(coordinator, 3).async_turn_on()

        coordinator.hatch_rest_device.async_set_favorite.assert_awaited_once_with(
            3, enabled=True
        )

    @pytest.mark.asyncio
    async def test_turning_off_changes_only_the_flag(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test disabling likewise only moves the flag."""
        await HatchBabyRestFavoriteEnabledSwitch(coordinator, 4).async_turn_off()

        coordinator.hatch_rest_device.async_set_favorite.assert_awaited_once_with(
            4, enabled=False
        )

    def test_is_a_config_entity(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test these sit with the device's configuration."""
        switch = HatchBabyRestFavoriteEnabledSwitch(coordinator, 1)

        assert switch.entity_category is EntityCategory.CONFIG


class TestHatchBabyRestProgramSwitch:
    """Tests for HatchBabyRestProgramSwitch."""

    @pytest.fixture
    def coordinator(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestUpdateCoordinator:
        """Return a coordinator with one program read."""
        mock_coordinator.hatch_rest_device.programs = {1: dict(PROGRAM)}
        mock_coordinator.hatch_rest_device.async_set_program_enabled = AsyncMock()
        return mock_coordinator

    def test_name_carries_the_program_name(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the name says what the program is for, when the device says."""
        assert (
            HatchBabyRestProgramSwitch(coordinator, 1).name
            == "Hatch Rest Program 1 (Weekday Sleep)"
        )

    def test_an_unnamed_slot_is_named_by_number(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a slot with no name still gets one."""
        assert HatchBabyRestProgramSwitch(coordinator, 9).name == "Hatch Rest Program 9"

    def test_reports_whether_the_program_is_enabled(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the switch follows the slot's enabled bit."""
        assert HatchBabyRestProgramSwitch(coordinator, 1).is_on is True

        coordinator.hatch_rest_device.programs[1]["enabled"] = False

        assert HatchBabyRestProgramSwitch(coordinator, 1).is_on is False

    def test_unread_slot_is_unknown_rather_than_off(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a slot nobody has asked about does not look disabled."""
        switch = HatchBabyRestProgramSwitch(coordinator, 9)

        assert switch.is_on is None
        assert switch.extra_state_attributes == {}

    def test_a_slot_known_only_by_name_does_not_throw(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a name arriving before its block leaves the switch usable.

        Names come as their own notification, so a slot can hold nothing but
        a name for a moment -- or for good, if the block never parses.
        """
        coordinator.hatch_rest_device.programs = {1: {"name": "Bed Time"}}
        switch = HatchBabyRestProgramSwitch(coordinator, 1)

        assert switch.is_on is None
        assert switch.extra_state_attributes == {}
        assert switch.name == "Hatch Rest Program 1 (Bed Time)"

    def test_attributes_report_the_rest_of_the_program(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test the program's other fields are exposed."""
        attributes = HatchBabyRestProgramSwitch(coordinator, 1).extra_state_attributes

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
            "toddler_lock": False,
            "flags": 0x40,
            "start_timestamp": PROGRAM["start_timestamp"],
        }

    def test_attributes_report_an_unnamed_sound_by_number(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a sound with no name still shows something."""
        coordinator.hatch_rest_device.programs[1]["sound"] = None
        coordinator.hatch_rest_device.programs[1]["sound_id"] = 8

        attributes = HatchBabyRestProgramSwitch(coordinator, 1).extra_state_attributes

        assert attributes["sound"] == 8

    def test_every_attribute_comes_from_a_real_block(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test a program straight from the parser renders without a KeyError."""
        coordinator.hatch_rest_device.programs = {
            1: _parse_program_block(PROGRAM_BLOCK)
        }
        switch = HatchBabyRestProgramSwitch(coordinator, 1)

        assert switch.is_on is True
        assert switch.extra_state_attributes["start_timestamp"] is not None

    @pytest.mark.asyncio
    async def test_turning_on_enables_the_program(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test enabling goes to the device for this slot."""
        await HatchBabyRestProgramSwitch(coordinator, 10).async_turn_on()

        coordinator.hatch_rest_device.async_set_program_enabled.assert_awaited_once_with(
            10, True
        )

    @pytest.mark.asyncio
    async def test_turning_off_disables_the_program(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test disabling goes to the device for this slot."""
        await HatchBabyRestProgramSwitch(coordinator, 3).async_turn_off()

        coordinator.hatch_rest_device.async_set_program_enabled.assert_awaited_once_with(
            3, False
        )

    def test_is_a_config_entity(self, coordinator: HatchBabyRestUpdateCoordinator):
        """Test programs sit with the device's configuration."""
        switch = HatchBabyRestProgramSwitch(coordinator, 1)

        assert switch.entity_category is EntityCategory.CONFIG
