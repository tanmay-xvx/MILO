"""Unit tests for `milo init`.

The end-to-end "detect a real board and flash it" path lives in the HIL
harness -- these tests cover the orchestration logic with every external
call mocked.
"""

import argparse
import json
import struct
from unittest.mock import MagicMock, patch

import pytest

import devices.boards as br
from flash import init_cmd


def _make_device(port, vid=0x303A, pid=0x1001, family_name="esp32"):
    fam = br.family_by_name(family_name)
    return br.DetectedDevice(
        port=port,
        vid=vid,
        pid=pid,
        candidate_families=[fam] if fam else [],
    )


def test_init_reports_nothing_when_no_ports():
    args = argparse.Namespace(port=None, yes=True, manifest_url="x", cache_dir="/tmp", dry_run=True)
    with patch("flash.init_cmd.enumerate_usb_devices", return_value=[]):
        rc = init_cmd.run(args)
    assert rc == 0


def test_init_skips_ports_with_unknown_vidpid(capsys):
    unknown = br.DetectedDevice(port="/dev/cu.usbmodem1", vid=0xDEAD, pid=0xBEEF, candidate_families=[])
    args = argparse.Namespace(port=None, yes=True, manifest_url="x", cache_dir="/tmp", dry_run=True)
    with patch("flash.init_cmd.enumerate_usb_devices", return_value=[unknown]):
        rc = init_cmd.run(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "unknown VID:PID" in out


def test_init_dry_run_lists_targets(capsys):
    dev = _make_device("/dev/cu.usbmodem1")
    args = argparse.Namespace(port=None, yes=True, manifest_url="x", cache_dir="/tmp", dry_run=True)
    with patch("flash.init_cmd.enumerate_usb_devices", return_value=[dev]), \
         patch("flash.init_cmd._probe_milo_firmware", return_value=None):
        rc = init_cmd.run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "/dev/cu.usbmodem1" in out
    assert "ESP32" in out


def test_init_skips_ports_with_milo_already_flashed(capsys):
    dev = _make_device("/dev/cu.usbmodem1")
    args = argparse.Namespace(port=None, yes=True, manifest_url="x", cache_dir="/tmp", dry_run=True)
    with patch("flash.init_cmd.enumerate_usb_devices", return_value=[dev]), \
         patch("flash.init_cmd._probe_milo_firmware", return_value="0.2.0"):
        rc = init_cmd.run(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "already running MILO v0.2.0" in out


def test_init_full_flow_flashes_blank_board(tmp_path):
    dev = _make_device("/dev/cu.usbmodem1")

    fake_backend = MagicMock()
    fake_backend.probe.return_value = MagicMock(ok=True, variant="esp32c3", reason=None)
    fake_backend.flash.return_value = MagicMock(ok=True, bytes_written=4096, reason=None)

    manifest = {
        "firmware_version": "0.2.0",
        "variants": [{
            "variant": "esp32c3",
            "filename": "milo-receiver-esp32c3.bin",
            "url": "https://example.com/fw.bin",
            "size_bytes": 4096,
            "family": "esp32",
        }],
    }

    args = argparse.Namespace(
        port=None, yes=True,
        manifest_url="https://example.com/manifest.json",
        cache_dir=str(tmp_path), dry_run=False,
    )

    with patch("flash.init_cmd.enumerate_usb_devices", return_value=[dev]), \
         patch("flash.init_cmd._probe_milo_firmware", return_value=None), \
         patch("flash.init_cmd.get_backend", return_value=fake_backend), \
         patch("flash.init_cmd.fetch_manifest", return_value=manifest), \
         patch("flash.init_cmd.download_firmware") as fake_dl:
        # Simulate that download_firmware actually places a file on disk.
        def place_file(url, dest):
            dest.write_bytes(b"\x00" * 4096)
        fake_dl.side_effect = place_file
        rc = init_cmd.run(args)

    assert rc == 0
    fake_backend.probe.assert_called_once_with("/dev/cu.usbmodem1")
    fake_backend.flash.assert_called_once()
    _, kwargs = fake_backend.flash.call_args
    args_positional = fake_backend.flash.call_args[0]
    assert args_positional[0] == "/dev/cu.usbmodem1"
    assert args_positional[1].endswith("milo-receiver-esp32c3.bin")
    assert kwargs["variant"] == "esp32c3"


def test_init_filters_by_port(capsys):
    d1 = _make_device("/dev/cu.usbmodem1")
    d2 = _make_device("/dev/cu.usbmodem2")
    args = argparse.Namespace(port="/dev/cu.usbmodem2", yes=True, manifest_url="x", cache_dir="/tmp", dry_run=True)
    with patch("flash.init_cmd.enumerate_usb_devices", return_value=[d1, d2]), \
         patch("flash.init_cmd._probe_milo_firmware", return_value=None):
        rc = init_cmd.run(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "/dev/cu.usbmodem2" in out
    assert "/dev/cu.usbmodem1" not in out


def test_init_missing_port_errors(capsys):
    d1 = _make_device("/dev/cu.usbmodem1")
    args = argparse.Namespace(port="/dev/nope", yes=True, manifest_url="x", cache_dir="/tmp", dry_run=True)
    with patch("flash.init_cmd.enumerate_usb_devices", return_value=[d1]):
        rc = init_cmd.run(args)
    assert rc == 1
    assert "no device found" in capsys.readouterr().err


def test_probe_milo_firmware_parses_discovery_frame():
    """Simulate a receiver that emits a discovery frame immediately on open."""
    manifest = {"board": "esp32c3", "firmware_version": "0.2.0"}
    payload = json.dumps(manifest).encode()
    frame = struct.pack(">BI", 0x01, len(payload)) + payload

    fake_serial = MagicMock()
    read_state = {"buf": frame}

    def fake_read(n):
        out, read_state["buf"] = read_state["buf"][:n], read_state["buf"][n:]
        return out

    fake_serial.read.side_effect = fake_read

    with patch("serial.Serial", return_value=fake_serial):
        version = init_cmd._probe_milo_firmware("/dev/fake", timeout_s=0.5)
    assert version == "0.2.0"


def test_probe_milo_firmware_returns_none_on_silence():
    fake_serial = MagicMock()
    fake_serial.read.return_value = b""
    with patch("serial.Serial", return_value=fake_serial):
        version = init_cmd._probe_milo_firmware("/dev/fake", timeout_s=0.1)
    assert version is None


def test_pick_family_ambiguous_picks_probing_winner():
    esp = br.family_by_name("esp32")
    avr = br.family_by_name("avr")

    esp_backend = MagicMock()
    esp_backend.probe.return_value = MagicMock(ok=False, reason="no")
    avr_backend = MagicMock()
    avr_backend.probe.return_value = MagicMock(ok=True, variant="atmega328p")

    def get_backend(name):
        return {"esptool": esp_backend, "avrdude": avr_backend}.get(name)

    dev = _make_device("/dev/cu.usbmodem1", vid=0x1A86, pid=0x7523)  # ambiguous CH340
    dev.candidate_families = [esp, avr]

    with patch("flash.init_cmd.get_backend", side_effect=get_backend):
        winner = init_cmd._pick_family(dev, [esp, avr])
    assert winner.family == "avr"


def test_pick_family_single_candidate_returns_it():
    esp = br.family_by_name("esp32")
    dev = _make_device("/dev/cu.usbmodem1")
    assert init_cmd._pick_family(dev, [esp]).family == "esp32"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
