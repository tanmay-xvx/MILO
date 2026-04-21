"""Unit tests for scripts/generate_manifest.py."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_manifest.py"


def _build_artifacts(root: Path, variant: str, family: str, ext: str, body: bytes) -> None:
    sub = root / f"lial-receiver-{variant}"
    sub.mkdir(parents=True)
    fw = sub / f"lial-receiver-{variant}.{ext}"
    fw.write_bytes(body)
    sha = hashlib.sha256(body).hexdigest()
    meta = {
        "variant": variant,
        "family": family,
        "filename": fw.name,
        "size_bytes": len(body),
        "sha256": sha,
    }
    (sub / f"{fw.name}.meta.json").write_text(json.dumps(meta))


def test_generate_manifest_single_variant(tmp_path):
    artifacts = tmp_path / "artifacts"
    _build_artifacts(artifacts, "esp32c3", "esp32", "bin", b"\xDE" * 1024)

    out = tmp_path / "manifest.json"
    result = subprocess.run(
        [
            sys.executable, str(GENERATOR),
            "--artifacts-dir", str(artifacts),
            "--firmware-version", "0.3.0",
            "--base-url", "https://example.com/releases/v0.3.0",
            "--output", str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    manifest = json.loads(out.read_text())
    assert manifest["firmware_version"] == "0.3.0"
    assert manifest["manifest_version"] == 1
    assert len(manifest["variants"]) == 1

    v = manifest["variants"][0]
    assert v["variant"] == "esp32c3"
    assert v["family"] == "esp32"
    assert v["filename"] == "lial-receiver-esp32c3.bin"
    assert v["size_bytes"] == 1024
    assert v["sha256"] == hashlib.sha256(b"\xDE" * 1024).hexdigest()
    assert v["flash_tool"] == "esptool"
    assert v["url"] == "https://example.com/releases/v0.3.0/lial-receiver-esp32c3.bin"
    assert "esptool" in v["flash_instructions"]


def test_generate_manifest_multiple_variants(tmp_path):
    artifacts = tmp_path / "artifacts"
    _build_artifacts(artifacts, "esp32c3", "esp32", "bin", b"\x01" * 512)
    _build_artifacts(artifacts, "rp2040", "rp2040", "uf2", b"\x02" * 512)

    out = tmp_path / "manifest.json"
    subprocess.run(
        [
            sys.executable, str(GENERATOR),
            "--artifacts-dir", str(artifacts),
            "--firmware-version", "0.3.0",
            "--base-url", "https://example.com/v0.3.0",
            "--output", str(out),
        ],
        check=True,
    )
    manifest = json.loads(out.read_text())
    variants = {v["variant"]: v for v in manifest["variants"]}
    assert "esp32c3" in variants
    assert "rp2040" in variants
    assert variants["rp2040"]["flash_tool"] == "uf2"
    assert "BOOTSEL" in variants["rp2040"]["flash_instructions"]


def test_generate_manifest_fails_on_empty_dir(tmp_path):
    artifacts = tmp_path / "empty"
    artifacts.mkdir()
    out = tmp_path / "manifest.json"
    result = subprocess.run(
        [
            sys.executable, str(GENERATOR),
            "--artifacts-dir", str(artifacts),
            "--firmware-version", "0.1.0",
            "--base-url", "https://example.com",
            "--output", str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
