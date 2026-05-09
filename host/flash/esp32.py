"""Flash backend for the Espressif ESP32 family (C3, S2, S3, C6, H2, ...).

Uses the `esptool` Python library (invoked as a subprocess for robustness
across esptool versions; the embedded Python API churns between releases).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import FlashBackend, FlashResult, ProbeResult


# Typical flash offsets for a single-image build. Adjust via `offset=` if the
# firmware ships multi-part (bootloader + partition table + app).
_DEFAULT_OFFSET = {
    "esp32": "0x1000",
    "esp32c3": "0x0",
    "esp32s2": "0x1000",
    "esp32s3": "0x0",
    "esp32c6": "0x0",
    "esp32h2": "0x0",
}


class Esp32Backend(FlashBackend):
    name = "esptool"

    def __init__(self, esptool_cmd: list[str] | None = None) -> None:
        self._cmd = esptool_cmd or self._default_cmd()

    @staticmethod
    def _default_cmd() -> list[str]:
        bin_name = shutil.which("esptool.py") or shutil.which("esptool")
        if bin_name:
            return [bin_name]
        return [sys.executable, "-m", "esptool"]

    def probe(self, port: str) -> ProbeResult:
        try:
            result = subprocess.run(
                [*self._cmd, "--port", port, "chip_id"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError as e:
            return ProbeResult(ok=False, reason=f"esptool not installed: {e}")
        except subprocess.TimeoutExpired:
            return ProbeResult(ok=False, reason="esptool chip_id timed out (is the port busy?)")

        if result.returncode != 0:
            return ProbeResult(ok=False, reason=_last_line(result.stderr) or "esptool chip_id failed")

        variant = _parse_chip_variant(result.stdout)
        if variant is None:
            return ProbeResult(ok=False, reason="could not parse chip type from esptool output")
        return ProbeResult(ok=True, variant=variant, details={"stdout": result.stdout})

    def flash(
        self,
        port: str,
        firmware_path: str,
        variant: str | None = None,
    ) -> FlashResult:
        if not os.path.exists(firmware_path):
            return FlashResult(ok=False, reason=f"firmware not found: {firmware_path}")

        offset = _DEFAULT_OFFSET.get(variant or "", "0x0")
        chip_arg: list[str] = []
        if variant:
            chip_arg = ["--chip", variant]

        cmd = [
            *self._cmd,
            *chip_arg,
            "--port", port,
            "--baud", "921600",
            "write_flash",
            offset,
            firmware_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except FileNotFoundError as e:
            return FlashResult(ok=False, reason=f"esptool not installed: {e}")
        except subprocess.TimeoutExpired:
            return FlashResult(ok=False, reason="esptool write_flash timed out")

        if result.returncode != 0:
            return FlashResult(
                ok=False,
                reason=_last_line(result.stderr) or "esptool write_flash failed",
            )

        return FlashResult(ok=True, bytes_written=os.path.getsize(firmware_path))


def _parse_chip_variant(stdout: str) -> str | None:
    """Turn esptool's `Chip is ESP32-C3 (revision v0.4)` into `esp32c3`."""
    for line in stdout.splitlines():
        if "Chip is" in line:
            rest = line.split("Chip is", 1)[1].strip()
            token = rest.split()[0]
            return token.replace("-", "").lower()
        if "Detecting chip type..." in line and "ESP32" in line:
            tail = line.split("...", 1)[1].strip()
            return tail.replace("-", "").lower()
    return None


def _last_line(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


BACKEND = Esp32Backend
