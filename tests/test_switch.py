"""Tests for Hatch Rest switch entity."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import EntityCategory

from custom_components.hatch_rest.const import FAVORITE_SLOTS
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.switch import (
    HatchBabyRestFavoriteEnabledSwitch,
    HatchBabyRestSwitch,
    async_setup_entry,
)


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

        assert len(added) == FAVORITE_SLOTS + 1
        assert isinstance(added[0], HatchBabyRestSwitch)
        assert [switch._slot for switch in added[1:]] == [1, 2, 3, 4, 5, 6]

    def test_unique_ids_do_not_collide_with_the_power_switch(
        self, coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test every switch on this platform is distinguishable.

        They all inherit the bare coordinator id, and the power switch keeps
        it, so without a suffix Home Assistant would keep one of the seven
        and drop the rest.
        """
        ids = [HatchBabyRestSwitch(coordinator).unique_id] + [
            HatchBabyRestFavoriteEnabledSwitch(coordinator, slot).unique_id
            for slot in range(1, FAVORITE_SLOTS + 1)
        ]

        assert len(set(ids)) == FAVORITE_SLOTS + 1

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
