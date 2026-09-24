"""Tests for Hatch Rest constants."""

import pytest
from habluetooth import BluetoothManager

from custom_components.hatch_rest.const import (
    ACTIVE_SCAN_DURATION_SECONDS,
    ACTIVE_SCAN_INTERVAL_SECONDS,
    COMMAND_SETTLE_SECONDS,
    IDLE_DISCONNECT_SECONDS,
)


class TestActiveScanSettings:
    """Tests for the active scan request values.

    async_register_callback hands these to habluetooth, which validates them
    and raises on anything out of range, failing setup of every config entry.
    Run them through the library's own validation rather than asserting
    against numbers copied out of it.
    """

    def test_values_are_accepted(self):
        """Test habluetooth accepts an active scan request with our values."""
        manager = BluetoothManager()

        cancel = manager.async_register_active_scan(
            "AA:BB:CC:DD:EE:FF",
            ACTIVE_SCAN_INTERVAL_SECONDS,
            ACTIVE_SCAN_DURATION_SECONDS,
        )

        assert callable(cancel)
        cancel()

    def test_an_interval_below_the_minimum_is_rejected(self):
        """Test the validation this guards against is really enforced."""
        manager = BluetoothManager()

        with pytest.raises(ValueError, match="scan_interval"):
            manager.async_register_active_scan("AA:BB:CC:DD:EE:FF", 15, 10)


class TestIdleDisconnect:
    """Tests for how long a connection is held open."""

    def test_idle_window_is_short(self):
        """Test the connection is not held long enough to hide the device.

        A connected device stops advertising, so its state is invisible for
        as long as the connection is held: a command cannot be confirmed and
        a button pressed on the device is not seen.
        """
        assert IDLE_DISCONNECT_SECONDS <= 10

    def test_idle_window_outlasts_a_command(self):
        """Test a burst of commands is not made to reconnect each time."""
        assert IDLE_DISCONNECT_SECONDS > COMMAND_SETTLE_SECONDS
