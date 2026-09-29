"""Hatch Rest switch."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import FAVORITE_SLOTS
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest switch."""
    coordinator = config_entry.runtime_data
    # The coordinator is already seeded from an advertisement, so updating
    # before add would only cost a connection: CoordinatorEntity.async_update
    # calls async_request_refresh, which reads state over GATT.
    async_add_entities(
        [
            HatchBabyRestSwitch(coordinator),
            *(
                HatchBabyRestFavoriteEnabledSwitch(coordinator, slot)
                for slot in range(1, FAVORITE_SLOTS + 1)
            ),
        ],
        update_before_add=False,
    )


class HatchBabyRestSwitch(HatchBabyRestEntity, SwitchEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Hatch Rest switch entity."""

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether the switch is on or not."""
        _LOGGER.debug("switch is_on = %s", self.coordinator.data.get("power"))
        return self.coordinator.data.get("power")

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return f"{self._hatch_rest_device.name.title()} Switch"
        return None

    async def async_turn_on(self, **_):
        """Turn on the Hatch Rest device."""
        if not self.is_on:
            _LOGGER.debug("switch setting on")
            await self._hatch_rest_device.turn_power_on()

    async def async_turn_off(self, **_):
        """Turn off the Hatch Rest device."""
        if self.is_on:
            _LOGGER.debug("switch setting off")
            await self._hatch_rest_device.turn_power_off()


class HatchBabyRestFavoriteEnabledSwitch(HatchBabyRestEntity, SwitchEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Whether one favorite is offered when cycling them on the device."""

    # These configure the device rather than operate it.
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator, slot: int) -> None:
        """Initialize the switch for one slot."""
        super().__init__(coordinator)
        self._slot = slot
        # HatchBabyRestEntity hands every entity the bare coordinator id, and
        # the power switch above already has it. Suffixing is what keeps
        # these six from colliding with it and with each other -- the power
        # switch keeps the unsuffixed id so its history survives.
        self._attr_unique_id = f"{coordinator.unique_id}_favorite_{slot}_enabled"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return (
                f"{self._hatch_rest_device.name.title()} Favorite {self._slot} Enabled"
            )
        return None

    @property
    def is_on(self) -> bool | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return whether this favorite is offered on the device.

        None until the slot has been read, which is not the same as off: a
        favorite whose state is unknown should not look disabled.
        """
        favorite = self._hatch_rest_device.favorites.get(self._slot)
        if favorite is None:
            return None
        return favorite.get("enabled")

    async def async_turn_on(self, **_):
        """Start offering this favorite on the device."""
        _LOGGER.debug("switch enabling favorite %d", self._slot)
        await self._hatch_rest_device.async_set_favorite(self._slot, enabled=True)

    async def async_turn_off(self, **_):
        """Stop offering this favorite on the device."""
        _LOGGER.debug("switch disabling favorite %d", self._slot)
        await self._hatch_rest_device.async_set_favorite(self._slot, enabled=False)
