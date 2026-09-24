"""Tests for Hatch Rest constants."""

from habluetooth.const import (
    AUTO_WINDOW_MAX_DURATION,
    MIN_ACTIVE_SCAN_DURATION,
    MIN_ACTIVE_SCAN_INTERVAL,
)

from custom_components.hatch_rest.const import (
    ACTIVE_SCAN_DURATION_SECONDS,
    ACTIVE_SCAN_INTERVAL_SECONDS,
)


class TestActiveScanSettings:
    """Tests for the active scan request values.

    async_register_callback passes these straight to habluetooth, which
    rejects out of range values by raising, failing setup of the entry.
    Check them against the library's own bounds rather than copied numbers.
    """

    def test_scan_interval_is_accepted(self):
        """Test the scan interval is not below the library minimum."""
        assert ACTIVE_SCAN_INTERVAL_SECONDS >= MIN_ACTIVE_SCAN_INTERVAL

    def test_scan_duration_is_accepted(self):
        """Test the scan duration is not below the library minimum."""
        assert ACTIVE_SCAN_DURATION_SECONDS >= MIN_ACTIVE_SCAN_DURATION

    def test_scan_duration_is_not_wasted(self):
        """Test the scan duration is not above what the scheduler will use."""
        assert ACTIVE_SCAN_DURATION_SECONDS <= AUTO_WINDOW_MAX_DURATION
