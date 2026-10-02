"""Tests for the favorite and program editor behind the integration's Configure."""

from datetime import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hatch_rest.api import (
    HatchRestConnectionError,
    _parse_favorite_block,
    _parse_program_block,
)
from custom_components.hatch_rest.config_flow import HatchBabyRestOptionsFlow
from custom_components.hatch_rest.const import DOMAIN, PyHatchBabyRestSound

# TestA as the Extra holds it: 21:15 for an hour on Mon, Wed and Fri, red at
# 0x40, ocean at 0x30, Toddler Lock on, enabled.
TEST_A = {
    **_parse_program_block(bytes.fromhex("01d47bbd6a0530100e0000ff01400000ff002adf")),
    "name": "TestA",
    "status": 0x07,
    "enabled": True,
}
EMPTY = {**_parse_program_block(bytes.fromhex("01" + "00" * 19)), "enabled": False}
# Favorite 2 as all four devices hold it: (253, 209, 45) at 0x7f, ocean at
# 0x54, offered on the touch ring.
FAVORITE_2 = _parse_favorite_block(bytes.fromhex("0105540000000000007f2dd1fdc003"))


@pytest.fixture
def device() -> MagicMock:
    """Return a stand-in device holding favorite 2, TestA in slot 5 and nothing in 7."""
    device = MagicMock()
    device.favorites = {2: dict(FAVORITE_2)}
    device.programs = {5: dict(TEST_A), 7: dict(EMPTY)}
    device.async_set_favorite = AsyncMock()
    device.async_set_program = AsyncMock()
    return device


@pytest.fixture
def entry(hass: HomeAssistant, device: MagicMock) -> MockConfigEntry:
    """Return a loaded entry for the Extra."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="c04965c88d46",
        data={CONF_ADDRESS: "C0:49:65:C8:8D:46"},
        options={"kept": True},
        state=ConfigEntryState.LOADED,
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MagicMock(hatch_rest_device=device)
    return entry


def _suggested(result, field: str):
    """Return what the form shows a field filled in with."""
    for key in result["data_schema"].schema:
        if str(key) == field:
            return key.description["suggested_value"]
    raise KeyError(field)


def _flow(hass: HomeAssistant, entry: MockConfigEntry) -> HatchBabyRestOptionsFlow:
    """Return the editor for an entry, driven directly.

    Through the flow manager, Home Assistant would set up the integration's
    dependencies first, and Bluetooth cannot start in these tests.
    """
    flow = HatchBabyRestOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    flow.flow_id = "test"
    return flow


async def _open(hass: HomeAssistant, entry: MockConfigEntry, slot: str):
    """Open the editor and choose a program."""
    flow = _flow(hass, entry)
    await flow.async_step_init()
    await flow.async_step_programs()
    return flow, await flow.async_step_programs({"program": slot})


async def _open_favorite(hass: HomeAssistant, entry: MockConfigEntry, slot: str):
    """Open the editor and choose a favorite."""
    flow = _flow(hass, entry)
    await flow.async_step_init()
    await flow.async_step_favorites()
    return flow, await flow.async_step_favorites({"favorite": slot})


@pytest.mark.asyncio
async def test_asks_favorite_or_program_first(
    hass: HomeAssistant, entry: MockConfigEntry
):
    """Test the first step is a menu choosing what kind of slot to edit."""
    result = await _flow(hass, entry).async_step_init()

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert result["menu_options"] == ["favorites", "programs"]


@pytest.mark.asyncio
async def test_refuses_an_entry_that_is_not_loaded(
    hass: HomeAssistant, entry: MockConfigEntry
):
    """Test the editor will not open while the device is not set up."""
    entry.mock_state(hass, ConfigEntryState.NOT_LOADED)

    result = await _flow(hass, entry).async_step_init()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


@pytest.mark.asyncio
async def test_lists_the_programs(hass: HomeAssistant, entry: MockConfigEntry):
    """Test the program step offers each program read, by name and time."""
    result = await _flow(hass, entry).async_step_programs()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "programs"
    selector = result["data_schema"].schema["program"]
    assert [option["label"] for option in selector.config["options"]] == [
        "5: TestA (21:15, on)",
        "7: empty",
    ]


@pytest.mark.asyncio
async def test_the_form_starts_from_what_the_device_holds(
    hass: HomeAssistant, entry: MockConfigEntry
):
    """Test every field is filled in with the program's current setting."""
    _, result = await _open(hass, entry, "5")

    assert result["step_id"] == "program"
    assert result["description_placeholders"] == {"slot": "5"}
    assert _suggested(result, "enabled") is True
    assert _suggested(result, "name") == "TestA"
    assert _suggested(result, "start") == "21:15:00"
    assert _suggested(result, "duration") == {"hours": 1, "minutes": 0, "seconds": 0}
    assert _suggested(result, "days") == ["Mon", "Wed", "Fri"]
    assert _suggested(result, "color") == [255, 0, 0]
    assert _suggested(result, "brightness") == 0x40
    assert _suggested(result, "sound") == "ocean"
    assert _suggested(result, "volume") == 0x30
    assert _suggested(result, "toddler_lock") is True


@pytest.mark.asyncio
async def test_an_empty_slot_starts_as_a_new_program(
    hass: HomeAssistant, entry: MockConfigEntry
):
    """Test an empty slot opens with defaults rather than zeros."""
    _, result = await _open(hass, entry, "7")

    assert _suggested(result, "name") == "Program 7"
    assert _suggested(result, "enabled") is False
    assert _suggested(result, "days") == [
        "Sun",
        "Mon",
        "Tue",
        "Wed",
        "Thu",
        "Fri",
        "Sat",
    ]


