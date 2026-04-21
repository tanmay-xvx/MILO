"""Flash backend for Arduino AVR boards (Uno, Mega, Nano).

Uses avrdude -- the de-facto standard AVR programmer. The embedded
`arduinobootloader` Python library is an alternative but avrdude is
universally available on any system that has the Arduino IDE or PlatformIO
installed, and ships in most package managers.

Chip variant -> avrdude -p flag:
    atmega328p  -> m328p   (Uno, Nano)
    atmega2560  -> m2560   (Mega 2560)
    atmega32u4  -> m32u4   (Leonardo, Micro)
"""

from __future__ import annotations

import os
import shutil
import subprocess

from . import FlashBackend, FlashResult, ProbeResult


_VARIANT_TO_AVRDUDE: dict[str, tuple[str, str]] = {
    "atmega328p": ("m328p", "arduino"),
    "atmega2560": ("m2560", "wiring"),
    "atmega32u4": ("m32u4", "avr109"),
}


class AvrBackend(FlashBackend):
    name = "avrdude"

    def __init__(self, avrdude_cmd: str | None = None) -> None:
        self._cmd = avrdude_cmd or shutil.which("avrdude") or "avrdude"

    def probe(self, port: str) -> ProbeResult:
        # avrdude's safest probe is a chip signature read. Try atmega328p first
        # (the overwhelmingly most common Arduino board), fall back otherwise.
        for variant, (part, programmer) in _VARIANT_TO_AVRDUDE.items():
            try:
                result = subprocess.run(
                    [self._cmd, "-c", programmer, "-p", part, "-P", port, "-n"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
            except FileNotFoundError as e:
                return ProbeResult(ok=False, reason=f"avrdude not installed: {e}")
            except subprocess.TimeoutExpired:
                continue

            stderr = result.stderr.lower()
            if "device signature" in stderr and "0x000000" not in stderr:
                return ProbeResult(ok=True, variant=variant, details={"programmer": programmer})

        return ProbeResult(ok=False, reason="no AVR chip responded; check cable / bootloader")

    def flash(
        self,
        port: str,
        firmware_path: str,
        variant: str | None = None,
    ) -> FlashResult:
        if not os.path.exists(firmware_path):
            return FlashResult(ok=False, reason=f"firmware not found: {firmware_path}")

        if variant is None or variant not in _VARIANT_TO_AVRDUDE:
            probed = self.probe(port)
            if not probed.ok:
                return FlashResult(ok=False, reason=f"cannot determine AVR variant: {probed.reason}")
            variant = probed.variant

        part, programmer = _VARIANT_TO_AVRDUDE[variant]
        cmd = [
            self._cmd,
            "-c", programmer,
            "-p", part,
            "-P", port,
            "-b", "115200",
            "-D",
            "-U", f"flash:w:{firmware_path}:i",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError as e:
            return FlashResult(ok=False, reason=f"avrdude not installed: {e}")
        except subprocess.TimeoutExpired:
            return FlashResult(ok=False, reason="avrdude write timed out")

        if result.returncode != 0:
            return FlashResult(ok=False, reason=_last_line(result.stderr) or "avrdude failed")

        return FlashResult(ok=True, bytes_written=os.path.getsize(firmware_path))


def _last_line(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


BACKEND = AvrBackend
