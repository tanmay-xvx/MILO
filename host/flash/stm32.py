"""Flash backend for STM32.

Two flashing modes are supported:

1. UART bootloader (ROM) via `stm32loader`. Boot0 must be pulled high and the
   chip reset -- same approach PlatformIO uses for the Blue Pill.
2. USB DFU via `dfu-util`. Used when the chip is in DFU mode
   (VID/PID 0x0483/0xDF11).

The backend picks the appropriate mode by probing the port first.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from . import FlashBackend, FlashResult, ProbeResult


class Stm32Backend(FlashBackend):
    name = "stm32loader"

    def probe(self, port: str) -> ProbeResult:
        # Try dfu-util first.
        if shutil.which("dfu-util"):
            try:
                res = subprocess.run(
                    ["dfu-util", "--list"],
                    capture_output=True,
                    text=True,
                    timeout=6,
                )
                if res.returncode == 0 and "0483:df11" in res.stdout.lower():
                    variant = _parse_dfu_variant(res.stdout)
                    return ProbeResult(ok=True, variant=variant, details={"mode": "dfu"})
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # Fall back to stm32loader UART bootloader probe.
        try:
            from stm32loader.uart import SerialConnection  # type: ignore
        except ImportError:
            return ProbeResult(ok=False, reason="install stm32loader or dfu-util to probe STM32")

        try:
            conn = SerialConnection(port, baud=115200, parity="E")
            conn.connect()
            chip_id = conn.read_byte()
            conn.close()
        except Exception as e:
            return ProbeResult(ok=False, reason=f"stm32loader probe failed: {e}")

        if chip_id is None:
            return ProbeResult(ok=False, reason="no response from bootloader")
        return ProbeResult(ok=True, variant="stm32", details={"mode": "uart", "chip_id": chip_id})

    def flash(
        self,
        port: str,
        firmware_path: str,
        variant: str | None = None,
    ) -> FlashResult:
        if not os.path.exists(firmware_path):
            return FlashResult(ok=False, reason=f"firmware not found: {firmware_path}")

        # DFU mode first.
        if shutil.which("dfu-util"):
            try:
                res = subprocess.run(
                    ["dfu-util", "--list"],
                    capture_output=True,
                    text=True,
                    timeout=6,
                )
                if res.returncode == 0 and "0483:df11" in res.stdout.lower():
                    return self._flash_dfu(firmware_path)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        return self._flash_uart(port, firmware_path)

    def _flash_dfu(self, firmware_path: str) -> FlashResult:
        cmd = [
            "dfu-util",
            "-a", "0",
            "-s", "0x08000000:leave",
            "-D", firmware_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except FileNotFoundError as e:
            return FlashResult(ok=False, reason=f"dfu-util not installed: {e}")
        except subprocess.TimeoutExpired:
            return FlashResult(ok=False, reason="dfu-util timed out")

        if result.returncode != 0:
            return FlashResult(ok=False, reason=_last_line(result.stderr) or "dfu-util failed")
        return FlashResult(ok=True, bytes_written=os.path.getsize(firmware_path))

    def _flash_uart(self, port: str, firmware_path: str) -> FlashResult:
        cmd = [
            "stm32loader",
            "-p", port,
            "-e", "-w", "-v",
            firmware_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except FileNotFoundError as e:
            return FlashResult(ok=False, reason=f"stm32loader not installed: {e}")
        except subprocess.TimeoutExpired:
            return FlashResult(ok=False, reason="stm32loader timed out")

        if result.returncode != 0:
            return FlashResult(ok=False, reason=_last_line(result.stderr) or "stm32loader failed")
        return FlashResult(ok=True, bytes_written=os.path.getsize(firmware_path))


def _parse_dfu_variant(stdout: str) -> str | None:
    for line in stdout.splitlines():
        lower = line.lower()
        for token in ("stm32f0", "stm32f1", "stm32f3", "stm32f4", "stm32f7", "stm32h7", "stm32l4"):
            if token in lower:
                return token
    return "stm32"


def _last_line(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


BACKEND = Stm32Backend
