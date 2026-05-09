#!/usr/bin/env python3
"""Generate `manifest.json` from a directory of per-variant build artifacts.

Expected layout:
    <artifacts_dir>/
        milo-receiver-esp32c3/
            milo-receiver-esp32c3.bin
            milo-receiver-esp32c3.bin.sha256
            milo-receiver-esp32c3.bin.size
            milo-receiver-esp32c3.bin.meta.json   # { variant, family, filename, size_bytes, sha256 }
        milo-receiver-rp2040/
            ...

Output `manifest.json` schema:
    {
      "manifest_version": 1,
      "firmware_version": "0.3.0",
      "released_at": "2026-03-12T...Z",
      "variants": [
        {
          "variant": "esp32c3",
          "family":  "esp32",
          "display_name": "Espressif ESP32-C3",
          "filename": "milo-receiver-esp32c3.bin",
          "url":      "<release asset URL>",
          "size_bytes": 182412,
          "sha256": "...",
          "flash_tool": "esptool",
          "flash_instructions": "esptool --chip esp32c3 --port <PORT> write_flash 0x0 <FILE>"
        }
      ]
    }
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path


FAMILY_TO_FLASH_TOOL = {
    "esp32": "esptool",
    "rp2040": "uf2",
    "avr": "avrdude",
    "stm32": "stm32loader",
    "samd": "bossac",
}

FAMILY_TO_DISPLAY = {
    "esp32": "Espressif ESP32",
    "rp2040": "Raspberry Pi RP2040",
    "avr": "Arduino AVR",
    "stm32": "STMicroelectronics STM32",
    "samd": "Microchip SAMD",
}

VARIANT_DISPLAY = {
    "esp32c3": "Espressif ESP32-C3",
    "esp32s3": "Espressif ESP32-S3",
    "esp32c6": "Espressif ESP32-C6",
    "rp2040": "Raspberry Pi Pico (RP2040)",
    "rp2350": "Raspberry Pi Pico 2 (RP2350)",
    "atmega328p": "Arduino Uno / Nano (ATmega328P)",
    "stm32f103": "STM32F103 Blue Pill",
}

FLASH_INSTRUCTIONS = {
    "esptool": "python -m esptool --chip {variant} --port <PORT> write_flash 0x0 {filename}",
    "uf2": "Hold BOOTSEL, plug in the board, then drag {filename} onto the RPI-RP2 volume",
    "avrdude": "avrdude -c arduino -p {variant_short} -P <PORT> -U flash:w:{filename}:i",
    "stm32loader": "stm32loader -p <PORT> -e -w -v {filename}",
    "bossac": "bossac --port <PORT> --erase --write --verify {filename}",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts-dir", required=True, help="Directory containing per-variant build artifacts")
    ap.add_argument("--firmware-version", required=True, help="Semantic version, e.g. 0.3.0")
    ap.add_argument("--base-url", required=True, help="URL prefix for asset downloads (release download URL)")
    ap.add_argument("--output", default="manifest.json")
    args = ap.parse_args()

    root = Path(args.artifacts_dir)
    if not root.is_dir():
        print(f"artifacts dir not found: {root}", file=sys.stderr)
        return 1

    variants: list[dict] = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        meta_path = _find_meta(sub)
        if meta_path is None:
            print(f"  skip {sub.name}: no *.meta.json", file=sys.stderr)
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError as e:
            print(f"  skip {sub.name}: invalid meta.json: {e}", file=sys.stderr)
            continue

        fw_name = meta["filename"]
        fw_path = sub / fw_name
        if not fw_path.exists():
            print(f"  skip {sub.name}: firmware file {fw_name} missing", file=sys.stderr)
            continue

        variant = meta["variant"]
        family = meta["family"]
        sha = meta.get("sha256") or _sha256(fw_path)
        size = meta.get("size_bytes") or fw_path.stat().st_size
        flash_tool = FAMILY_TO_FLASH_TOOL.get(family, "unknown")

        instructions = FLASH_INSTRUCTIONS.get(flash_tool, "").format(
            variant=variant,
            variant_short=_avr_part_name(variant),
            filename=fw_name,
        )

        variants.append({
            "variant": variant,
            "family": family,
            "display_name": VARIANT_DISPLAY.get(variant, FAMILY_TO_DISPLAY.get(family, variant)),
            "filename": fw_name,
            "url": _join_url(args.base_url, fw_name),
            "size_bytes": size,
            "sha256": sha,
            "flash_tool": flash_tool,
            "flash_instructions": instructions,
        })

    if not variants:
        print("no variants found", file=sys.stderr)
        return 1

    manifest = {
        "manifest_version": 1,
        "firmware_version": args.firmware_version,
        "released_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "variants": variants,
    }

    Path(args.output).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {args.output} ({len(variants)} variant(s))")
    return 0


def _find_meta(d: Path) -> Path | None:
    matches = list(d.glob("*.meta.json"))
    return matches[0] if matches else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _join_url(base: str, filename: str) -> str:
    if not base.endswith("/"):
        base += "/"
    return base + filename


def _avr_part_name(variant: str) -> str:
    mapping = {"atmega328p": "m328p", "atmega2560": "m2560", "atmega32u4": "m32u4"}
    return mapping.get(variant, variant)


if __name__ == "__main__":
    sys.exit(main())
