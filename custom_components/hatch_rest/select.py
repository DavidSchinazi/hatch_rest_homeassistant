"""Hatch Rest favorite and sleep timer selection."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any

import voluptuous as vol
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from .const import (
    FAVORITE_SLOTS,
    TIMER_CUSTOM,
    TIMER_MAX_SECONDS,
    TIMER_OFF,
    TIMER_PRESETS,
    PyHatchBabyRestSound,
)
from .coordinator import (
    HatchBabyRestEntity,
    HatchBabyRestTimerEntity,
    HatchBabyRestUpdateCoordinator,
)

_LOGGER = logging.getLogger(__name__)

# What to call the option that plays nothing in particular.
OPTION_NONE = "None"

SERVICE_SET_FAVORITE = "set_favorite"
SERVICE_SAVE_FAVORITE = "save_favorite"
SERVICE_SET_SLEEP_TIMER = "set_sleep_timer"

SLOT_SCHEMA = {
    vol.Required("slot"): vol.All(vol.Coerce(int), vol.Range(min=1, max=FAVORITE_SLOTS))
}

# Everything but the slot is optional: whatever is left out keeps the value
# the slot already holds.
SET_FAVORITE_SCHEMA = {
    **SLOT_SCHEMA,
    vol.Optional("rgb_color"): vol.All(
        vol.ExactSequence((cv.byte, cv.byte, cv.byte)), vol.Coerce(tuple)
    ),
    vol.Optional("brightness"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
    vol.Optional("sound"): vol.In([sound.name for sound in PyHatchBabyRestSound]),
    vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
    vol.Optional("enabled"): cv.boolean,
}

# Zero cancels. The ceiling is what SD's four hex digits of seconds hold.
SET_SLEEP_TIMER_SCHEMA = {
    vol.Required("duration"): vol.All(
        cv.time_period,
        vol.Range(
            min=timedelta(0),
            max=timedelta(seconds=TIMER_MAX_SECONDS),
            msg="duration must be between 0 and 18:12:15",
        ),
    )
}

# How close a running timer's end has to be to the one a preset set, for the
# control to go on showing that preset.
TIMER_PRESET_TOLERANCE_SECONDS = 5


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest favorite select."""
    coordinator = config_entry.runtime_data

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_FAVORITE,
        SET_FAVORITE_SCHEMA,
        "async_set_favorite",
    )
    platform.async_register_entity_service(
        SERVICE_SAVE_FAVORITE,
        SLOT_SCHEMA,
        "async_save_favorite",
    )
    platform.async_register_entity_service(
        SERVICE_SET_SLEEP_TIMER,
        SET_SLEEP_TIMER_SCHEMA,
        "async_set_sleep_timer",
    )

    async_add_entities(
        [
            HatchBabyRestFavoriteSelect(coordinator),
            HatchBabyRestTimerSelect(coordinator),
        ],
        update_before_add=False,
    )


