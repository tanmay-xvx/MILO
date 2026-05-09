"""Unit tests for `milo download`."""

import argparse
import hashlib
import io
import json
from unittest.mock import MagicMock, patch

import pytest

from flash import download as download_cmd


SAMPLE_MANIFEST = {
    "firmware_version": "0.2.0",
    "variants": [
        {
            "variant": "esp32c3",
            "family": "esp32",
            "display_name": "ESP32-C3",
            "filename": "milo-receiver-esp32c3-0.2.0.bin",
            "url": "https://example.com/milo-receiver-esp32c3-0.2.0.bin",
            "size_bytes": 4096,
            "sha256": "a" * 64,
            "flash_tool": "esptool",
        },
        {
            "variant": "rp2040",
            "family": "rp2040",
            "display_name": "Raspberry Pi Pico",
            "filename": "milo-receiver-rp2040-0.2.0.uf2",
            "url": "https://example.com/milo-receiver-rp2040-0.2.0.uf2",
            "size_bytes": 6144,
        },
    ],
}


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self._consumed = False

    def read(self, n: int = -1) -> bytes:
        if self._consumed:
            return b""
        if n < 0 or n >= len(self._body):
            self._consumed = True
            return self._body
        out, self._body = self._body[:n], self._body[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_fetch_manifest_parses_json():
    body = json.dumps(SAMPLE_MANIFEST).encode()
    with patch("flash.download.urllib.request.urlopen", return_value=FakeResponse(body)):
        m = download_cmd.fetch_manifest("https://example.com/manifest.json")
    assert m["firmware_version"] == "0.2.0"
    assert len(m["variants"]) == 2


def test_fetch_manifest_invalid_json_raises():
    with patch("flash.download.urllib.request.urlopen", return_value=FakeResponse(b"not json")):
        with pytest.raises(download_cmd.DownloadError):
            download_cmd.fetch_manifest("https://example.com/manifest.json")


def test_download_firmware_writes_file_atomically(tmp_path):
    body = b"\xFE" * 2048
    dest = tmp_path / "fw.bin"
    with patch("flash.download.urllib.request.urlopen", return_value=FakeResponse(body)):
        download_cmd.download_firmware("https://example.com/fw.bin", dest)
    assert dest.read_bytes() == body
    # .part file should have been removed (atomic rename).
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_run_with_explicit_board(tmp_path, capsys):
    body = json.dumps(SAMPLE_MANIFEST).encode()
    fw_bytes = b"\xAA" * 4096

    def fake_urlopen(url, *a, **kw):
        if url.endswith(".json"):
            return FakeResponse(body)
        return FakeResponse(fw_bytes)

    args = argparse.Namespace(
        board="esp32c3",
        manifest_url="https://example.com/manifest.json",
        cache_dir=str(tmp_path),
        verify=False,
    )
    with patch("flash.download.urllib.request.urlopen", side_effect=fake_urlopen):
        rc = download_cmd.run(args)
    assert rc == 0
    downloaded = tmp_path / "milo-receiver-esp32c3-0.2.0.bin"
    assert downloaded.exists()
    assert downloaded.stat().st_size == 4096


def test_run_with_verify_good_sha256(tmp_path):
    fw_bytes = b"\x5A" * 512
    digest = hashlib.sha256(fw_bytes).hexdigest()
    manifest = {
        "variants": [{
            "variant": "tiny",
            "filename": "tiny.bin",
            "url": "https://example.com/tiny.bin",
            "sha256": digest,
            "size_bytes": 512,
        }],
    }
    body = json.dumps(manifest).encode()

    def fake_urlopen(url, *a, **kw):
        return FakeResponse(body if url.endswith(".json") else fw_bytes)

    args = argparse.Namespace(
        board="tiny",
        manifest_url="https://example.com/manifest.json",
        cache_dir=str(tmp_path),
        verify=True,
    )
    with patch("flash.download.urllib.request.urlopen", side_effect=fake_urlopen):
        rc = download_cmd.run(args)
    assert rc == 0


def test_run_with_verify_bad_sha256(tmp_path, capsys):
    fw_bytes = b"\x5A" * 512
    manifest = {
        "variants": [{
            "variant": "tiny",
            "filename": "tiny.bin",
            "url": "https://example.com/tiny.bin",
            "sha256": "0" * 64,  # intentionally wrong
            "size_bytes": 512,
        }],
    }
    body = json.dumps(manifest).encode()

    def fake_urlopen(url, *a, **kw):
        return FakeResponse(body if url.endswith(".json") else fw_bytes)

    args = argparse.Namespace(
        board="tiny",
        manifest_url="https://example.com/manifest.json",
        cache_dir=str(tmp_path),
        verify=True,
    )
    with patch("flash.download.urllib.request.urlopen", side_effect=fake_urlopen):
        rc = download_cmd.run(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "sha256 mismatch" in err


def test_run_unknown_board_returns_error(tmp_path, capsys):
    body = json.dumps(SAMPLE_MANIFEST).encode()
    args = argparse.Namespace(
        board="stm32f9999",
        manifest_url="https://example.com/manifest.json",
        cache_dir=str(tmp_path),
        verify=False,
    )
    with patch("flash.download.urllib.request.urlopen", return_value=FakeResponse(body)):
        rc = download_cmd.run(args)
    assert rc == 1
    assert "not in manifest" in capsys.readouterr().err


def test_interactive_pick_numeric():
    with patch("builtins.input", return_value="2"):
        result = download_cmd.interactive_pick(SAMPLE_MANIFEST["variants"])
    assert result["variant"] == "rp2040"


def test_interactive_pick_by_name():
    with patch("builtins.input", return_value="esp32c3"):
        result = download_cmd.interactive_pick(SAMPLE_MANIFEST["variants"])
    assert result["variant"] == "esp32c3"


def test_interactive_pick_out_of_range():
    with patch("builtins.input", return_value="99"):
        result = download_cmd.interactive_pick(SAMPLE_MANIFEST["variants"])
    assert result is None


def test_interactive_pick_empty_input():
    with patch("builtins.input", return_value=""):
        result = download_cmd.interactive_pick(SAMPLE_MANIFEST["variants"])
    assert result is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
