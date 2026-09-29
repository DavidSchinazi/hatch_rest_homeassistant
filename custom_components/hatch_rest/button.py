"""Hatch Rest favorite buttons."""

import logging

from homeassistant.components.button import ButtonEntity
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
    """Set up a save button for each of the device's favorites."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        [
            HatchBabyRestSaveFavoriteButton(coordinator, slot)
            for slot in range(1, FAVORITE_SLOTS + 1)
        ],
        update_before_add=False,
    )


class HatchBabyRestSaveFavoriteButton(HatchBabyRestEntity, ButtonEntity):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Saves whatever the device is playing into one of its favorites.

    One button per slot rather than a single button and a slot to aim it
    with: overwriting a favorite cannot be undone, and a button that says
    which slot it writes to is harder to get wrong than one that depends on
    where something else is pointing.
    """

    # These configure the device rather than operate it, which also keeps
    # them out of the way of the controls that get used day to day.
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HatchBabyRestUpdateCoordinator, slot: int) -> None:
        """Initialize the button for one slot."""
        super().__init__(coordinator)
        self._slot = slot
        self._attr_unique_id = f"{coordinator.unique_id}_save_favorite_{slot}"

    @property
    def name(self) -> str | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the name of the entity."""
        if self._hatch_rest_device.name:
            return (
                f"{self._hatch_rest_device.name.title()} Save to Favorite {self._slot}"
            )
        return None

    async def async_press(self) -> None:
        """Save the current state into this button's slot."""
        _LOGGER.debug("button saving current state to favorite %d", self._slot)
        await self._hatch_rest_device.async_save_favorite(self._slot)
