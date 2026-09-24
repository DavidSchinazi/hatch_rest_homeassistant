"""Hatch Rest media player."""

import logging

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PyHatchBabyRestSound
from .const import DEFAULT_SOUND
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest media player."""
    coordinator = config_entry.runtime_data
    # only need to update_before_add on one entity -- switch is "master" entity
    async_add_entities([HatchBabyRestMediaPlayer(coordinator)], update_before_add=False)


class HatchBabyRestMediaPlayer(HatchBabyRestEntity, MediaPlayerEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Hatch Rest media player entity."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)

        self._previous_sound: PyHatchBabyRestSound | None = (
            coordinator.data.get("sound") if coordinator.data else None
        ) or None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember the sound to resume to.

        Tracking every update, rather than only a pause made through Home
        Assistant, means a sound started on the device itself is remembered
        too.
        """
        if sound := self.coordinator.data.get("sound"):
            self._previous_sound = sound
        super()._handle_coordinator_update()

    @property
    def device_class(self) -> MediaPlayerDeviceClass | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the device class of the media player."""
        return MediaPlayerDeviceClass.SPEAKER

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Media Player"
        return None

    @property
    def source(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the current source of the media player."""
        if self.coordinator.data.get("sound"):
            _LOGGER.debug(
                "media_player source = %d (%s)",
                self.coordinator.data.get("sound"),
                PyHatchBabyRestSound(self.coordinator.data.get("sound")).name,
            )
        else:
            _LOGGER.debug("media_player source = None")
        sound = self.coordinator.data.get("sound")
        if sound:
            return sound.name.capitalize()
        return None

    @property
    def source_list(self) -> list[str] | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return a list of available sources."""
        return [sound.name.capitalize() for sound in PyHatchBabyRestSound]

    @property
    def state(self) -> MediaPlayerState | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the current state of the media player."""
        # if power is off, then it's off
        if self.coordinator.data.get("power") is False:
            return MediaPlayerState.OFF

        if self.coordinator.data.get("sound") == PyHatchBabyRestSound.none:
            return MediaPlayerState.PAUSED

        return MediaPlayerState.PLAYING

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return a set of supported features."""
        return (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )

    @property
    def volume_level(self) -> float | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the volume level of the media player."""
        _LOGGER.debug(
            "media_player volume_level = %s", self.coordinator.data.get("volume")
        )
        volume = self.coordinator.data.get("volume")
        if volume:
            return float(volume / 255)
        return None

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the volume level of the media player."""
        _LOGGER.debug("media_player setting volume_level = %s", int(255 * volume))
        await self._hatch_rest_device.set_volume(int(255 * volume))

    async def async_select_source(self, source: str) -> None:
        """Select a source from the list of available sources."""
        source_number = PyHatchBabyRestSound[source.lower()]
        self._previous_sound = PyHatchBabyRestSound(source_number)
        _LOGGER.debug(
            "media_player setting source = %d (%s) ",
            source_number,
            PyHatchBabyRestSound(source_number).name,
        )
        await self._hatch_rest_device.set_sound(source_number)

    async def async_media_pause(self) -> None:
        """Pause the media player."""
        self._previous_sound = self._hatch_rest_device.sound
        _LOGGER.debug(
            "media_player setting source = %d (%s)",
            PyHatchBabyRestSound.none,
            PyHatchBabyRestSound.none.name,
        )
        await self._hatch_rest_device.set_sound(PyHatchBabyRestSound.none)

    async def async_media_play(self) -> None:
        """Play the media player."""
        if not self._hatch_rest_device.power:
            _LOGGER.debug("media_player _hatch_rest_device power not on -- turning on")
            await self._hatch_rest_device.turn_power_on()
        # Nothing is known to resume to after a restart, or if the device was
        # paused somewhere other than here. Play something rather than
        # silently doing nothing.
        previous_sound = self._previous_sound or DEFAULT_SOUND
        _LOGGER.debug(
            "media_player setting source = %d (%s)",
            previous_sound,
            PyHatchBabyRestSound(previous_sound).name,
        )
        await self._hatch_rest_device.set_sound(previous_sound)
