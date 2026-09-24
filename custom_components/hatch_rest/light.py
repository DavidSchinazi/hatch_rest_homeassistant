"""Hatch Rest light."""

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    LightEntity,
)
from homeassistant.components.light.const import ColorMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .const import DEFAULT_ON_BRIGHTNESS
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class HatchBabyRestLightExtraData(ExtraStoredData):
    """The brightness to come back to, kept across restarts."""

    last_on_brightness: int

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the extra data."""
        return {"last_on_brightness": self.last_on_brightness}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest light."""
    coordinator = config_entry.runtime_data
    # only need to update_before_add on one entity -- switch is "master" entity
    async_add_entities([HatchBabyRestLight(coordinator)], update_before_add=False)


class HatchBabyRestLight(HatchBabyRestEntity, RestoreEntity, LightEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Hatch Rest light entity."""

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._last_on_brightness = (
            coordinator.data.get("brightness") or DEFAULT_ON_BRIGHTNESS
            if coordinator.data
            else DEFAULT_ON_BRIGHTNESS
        )

    @property
    def extra_restore_state_data(self) -> HatchBabyRestLightExtraData:
        """Return the brightness to restore after a restart."""
        return HatchBabyRestLightExtraData(self._last_on_brightness)

    async def async_added_to_hass(self) -> None:
        """Restore the brightness remembered before the restart."""
        await super().async_added_to_hass()

        if (extra_data := await self.async_get_last_extra_data()) and (
            brightness := extra_data.as_dict().get("last_on_brightness")
        ):
            self._last_on_brightness = brightness
            _LOGGER.debug("light restored last on brightness = %s", brightness)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember the brightness to come back to."""
        if brightness := self.coordinator.data.get("brightness"):
            self._last_on_brightness = brightness
        super()._handle_coordinator_update()

    @property
    def brightness(self) -> int | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the brightness of the light."""
        _LOGGER.debug("light brightness = %s", self.coordinator.data.get("brightness"))
        return self.coordinator.data.get("brightness")

    @property
    def color_mode(self) -> ColorMode:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return color mode."""
        return ColorMode.RGB

    @property
    def is_on(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return if the light is on."""
        # if power is off, then it's off
        if self.coordinator.data.get("power") is False:
            return False
        brightness = self.coordinator.data.get("brightness")
        if brightness:
            _LOGGER.debug("light is_on = %s", brightness > 0)
            # if brightness is greater than 0, then it's on
            return brightness > 0
        return False

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Light"
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the RGB color of the light."""
        _LOGGER.debug("light rgb_color = %s", self.coordinator.data.get("color"))
        return self.coordinator.data.get("color")

    @property
    def supported_color_modes(self) -> set[ColorMode]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return supported color modes."""
        return {ColorMode.RGB}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the light on."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgb = kwargs.get(ATTR_RGB_COLOR)

        if not self._hatch_rest_device.power:
            _LOGGER.debug("light _hatch_rest_device power not on -- turning on")
            await self._hatch_rest_device.turn_power_on()

        if brightness is None and not self.coordinator.data.get("brightness"):
            # Turning the light off writes a brightness of zero, and the
            # device does not remember what it was, so turning it back on
            # without one has to say. Otherwise nothing would be sent at all
            # and the light would stay dark.
            brightness = self._last_on_brightness
            _LOGGER.debug("light restoring brightness = %s", brightness)

        # The device takes color and brightness in one command, so setting
        # both is a single round trip.
        if brightness and rgb:
            _LOGGER.debug("light setting RGB = %s and brightness = %s", rgb, brightness)
            await self._hatch_rest_device.set_color_and_brightness(*rgb, brightness)
        elif brightness:
            _LOGGER.debug("light setting brightness = %s", brightness)
            await self._hatch_rest_device.set_brightness(brightness)
        elif rgb:
            _LOGGER.debug("light setting RGB = %s", rgb)
            await self._hatch_rest_device.set_color(*rgb)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Set the light off."""
        await self._hatch_rest_device.set_brightness(0)