class HatchBabyRestFavoriteSelect(HatchBabyRestEntity, SelectEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """The favorite the Hatch Rest is currently playing."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        # HatchBabyRestEntity gives every entity the bare coordinator id, which
        # only works while no two share a platform. Suffixing here stops the
        # next entity on this one from colliding.
        self._attr_unique_id = f"{coordinator.unique_id}_favorite"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Favorite"
        return None

    def _slot_labels(self) -> dict[int, str]:
        """Return the label to show for each slot.

        The device names its favorites, but a slot may be unnamed, and two may
        share a name. Either would make an option ambiguous, so a name is only
        used when it is present and unique.
        """
        favorites = self._hatch_rest_device.favorites
        names = [
            favorites.get(slot, {}).get("name") for slot in range(1, FAVORITE_SLOTS + 1)
        ]

        labels = {}
        for slot, name in enumerate(names, start=1):
            if name and names.count(name) == 1:
                labels[slot] = name
            else:
                labels[slot] = f"Favorite {slot}"
        return labels

    @property
    def options(self) -> list[str]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the favorites that can be selected."""
        return [OPTION_NONE, *self._slot_labels().values()]

    @property
    def current_option(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the favorite currently playing."""
        slot = self.coordinator.data.get("active_favorite")
        _LOGGER.debug("select current favorite = %s", slot)
        if slot is None:
            return OPTION_NONE
        return self._slot_labels().get(slot)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return what each favorite holds.

        Carried here rather than as an entity each, so that reading the
        favorites does not cost six more entities per device.
        """
        favorites = self._hatch_rest_device.favorites
        labels = self._slot_labels()

        return {
            "favorites": {
                slot: {
                    "name": labels[slot],
                    "color": favorite.get("color"),
                    "brightness": favorite.get("brightness"),
                    "sound": (
                        favorite["sound"].name
                        if favorite.get("sound") is not None
                        else favorite.get("sound_id")
                    ),
                    "volume": favorite.get("volume"),
                    "enabled": favorite.get("enabled"),
                }
                for slot, favorite in sorted(favorites.items())
            }
        }

    async def async_select_option(self, option: str) -> None:
        """Play the chosen favorite."""
        if option == OPTION_NONE:
            _LOGGER.debug("select deselecting favorite")
            await self._hatch_rest_device.set_active_favorite(None)
            return

        for slot, label in self._slot_labels().items():
            if label == option:
                _LOGGER.debug("select playing favorite %d (%s)", slot, label)
                await self._hatch_rest_device.set_active_favorite(slot)
                return

        raise ValueError(f"{option} is not one of {self.options}")

    async def async_set_favorite(
        self,
        slot: int,
        rgb_color: tuple[int, int, int] | None = None,
        brightness: int | None = None,
        sound: str | None = None,
        volume: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Write to one of the device's stored favorites."""
        _LOGGER.debug("select setting favorite %d", slot)
        await self._hatch_rest_device.async_set_favorite(
            slot,
            color=rgb_color,
            brightness=brightness,
            sound=PyHatchBabyRestSound[sound] if sound else None,
            volume=volume,
            enabled=enabled,
        )

    async def async_save_favorite(self, slot: int) -> None:
        """Save what the device is playing now into one of its favorites."""
        _LOGGER.debug("select saving current state to favorite %d", slot)
        await self._hatch_rest_device.async_save_favorite(slot)


@dataclass
class TimerPresetData(ExtraStoredData):
    """The preset that started the running timer, and when that timer ends.

    Wall clock, since the monotonic clock the countdown runs on starts over
    with Home Assistant.
    """

    preset: str
    ends_at: datetime

    def as_dict(self) -> dict[str, Any]:
        """Return what to store."""
        return {"preset": self.preset, "ends_at": self.ends_at.isoformat()}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> "TimerPresetData | None":
        """Return what was stored, or None if it cannot be read."""
        try:
            ends_at = dt_util.parse_datetime(restored["ends_at"])
            preset = restored["preset"]
        except (KeyError, TypeError):
            return None
        if ends_at is None or preset not in TIMER_PRESETS:
            return None
        return cls(preset, ends_at)


class HatchBabyRestTimerSelect(HatchBabyRestTimerEntity, SelectEntity, RestoreEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """The sleep timer: pick a duration to start one, or Off to cancel.

    Shows the preset that started the running timer, Off when there is none,
    and Custom for one started any other way -- from the app, or with an
    exact duration through the set_sleep_timer action. The time left is its
    own sensor.

    The preset outlives a restart: it is stored with the time its timer ends,
    and shown again only if the timer the device reports still ends then.
    """

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the timer control."""
        super().__init__(coordinator)
        # The id the number control had. A different platform, so no clash.
        self._attr_unique_id = f"{coordinator.unique_id}_timer"
        # The preset last chosen here, and when the timer it started ends.
        # Held against the device's own countdown, so that a timer the app
        # starts afterwards shows as Custom rather than as this preset.
        self._preset: str | None = None
        self._preset_expires_at: float | None = None

    async def async_added_to_hass(self) -> None:
        """Start ticking, and take back the preset from before a restart."""
        await super().async_added_to_hass()
        if (restored := await self.async_get_last_extra_data()) is None:
            return
        if (data := TimerPresetData.from_dict(restored.as_dict())) is None:
            return
        self._preset = data.preset
        self._preset_expires_at = (
            monotonic() + (data.ends_at - dt_util.utcnow()).total_seconds()
        )

    @property
    def extra_restore_state_data(self) -> TimerPresetData | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the preset to take back after a restart, if one is running."""
        if self.current_option not in TIMER_PRESETS or self._preset_expires_at is None:
            return None
        return TimerPresetData(
            self.current_option,
            dt_util.utcnow() + timedelta(seconds=self._preset_expires_at - monotonic()),
        )

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Sleep Timer"
        return None

    @property
    def current_option(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return Off, the preset that started the timer, or Custom."""
        if self._hatch_rest_device.timer_remaining is None:
            return TIMER_OFF

        expires_at = self._hatch_rest_device.timer_expires_at
        if (
            self._preset is not None
            and self._preset_expires_at is not None
            and expires_at is not None
            and abs(expires_at - self._preset_expires_at)
            <= TIMER_PRESET_TOLERANCE_SECONDS
        ):
            return self._preset
        return TIMER_CUSTOM

    @property
    def options(self) -> list[str]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return Off and the presets, and Custom while one is running."""
        options = [TIMER_OFF, *TIMER_PRESETS]
        if self.current_option == TIMER_CUSTOM:
            options.append(TIMER_CUSTOM)
        return options

    def _timer_value(self) -> str:
        """Return the option this control shows."""
        return self.current_option

    async def async_select_option(self, option: str) -> None:
        """Start the chosen timer, or cancel with Off."""
        if option == TIMER_CUSTOM:
            # Only ever shown, never a thing to pick.
            return
        seconds = 0 if option == TIMER_OFF else TIMER_PRESETS[option]
        _LOGGER.debug("select setting sleep timer to %s", option)
        await self._async_set_timer(seconds)

    async def async_set_sleep_timer(self, duration: timedelta) -> None:
        """Start a sleep timer of an exact duration, or cancel it with zero."""
        seconds = int(duration.total_seconds())
        _LOGGER.debug("select setting sleep timer to %d seconds", seconds)
        await self._async_set_timer(seconds)

    async def _async_set_timer(self, seconds: int) -> None:
        """Set the timer, remembering which preset it was if it was one.

        Remembered before it is sent rather than after, with when it should
        end: the read back publishes, and the control would otherwise show
        Custom for a moment on its way to the preset.
        """
        presets = {value: label for label, value in TIMER_PRESETS.items()}
        self._preset = presets.get(seconds)
        self._preset_expires_at = monotonic() + seconds

        await self._hatch_rest_device.async_set_timer(seconds)
        self.async_write_ha_state()
