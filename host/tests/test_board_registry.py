"""Unit tests for devices.boards."""

from unittest.mock import patch

import pytest

import devices.boards as br


class FakePort:
    """Mimics the fields we use off a `serial.tools.list_ports.ListPortInfo`."""

    def __init__(self, device, vid=None, pid=None, manufacturer=None, product=None, serial_number=None):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.manufacturer = manufacturer
        self.product = product
        self.serial_number = serial_number


def test_candidates_for_esp32_usb_jtag():
    matches = br.candidates_for(0x303A, 0x1001)
    assert len(matches) == 1
    assert matches[0].family == "esp32"


def test_candidates_for_rp2040_bootsel():
    matches = br.candidates_for(0x2E8A, 0x0003)
    assert len(matches) == 1
    assert matches[0].family == "rp2040"


def test_candidates_for_arduino_uno():
    matches = br.candidates_for(0x2341, 0x0043)
    assert len(matches) == 1
    assert matches[0].family == "avr"


def test_candidates_for_unknown_vidpid():
    assert br.candidates_for(0xDEAD, 0xBEEF) == []


def test_candidates_for_none_returns_empty():
    assert br.candidates_for(None, None) == []


def test_ambiguous_ch340_matches_multiple():
    """CH340 (1A86:7523) ships on both ESP32 clones and AVR Nano clones."""
    matches = br.candidates_for(0x1A86, 0x7523)
    families = {m.family for m in matches}
    assert "esp32" in families
    assert "avr" in families
    assert (0x1A86, 0x7523) in br.AMBIGUOUS_VID_PID


def test_family_by_name():
    assert br.family_by_name("esp32").family == "esp32"
    assert br.family_by_name("does-not-exist") is None


def test_family_has_variant():
    assert br.family_has_variant("esp32", "esp32c3") is True
    assert br.family_has_variant("esp32", "esp32c99") is False
    assert br.family_has_variant("nonexistent", "whatever") is False


def test_detected_device_states():
    # Fresh blank ESP32: candidates matched, no MILO firmware.
    fam = br.family_by_name("esp32")
    d = br.DetectedDevice(port="/dev/cu.usbmodem1", vid=0x303A, pid=0x1001,
                          candidate_families=[fam], confirmed_family="esp32")
    assert d.has_milo is False
    assert d.is_blank is True

    # Same device, already running MILO.
    d.milo_version = "0.2.0"
    assert d.has_milo is True
    assert d.is_blank is False

    # Unknown USB device.
    d2 = br.DetectedDevice(port="/dev/cu.usbmodem2", vid=0xDEAD, pid=0xBEEF,
                           candidate_families=[])
    assert d2.has_milo is False
    assert d2.is_blank is False


def test_enumerate_usb_devices_mocked():
    ports = [
        FakePort("/dev/cu.usbmodem101", vid=0x303A, pid=0x1001, product="USB JTAG/serial"),
        FakePort("/dev/cu.usbmodem201", vid=0xDEAD, pid=0xBEEF, product="Unknown"),
        FakePort("/dev/cu.usbmodem301", vid=0x2341, pid=0x0043, product="Arduino Uno"),
    ]
    with patch("serial.tools.list_ports.comports", return_value=ports):
        devices = br.enumerate_usb_devices()
    assert len(devices) == 3
    by_port = {d.port: d for d in devices}
    assert by_port["/dev/cu.usbmodem101"].candidate_families[0].family == "esp32"
    assert by_port["/dev/cu.usbmodem201"].candidate_families == []
    assert by_port["/dev/cu.usbmodem301"].candidate_families[0].family == "avr"


def test_every_registered_family_has_a_backend_name():
    for fam in br.BOARD_REGISTRY:
        assert isinstance(fam.flash_backend, str) and fam.flash_backend
        assert fam.firmware_ext.startswith(".")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
