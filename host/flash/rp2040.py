"""Flash backend for Raspberry Pi RP2040 / RP2350.

RP2040 in BOOTSEL mode exposes itself as a USB mass-storage volume named
`RPI-RP2` (or `RP2350` for RP2350). Flashing is literally copying a `.uf2`
file onto that volume -- no toolchain required.

When the device is already running MILO firmware (not in BOOTSEL), we can
reboot into BOOTSEL via `picotool reboot -f -u` and then copy the UF2.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from . import FlashBackend, FlashResult, ProbeResult

# Volume labels we accept as an RP2040/RP2350 in BOOTSEL mode.
BOOTSEL_VOLUMES = ("RPI-RP2", "RP2350")


class Rp2040Backend(FlashBackend):
    name = "uf2"

    def probe(self, port: str) -> ProbeResult:
        # A Pico in BOOTSEL mode does not enumerate as a serial port -- it's a
        # mass-storage volume. A Pico already running a CDC-enabled firmware
        # does enumerate as a port, so we accept either signal.
        mount = _find_bootsel_mount()
        if mount:
            variant = "rp2350" if "RP2350" in mount.name else "rp2040"
            return ProbeResult(ok=True, variant=variant, details={"mount": str(mount)})

        if _picotool_available():
            info = _picotool_info(port)
            if info:
                return ProbeResult(ok=True, variant=info, details={"via": "picotool"})

        return ProbeResult(ok=False, reason="no BOOTSEL volume and picotool not available")

    def flash(
        self,
        port: str,
        firmware_path: str,
        variant: str | None = None,
    ) -> FlashResult:
        if not os.path.exists(firmware_path):
            return FlashResult(ok=False, reason=f"firmware not found: {firmware_path}")
        if not firmware_path.endswith(".uf2"):
            return FlashResult(ok=False, reason="RP2040 expects .uf2 firmware")

        mount = _find_bootsel_mount()
        if mount is None and _picotool_available():
            # Try to force the device into BOOTSEL mode.
            subprocess.run(
                ["picotool", "reboot", "-f", "-u"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            time.sleep(2)
            mount = _wait_for_bootsel(timeout_s=10)

        if mount is None:
            return FlashResult(ok=False, reason="no RP2040 BOOTSEL volume detected; hold BOOTSEL and re-plug")

        try:
            shutil.copy(firmware_path, mount)
        except OSError as e:
            return FlashResult(ok=False, reason=f"copy failed: {e}")

        return FlashResult(ok=True, bytes_written=os.path.getsize(firmware_path))


def _find_bootsel_mount() -> Path | None:
    """Return the Path of a currently-mounted RPI-RP2/RP2350 volume."""
    # macOS: /Volumes/RPI-RP2
    # Linux: /media/$USER/RPI-RP2, /run/media/$USER/RPI-RP2
    candidates: list[Path] = []
    for base in ("/Volumes", f"/media/{os.environ.get('USER', '')}", f"/run/media/{os.environ.get('USER', '')}"):
        p = Path(base)
        if p.is_dir():
            candidates.extend(p.iterdir())

    for c in candidates:
        if c.name in BOOTSEL_VOLUMES:
            info_file = c / "INFO_UF2.TXT"
            if info_file.exists():
                return c
    return None


def _wait_for_bootsel(timeout_s: float) -> Path | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        mount = _find_bootsel_mount()
        if mount:
            return mount
        time.sleep(0.25)
    return None


def _picotool_available() -> bool:
    return shutil.which("picotool") is not None


def _picotool_info(port: str) -> str | None:
    if not _picotool_available():
        return None
    try:
        res = subprocess.run(
            ["picotool", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    out = res.stdout.lower()
    if "rp2350" in out:
        return "rp2350"
    if "rp2040" in out:
        return "rp2040"
    return None


BACKEND = Rp2040Backend
