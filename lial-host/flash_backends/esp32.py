"""Flash backend for the Espressif ESP32 family (C3, S2, S3, C6, H2, ...).

Prefers `espflash` (Rust-based, handles USB JTAG reset correctly) and falls
back to `esptool` (Python-based, broader install base) if espflash is not
found.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from . import FlashBackend, FlashResult, ProbeResult


_DEFAULT_OFFSET = {
    "esp32": "0x1000",
    "esp32c3": "0x0",
    "esp32s2": "0x1000",
    "esp32s3": "0x0",
    "esp32c6": "0x0",
    "esp32h2": "0x0",
}


def _find_espflash() -> str | None:
    return shutil.which("espflash")


def _find_esptool() -> list[str] | None:
    for name in ("esptool.py", "esptool"):
        path = shutil.which(name)
        if path:
            return [path]
    try:
        subprocess.run(
            [sys.executable, "-m", "esptool", "version"],
            capture_output=True, timeout=5,
        )
        return [sys.executable, "-m", "esptool"]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


class Esp32Backend(FlashBackend):
    name = "esptool"

    def probe(self, port: str) -> ProbeResult:
        espflash = _find_espflash()
        if espflash:
            return self._probe_espflash(espflash, port)

        esptool = _find_esptool()
        if esptool:
            return self._probe_esptool(esptool, port)

        return ProbeResult(ok=False, reason="neither espflash nor esptool found")

    def flash(
        self,
        port: str,
        firmware_path: str,
        variant: str | None = None,
    ) -> FlashResult:
        if not os.path.exists(firmware_path):
            return FlashResult(ok=False, reason=f"firmware not found: {firmware_path}")

        espflash = _find_espflash()
        if espflash:
            return self._flash_espflash(espflash, port, firmware_path, variant)

        esptool = _find_esptool()
        if esptool:
            return self._flash_esptool(esptool, port, firmware_path, variant)

        return FlashResult(ok=False, reason="neither espflash nor esptool found")

    # ── espflash ─────────────────────────────────────────────────────

    @staticmethod
    def _probe_espflash(espflash: str, port: str) -> ProbeResult:
        try:
            result = subprocess.run(
                [espflash, "board-info", "--port", port],
                capture_output=True, text=True, timeout=15,
            )
        except FileNotFoundError as e:
            return ProbeResult(ok=False, reason=f"espflash not found: {e}")
        except subprocess.TimeoutExpired:
            return ProbeResult(ok=False, reason="espflash board-info timed out")

        if result.returncode != 0:
            combined = (result.stdout + result.stderr).strip()
            return ProbeResult(ok=False, reason=_last_line(combined) or "espflash board-info failed")

        variant = _parse_espflash_chip(result.stdout)
        if variant is None:
            return ProbeResult(ok=False, reason="could not parse chip type from espflash output")
        return ProbeResult(ok=True, variant=variant, details={"stdout": result.stdout})

    @staticmethod
    def _flash_espflash(
        espflash: str, port: str, firmware_path: str, variant: str | None,
    ) -> FlashResult:
        is_bin = firmware_path.endswith(".bin")
        if is_bin:
            offset = _DEFAULT_OFFSET.get(variant or "", "0x0")
            cmd = [espflash, "write-bin", "--port", port, offset, firmware_path]
        else:
            cmd = [espflash, "flash", "--port", port, firmware_path]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except FileNotFoundError as e:
            return FlashResult(ok=False, reason=f"espflash not found: {e}")
        except subprocess.TimeoutExpired:
            return FlashResult(ok=False, reason="espflash timed out")

        if result.returncode != 0:
            combined = (result.stdout + result.stderr).strip()
            return FlashResult(ok=False, reason=_last_line(combined) or "espflash failed")

        return FlashResult(ok=True, bytes_written=os.path.getsize(firmware_path))

    # ── esptool (fallback) ───────────────────────────────────────────

    @staticmethod
    def _probe_esptool(esptool_cmd: list[str], port: str) -> ProbeResult:
        try:
            result = subprocess.run(
                [*esptool_cmd, "--port", port, "chip_id"],
                capture_output=True, text=True, timeout=15,
            )
        except FileNotFoundError as e:
            return ProbeResult(ok=False, reason=f"esptool not installed: {e}")
        except subprocess.TimeoutExpired:
            return ProbeResult(ok=False, reason="esptool chip_id timed out (is the port busy?)")

        if result.returncode != 0:
            return ProbeResult(ok=False, reason=_last_line(result.stderr) or "esptool chip_id failed")

        variant = _parse_esptool_chip(result.stdout)
        if variant is None:
            return ProbeResult(ok=False, reason="could not parse chip type from esptool output")
        return ProbeResult(ok=True, variant=variant, details={"stdout": result.stdout})

    @staticmethod
    def _flash_esptool(
        esptool_cmd: list[str], port: str, firmware_path: str, variant: str | None,
    ) -> FlashResult:
        offset = _DEFAULT_OFFSET.get(variant or "", "0x0")
        chip_arg: list[str] = ["--chip", variant] if variant else []

        cmd = [
            *esptool_cmd,
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


def _parse_espflash_chip(stdout: str) -> str | None:
    """Parse espflash board-info output for chip type."""
    for line in stdout.splitlines():
        low = line.lower()
        if "chip type:" in low:
            chip = line.split(":", 1)[1].strip().split()[0]
            return chip.replace("-", "").lower()
    return None


def _parse_esptool_chip(stdout: str) -> str | None:
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
