"""Hatch Rest favorite selection."""

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import FAVORITE_SLOTS
from .coordinator import HatchBabyRestEntity, HatchBabyRestUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# What to call the option that plays nothing in particular.
OPTION_NONE = "None"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hatch Rest favorite select."""
    coordinator = config_entry.runtime_data
    async_add_entities(
        [HatchBabyRestFavoriteSelect(coordinator)], update_before_add=False
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
