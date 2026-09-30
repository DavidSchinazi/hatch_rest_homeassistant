"""Tests for Hatch Rest favorite and sleep timer select entities."""

import pathlib
from datetime import timedelta
from time import monotonic
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol

from custom_components.hatch_rest.const import (
    TIMER_CUSTOM,
    TIMER_OFF,
    TIMER_PRESETS,
    PyHatchBabyRestSound,
)
from custom_components.hatch_rest.coordinator import HatchBabyRestUpdateCoordinator
from custom_components.hatch_rest.select import (
    OPTION_NONE,
    SET_SLEEP_TIMER_SCHEMA,
    HatchBabyRestFavoriteSelect,
    HatchBabyRestTimerSelect,
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


class TestSetFavoriteService:
    """Tests for the set_favorite entity service."""

    @pytest.fixture
    def select_entity(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestFavoriteSelect:
        """Create the select entity over a device that accepts writes."""
        mock_coordinator.hatch_rest_device.favorites = {}
        mock_coordinator.hatch_rest_device.async_set_favorite = AsyncMock()
        return HatchBabyRestFavoriteSelect(mock_coordinator)

    @pytest.mark.asyncio
    async def test_passes_the_fields_through(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test the service maps onto the device call."""
        await select_entity.async_set_favorite(
            2,
            rgb_color=(1, 2, 3),
            brightness=10,
            sound="ocean",
            volume=20,
            enabled=True,
        )

        select_entity._hatch_rest_device.async_set_favorite.assert_awaited_once_with(
            2,
            color=(1, 2, 3),
            brightness=10,
            sound=PyHatchBabyRestSound.ocean,
            volume=20,
            enabled=True,
        )

    @pytest.mark.asyncio
    async def test_omitted_fields_stay_omitted(
        self, select_entity: HatchBabyRestFavoriteSelect
    ):
        """Test what the caller left out is passed on as left out.

        The device layer fills the gaps from the slot's current contents, so
        substituting a default here would defeat that.
        """
        await select_entity.async_set_favorite(3, volume=50)

        select_entity._hatch_rest_device.async_set_favorite.assert_awaited_once_with(
            3, color=None, brightness=None, sound=None, volume=50, enabled=None
        )


class TestServiceRegistration:
    """Tests that the services are actually wired up on platform setup."""

    @pytest.mark.asyncio
    async def test_setup_registers_the_services(
        self, hass, mock_coordinator: HatchBabyRestUpdateCoordinator
    ):
        """Test setting up the platform registers every service.

        The registration is easy to get wrong in a way nothing else catches:
        the service simply never appears, and the entity looks fine.
        """
        from unittest.mock import MagicMock, patch

        from custom_components.hatch_rest.select import (
            SERVICE_SAVE_FAVORITE,
            SERVICE_SET_FAVORITE,
            SERVICE_SET_SLEEP_TIMER,
            async_setup_entry,
        )

        config_entry = MagicMock()
        config_entry.runtime_data = mock_coordinator
        platform = MagicMock()

        with patch(
            "custom_components.hatch_rest.select.entity_platform"
            ".async_get_current_platform",
            return_value=platform,
        ):
            await async_setup_entry(hass, config_entry, MagicMock())

        registered = {
            call.args[0]: call.args
            for call in platform.async_register_entity_service.call_args_list
        }

        assert set(registered) == {
            SERVICE_SET_FAVORITE,
            SERVICE_SAVE_FAVORITE,
            SERVICE_SET_SLEEP_TIMER,
        }

        for name, (_, schema, func) in registered.items():
            # The method each service dispatches to has to exist on the
            # entity it is meant for.
            entity = (
                HatchBabyRestTimerSelect
                if name == SERVICE_SET_SLEEP_TIMER
                else HatchBabyRestFavoriteSelect
            )
            assert hasattr(entity, func), name
            # And every favorite service takes the slot it is meant to act on.
            if name != SERVICE_SET_SLEEP_TIMER:
                assert "slot" in {str(key) for key in schema}, name

    def test_every_service_is_documented(self):
        """Test each registered service has an entry in services.yaml.

        A service missing from there still works, but shows up in the UI
        under its raw name with no fields, which looks broken.
        """
        import yaml

        from custom_components.hatch_rest.select import (
            SERVICE_SAVE_FAVORITE,
            SERVICE_SET_FAVORITE,
            SERVICE_SET_SLEEP_TIMER,
        )

        documented = yaml.safe_load(
            pathlib.Path("custom_components/hatch_rest/services.yaml").read_text()
        )

        # send_command is registered by the integration rather than this
        # platform, and addresses a device rather than an entity.
        assert set(documented) - {"send_command"} == {
            SERVICE_SET_FAVORITE,
            SERVICE_SAVE_FAVORITE,
            SERVICE_SET_SLEEP_TIMER,
        }
        for name in (
            SERVICE_SET_FAVORITE,
            SERVICE_SAVE_FAVORITE,
            SERVICE_SET_SLEEP_TIMER,
        ):
            assert documented[name]["target"]["entity"]["domain"] == "select", name
        for name in (SERVICE_SET_FAVORITE, SERVICE_SAVE_FAVORITE):
            assert documented[name]["fields"]["slot"]["required"] is True, name
        timer = documented[SERVICE_SET_SLEEP_TIMER]["fields"]["duration"]
        assert timer["required"] is True
        assert "duration" in timer["selector"]

    def test_service_schema_offers_every_sound(self):
        """Test the documented sound list matches the enum.

        A name in services.yaml that is not in the enum would raise a
        KeyError only once someone picked it.
        """
        import yaml

        documented = yaml.safe_load(
            pathlib.Path("custom_components/hatch_rest/services.yaml").read_text()
        )["set_favorite"]["fields"]["sound"]["selector"]["select"]["options"]

        assert documented == [sound.name for sound in PyHatchBabyRestSound]


class TestHatchBabyRestTimerSelect:
    """Tests for HatchBabyRestTimerSelect."""

    @pytest.fixture
    def timer(
        self, mock_coordinator: HatchBabyRestUpdateCoordinator
    ) -> HatchBabyRestTimerSelect:
        """Return the timer control over a device that keeps what SD sets."""
        device = mock_coordinator.hatch_rest_device
        device.timer_remaining = None
        device.timer_expires_at = None

        async def set_timer(seconds):
            if seconds:
                device.timer_remaining = seconds
                device.timer_expires_at = monotonic() + seconds
            else:
                device.timer_remaining = None
                device.timer_expires_at = None

        device.async_set_timer = AsyncMock(side_effect=set_timer)
        entity = HatchBabyRestTimerSelect(mock_coordinator)
        entity.hass = mock_coordinator.hass
        return entity

    def test_is_off_with_no_timer(self, timer: HatchBabyRestTimerSelect):
        """Test an idle device shows Off, and offers no Custom."""
        assert timer.current_option == TIMER_OFF
        assert timer.options == [TIMER_OFF, *TIMER_PRESETS]
        assert timer.name == "Hatch Rest Sleep Timer"
        assert timer.unique_id == "aabbccddeeff_timer"

    @pytest.mark.asyncio
    async def test_a_preset_starts_that_timer_and_shows_it(
        self, timer: HatchBabyRestTimerSelect
    ):
        """Test picking a preset sends its seconds and then shows it."""
        with patch.object(timer, "async_write_ha_state"):
            await timer.async_select_option("1.5 hours")

        timer._hatch_rest_device.async_set_timer.assert_awaited_once_with(5400)
        assert timer.current_option == "1.5 hours"

    @pytest.mark.asyncio
    async def test_off_cancels(self, timer: HatchBabyRestTimerSelect):
        """Test Off sends zero."""
        with patch.object(timer, "async_write_ha_state"):
            await timer.async_select_option("1 hour")
            await timer.async_select_option(TIMER_OFF)

        timer._hatch_rest_device.async_set_timer.assert_awaited_with(0)
        assert timer.current_option == TIMER_OFF

    def test_a_timer_from_elsewhere_is_custom(self, timer: HatchBabyRestTimerSelect):
        """Test a timer the app started shows as Custom, and can be seen."""
        device = timer._hatch_rest_device
        device.timer_remaining = 21076
        device.timer_expires_at = monotonic() + 21076

        assert timer.current_option == TIMER_CUSTOM
        assert TIMER_CUSTOM in timer.options

    @pytest.mark.asyncio
    async def test_the_app_replacing_a_preset_shows_custom(
        self, timer: HatchBabyRestTimerSelect
    ):
        """Test a preset stops showing once a different timer replaces it."""
        with patch.object(timer, "async_write_ha_state"):
            await timer.async_select_option("1 hour")

        device = timer._hatch_rest_device
        device.timer_remaining = 60
        device.timer_expires_at = monotonic() + 60

        assert timer.current_option == TIMER_CUSTOM

    @pytest.mark.asyncio
    async def test_a_preset_never_shows_as_custom_on_the_way(
        self, timer: HatchBabyRestTimerSelect
    ):
        """Test the preset is already shown while the timer is read back.

        The read back publishes, and the control showed Custom for a moment
        when the preset was only remembered afterwards.
        """
        device = timer._hatch_rest_device
        set_timer = device.async_set_timer.side_effect
        seen = []

        async def and_look(seconds):
            await set_timer(seconds)
            seen.append(timer.current_option)

        device.async_set_timer.side_effect = and_look
        with patch.object(timer, "async_write_ha_state"):
            await timer.async_select_option("15 minutes")

        assert seen == ["15 minutes"]

    @pytest.mark.asyncio
    async def test_a_preset_outlives_a_restart(self, timer: HatchBabyRestTimerSelect):
        """Test the preset is stored and taken back if its timer still runs."""
        with patch.object(timer, "async_write_ha_state"):
            await timer.async_select_option("15 minutes")
        stored = timer.extra_restore_state_data
        assert stored is not None
        assert stored.preset == "15 minutes"

        # Home Assistant restarts: a new entity, and the device still counting
        # down the same timer.
        restarted = HatchBabyRestTimerSelect(timer.coordinator)
        restarted.hass = timer.hass
        with (
            patch.object(
                restarted,
                "async_get_last_extra_data",
                AsyncMock(return_value=stored),
            ),
            patch(
                "custom_components.hatch_rest.coordinator.CoordinatorEntity"
                ".async_added_to_hass",
                AsyncMock(),
            ),
            patch("custom_components.hatch_rest.coordinator.async_track_time_interval"),
        ):
            await restarted.async_added_to_hass()

        assert restarted.current_option == "15 minutes"

    @pytest.mark.asyncio
    async def test_a_restored_preset_yields_to_a_different_timer(
        self, timer: HatchBabyRestTimerSelect
    ):
        """Test a timer the app started while Home Assistant was down is Custom."""
        with patch.object(timer, "async_write_ha_state"):
            await timer.async_select_option("15 minutes")
        stored = timer.extra_restore_state_data

        device = timer._hatch_rest_device
        device.timer_remaining = 3600
        device.timer_expires_at = monotonic() + 3600

        restarted = HatchBabyRestTimerSelect(timer.coordinator)
        restarted.hass = timer.hass
        with (
            patch.object(
                restarted,
                "async_get_last_extra_data",
                AsyncMock(return_value=stored),
            ),
            patch(
                "custom_components.hatch_rest.coordinator.CoordinatorEntity"
                ".async_added_to_hass",
                AsyncMock(),
            ),
            patch("custom_components.hatch_rest.coordinator.async_track_time_interval"),
        ):
            await restarted.async_added_to_hass()

        assert restarted.current_option == TIMER_CUSTOM

    def test_nothing_is_stored_without_a_preset(self, timer: HatchBabyRestTimerSelect):
        """Test Off and Custom leave nothing to restore."""
        assert timer.extra_restore_state_data is None

        device = timer._hatch_rest_device
        device.timer_remaining = 21076
        device.timer_expires_at = monotonic() + 21076
        assert timer.extra_restore_state_data is None

    @pytest.mark.asyncio
    async def test_choosing_custom_does_nothing(self, timer: HatchBabyRestTimerSelect):
        """Test Custom is only ever shown, never sent."""
        await timer.async_select_option(TIMER_CUSTOM)

        timer._hatch_rest_device.async_set_timer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_action_sets_an_exact_duration(
        self, timer: HatchBabyRestTimerSelect
    ):
        """Test the action sends any duration, and a preset one shows as such."""
        with patch.object(timer, "async_write_ha_state"):
            await timer.async_set_sleep_timer(timedelta(hours=1, minutes=5, seconds=7))
            assert timer.current_option == TIMER_CUSTOM

            await timer.async_set_sleep_timer(timedelta(minutes=30))
            assert timer.current_option == "30 minutes"

        timer._hatch_rest_device.async_set_timer.assert_any_await(3907)

    def test_the_action_takes_what_the_duration_picker_sends(self):
        """Test the picker's hours, minutes and seconds are accepted."""
        schema = vol.Schema(SET_SLEEP_TIMER_SCHEMA)

        assert schema({"duration": {"hours": 2, "minutes": 3, "seconds": 4}}) == {
            "duration": timedelta(hours=2, minutes=3, seconds=4)
        }
        assert schema({"duration": "0:00:00"})["duration"] == timedelta(0)

    def test_the_action_refuses_what_sd_cannot_hold(self):
        """Test a duration past four hex digits of seconds is refused up front."""
        schema = vol.Schema(SET_SLEEP_TIMER_SCHEMA)

        with pytest.raises(vol.Invalid):
            schema({"duration": {"hours": 18, "minutes": 13}})
