"""Unit tests for the flash backends.

All subprocess invocations are mocked -- we never actually talk to hardware
in CI. The HIL layer in hil_test.py is where real flash validation lives.
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import flash
from flash import esp32 as esp32_backend
from flash import rp2040 as rp2040_backend
from flash import avr as avr_backend
from flash import stm32 as stm32_backend


# ── ESP32 backend ─────────────────────────────────────────────────────────


def _fake_run_ok(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_esp32_probe_parses_chip_type():
    stdout = "esptool.py v4.7\nDetecting chip type... Unsupported detection protocol\nChip is ESP32-C3 (revision v0.4)\n"
    with patch("flash.esp32.subprocess.run", return_value=_fake_run_ok(stdout=stdout)):
        b = esp32_backend.Esp32Backend(esptool_cmd=["esptool"])
        result = b.probe("/dev/ttyUSB0")
    assert result.ok
    assert result.variant == "esp32c3"


def test_esp32_probe_failure_returns_reason():
    with patch("flash.esp32.subprocess.run", return_value=_fake_run_ok(returncode=2, stderr="no serial data\n")):
        b = esp32_backend.Esp32Backend(esptool_cmd=["esptool"])
        result = b.probe("/dev/ttyUSB0")
    assert not result.ok
    assert "no serial data" in (result.reason or "")


def test_esp32_flash_invokes_write_flash_with_offset(tmp_path):
    firmware = tmp_path / "milo-receiver-esp32c3.bin"
    firmware.write_bytes(b"\x00" * 1024)

    captured = {}

    def _fake_run(cmd, *a, **kw):
        captured["cmd"] = cmd
        return _fake_run_ok()

    with patch("flash.esp32.subprocess.run", side_effect=_fake_run):
        b = esp32_backend.Esp32Backend(esptool_cmd=["esptool"])
        result = b.flash("/dev/ttyUSB0", str(firmware), variant="esp32c3")

    assert result.ok
    assert result.bytes_written == 1024
    assert captured["cmd"][0] == "esptool"
    assert "--port" in captured["cmd"]
    assert "write_flash" in captured["cmd"]
    assert "0x0" in captured["cmd"]
    assert str(firmware) in captured["cmd"]


def test_esp32_flash_missing_firmware():
    b = esp32_backend.Esp32Backend(esptool_cmd=["esptool"])
    result = b.flash("/dev/ttyUSB0", "/nonexistent/path.bin", variant="esp32c3")
    assert not result.ok
    assert "firmware not found" in (result.reason or "")


def test_esp32_flash_handles_esptool_not_installed(tmp_path):
    firmware = tmp_path / "fw.bin"
    firmware.write_bytes(b"\x00")
    with patch("flash.esp32.subprocess.run", side_effect=FileNotFoundError("esptool")):
        b = esp32_backend.Esp32Backend(esptool_cmd=["esptool"])
        result = b.flash("/dev/ttyUSB0", str(firmware), variant="esp32c3")
    assert not result.ok
    assert "not installed" in (result.reason or "")


# ── RP2040 backend ────────────────────────────────────────────────────────


def test_rp2040_probe_detects_bootsel_mount(tmp_path):
    mount = tmp_path / "RPI-RP2"
    mount.mkdir()
    (mount / "INFO_UF2.TXT").write_text("UF2 Bootloader\n")
    with patch("flash.rp2040._find_bootsel_mount", return_value=mount):
        b = rp2040_backend.Rp2040Backend()
        result = b.probe("/dev/whatever")
    assert result.ok
    assert result.variant == "rp2040"


def test_rp2040_flash_copies_uf2(tmp_path):
    mount = tmp_path / "RPI-RP2"
    mount.mkdir()
    (mount / "INFO_UF2.TXT").write_text("ok")

    firmware = tmp_path / "fw.uf2"
    firmware.write_bytes(b"UF2\n" + b"\x00" * 500)

    with patch("flash.rp2040._find_bootsel_mount", return_value=mount):
        b = rp2040_backend.Rp2040Backend()
        result = b.flash("/dev/whatever", str(firmware), variant="rp2040")

    assert result.ok
    assert result.bytes_written == firmware.stat().st_size
    assert (mount / "fw.uf2").exists()


def test_rp2040_flash_rejects_non_uf2(tmp_path):
    firmware = tmp_path / "fw.bin"
    firmware.write_bytes(b"not a uf2")
    b = rp2040_backend.Rp2040Backend()
    result = b.flash("/dev/whatever", str(firmware))
    assert not result.ok
    assert "uf2" in (result.reason or "").lower()


def test_rp2040_flash_no_mount_and_no_picotool(tmp_path):
    firmware = tmp_path / "fw.uf2"
    firmware.write_bytes(b"UF2\n")
    with patch("flash.rp2040._find_bootsel_mount", return_value=None), \
         patch("flash.rp2040._picotool_available", return_value=False):
        b = rp2040_backend.Rp2040Backend()
        result = b.flash("/dev/whatever", str(firmware))
    assert not result.ok
    assert "BOOTSEL" in (result.reason or "")


# ── AVR backend ───────────────────────────────────────────────────────────


def test_avr_probe_detects_atmega328p():
    # avrdude -n prints "Device signature = 0x1e950f" to stderr on success.
    def fake_run(cmd, *a, **kw):
        if "m328p" in cmd:
            return _fake_run_ok(stderr="avrdude: Device signature = 0x1e950f\n")
        return _fake_run_ok(returncode=1, stderr="avrdude: Device signature = 0x000000\n")

    with patch("flash.avr.subprocess.run", side_effect=fake_run):
        b = avr_backend.AvrBackend(avrdude_cmd="avrdude")
        result = b.probe("/dev/ttyUSB0")
    assert result.ok
    assert result.variant == "atmega328p"


def test_avr_flash_invokes_avrdude(tmp_path):
    firmware = tmp_path / "sketch.hex"
    firmware.write_text(":020000040000FA\n:00000001FF\n")
    captured = {}

    def fake_run(cmd, *a, **kw):
        captured["cmd"] = cmd
        return _fake_run_ok()

    with patch("flash.avr.subprocess.run", side_effect=fake_run):
        b = avr_backend.AvrBackend(avrdude_cmd="avrdude")
        result = b.flash("/dev/ttyUSB0", str(firmware), variant="atmega328p")

    assert result.ok
    assert "-p" in captured["cmd"] and "m328p" in captured["cmd"]
    assert "-c" in captured["cmd"] and "arduino" in captured["cmd"]
    assert any(str(firmware) in tok for tok in captured["cmd"])


# ── STM32 backend ─────────────────────────────────────────────────────────


def test_stm32_probe_detects_dfu():
    dfu_stdout = "Found DFU: [0483:df11] ver=0200, devnum=14, cfg=1, intf=0, alt=0, name=\"@Internal Flash  /0x08000000/64*0002Kg\"\n"
    with patch("flash.stm32.shutil.which", return_value="/usr/bin/dfu-util"), \
         patch("flash.stm32.subprocess.run", return_value=_fake_run_ok(stdout=dfu_stdout)):
        b = stm32_backend.Stm32Backend()
        result = b.probe("/dev/does-not-matter")
    assert result.ok
    assert (result.variant or "").startswith("stm32")
    assert (result.details or {}).get("mode") == "dfu"


def test_stm32_flash_dfu(tmp_path):
    firmware = tmp_path / "fw.bin"
    firmware.write_bytes(b"\x00" * 128)

    def fake_run(cmd, *a, **kw):
        if "--list" in cmd:
            return _fake_run_ok(stdout="Found DFU: [0483:df11]")
        return _fake_run_ok()

    with patch("flash.stm32.shutil.which", return_value="/usr/bin/dfu-util"), \
         patch("flash.stm32.subprocess.run", side_effect=fake_run):
        b = stm32_backend.Stm32Backend()
        result = b.flash("/dev/whatever", str(firmware))
    assert result.ok
    assert result.bytes_written == 128


# ── registry ──────────────────────────────────────────────────────────────


def test_registry_lazy_registers_all_backends():
    names = flash.available_backends()
    for expected in ("esptool", "uf2", "avrdude", "stm32loader"):
        assert expected in names


def test_get_backend_returns_correct_type():
    assert isinstance(flash.get_backend("esptool"), esp32_backend.Esp32Backend)
    assert isinstance(flash.get_backend("uf2"), rp2040_backend.Rp2040Backend)
    assert isinstance(flash.get_backend("avrdude"), avr_backend.AvrBackend)
    assert isinstance(flash.get_backend("stm32loader"), stm32_backend.Stm32Backend)
    assert flash.get_backend("nonexistent") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