def _form(**changes):
    """Return the TestA form as submitted, with some fields changed."""
    return {
        "enabled": True,
        "name": "TestA",
        "start": "21:15:00",
        "duration": {"hours": 1, "minutes": 0, "seconds": 0},
        "days": ["Mon", "Wed", "Fri"],
        "color": [255, 0, 0],
        "brightness": 64,
        "sound": "ocean",
        "volume": 48,
        "toddler_lock": True,
        **changes,
    }


@pytest.mark.asyncio
async def test_saving_writes_the_whole_program(
    hass: HomeAssistant, entry: MockConfigEntry, device: MagicMock
):
    """Test saving sends every field, and leaves the entry's options alone."""
    flow, _ = await _open(hass, entry, "5")

    result = await flow.async_step_program(
        _form(
            start="07:30:15",
            duration={"hours": 0, "minutes": 10, "seconds": 0},
            days=["Sun", "Sat"],
            sound="rain",
        ),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"kept": True}
    device.async_set_program.assert_awaited_once_with(
        5,
        enabled=True,
        name="TestA",
        start=time(7, 30, 15),
        duration_seconds=600,
        days_mask=0x41,
        color=(255, 0, 0),
        brightness=64,
        sound=PyHatchBabyRestSound.rain,
        volume=48,
        toddler_lock=True,
    )


@pytest.mark.asyncio
async def test_a_failed_write_says_so_and_keeps_the_form(
    hass: HomeAssistant, entry: MockConfigEntry, device: MagicMock
):
    """Test a write the device did not take leaves the edits to try again."""
    device.async_set_program.side_effect = HatchRestConnectionError("no")
    flow, _ = await _open(hass, entry, "5")

    result = await flow.async_step_program(_form(name="Renamed"))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "write_failed"}
    assert _suggested(result, "name") == "Renamed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "errors"),
    [
        (
            {"duration": {"hours": 18, "minutes": 13, "seconds": 0}},
            {"duration": "duration_too_long"},
        ),
        ({"name": "A much longer name"}, {"name": "name_invalid"}),
        ({"name": "Café"}, {"name": "name_invalid"}),
    ],
)
async def test_what_the_device_cannot_hold_is_refused(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    device: MagicMock,
    changes,
    errors,
):
    """Test a value the device cannot hold is caught on the form, unsent."""
    flow, _ = await _open(hass, entry, "5")

    result = await flow.async_step_program(_form(**changes))

    assert result["errors"] == errors
    device.async_set_program.assert_not_awaited()


@pytest.mark.asyncio
async def test_waits_for_the_programs_to_be_read(
    hass: HomeAssistant, entry: MockConfigEntry, device: MagicMock
):
    """Test the editor will not open before the programs have been read."""
    device.programs = {}

    result = await _flow(hass, entry).async_step_programs()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "programs_unread"


@pytest.mark.asyncio
async def test_lists_the_favorites(hass: HomeAssistant, entry: MockConfigEntry):
    """Test the favorite step offers each favorite read, by what it holds."""
    result = await _flow(hass, entry).async_step_favorites()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "favorites"
    selector = result["data_schema"].schema["favorite"]
    assert [option["label"] for option in selector.config["options"]] == [
        "2: Favorite 2 (#FDD12D, ocean, on)",
    ]


@pytest.mark.asyncio
async def test_the_favorite_form_starts_from_what_the_device_holds(
    hass: HomeAssistant, entry: MockConfigEntry
):
    """Test every field is filled in with the favorite's current setting."""
    _, result = await _open_favorite(hass, entry, "2")

    assert result["step_id"] == "favorite"
    assert result["description_placeholders"] == {"slot": "2"}
    assert _suggested(result, "enabled") is True
    assert _suggested(result, "color") == [253, 209, 45]
    assert _suggested(result, "brightness") == 0x7F
    assert _suggested(result, "sound") == "ocean"
    assert _suggested(result, "volume") == 0x54


@pytest.mark.asyncio
async def test_saving_writes_the_whole_favorite(
    hass: HomeAssistant, entry: MockConfigEntry, device: MagicMock
):
    """Test saving sends every field, and leaves the entry's options alone."""
    flow, _ = await _open_favorite(hass, entry, "2")

    result = await flow.async_step_favorite(
        {
            "enabled": False,
            "color": [0, 128, 255],
            "brightness": 32,
            "sound": "rain",
            "volume": 16,
        }
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"kept": True}
    device.async_set_favorite.assert_awaited_once_with(
        2,
        enabled=False,
        color=(0, 128, 255),
        brightness=32,
        sound=PyHatchBabyRestSound.rain,
        volume=16,
    )


@pytest.mark.asyncio
async def test_a_failed_favorite_write_says_so_and_keeps_the_form(
    hass: HomeAssistant, entry: MockConfigEntry, device: MagicMock
):
    """Test a favorite the device did not take leaves the edits to try again."""
    device.async_set_favorite.side_effect = HatchRestConnectionError("no")
    flow, _ = await _open_favorite(hass, entry, "2")

    result = await flow.async_step_favorite(
        {
            "enabled": True,
            "color": [253, 209, 45],
            "brightness": 127,
            "sound": "ocean",
            "volume": 99,
        }
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "favorite_write_failed"}
    assert _suggested(result, "volume") == 99


@pytest.mark.asyncio
async def test_waits_for_the_favorites_to_be_read(
    hass: HomeAssistant, entry: MockConfigEntry, device: MagicMock
):
    """Test the favorite editor will not open before the favorites are read."""
    device.favorites = {}

    result = await _flow(hass, entry).async_step_favorites()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "favorites_unread"
