"""`milo init` -- auto-detect connected boards and flash MILO firmware.

Flow:
    1. Enumerate USB devices via board_registry.enumerate_usb_devices().
    2. For each device with a candidate family, check if it already runs MILO
       (open the port briefly, look for an OP_DISCOVERY frame). If so, skip.
    3. Otherwise ask the user whether to flash each blank device.
    4. For each confirmed target, call the family's FlashBackend.probe() to
       confirm variant, download the matching firmware via `milo download`,
       and flash.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

from devices.boards import (
    BoardFamily,
    DetectedDevice,
    enumerate_usb_devices,
)
from flash import FlashBackend, get_backend
from flash.download import (
    DEFAULT_CACHE_DIR,
    DEFAULT_MANIFEST_URL,
    DownloadError,
    download_firmware,
    fetch_manifest,
)

OP_DISCOVERY = 0x01


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "init",
        help="Auto-detect connected boards and flash MILO firmware",
    )
    p.add_argument("--port", help="Only initialize this specific port")
    p.add_argument("--yes", "-y", action="store_true", help="Skip flash confirmation prompt")
    p.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL)
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.add_argument("--dry-run", action="store_true", help="Detect + report only; do not flash")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    devices = enumerate_usb_devices()
    if args.port:
        devices = [d for d in devices if d.port == args.port]
        if not devices:
            print(f"  no device found at port {args.port}", file=sys.stderr)
            return 1

    if not devices:
        print("  no USB serial devices detected")
        return 0

    print(f"  detected {len(devices)} port(s):")
    for d in devices:
        label = _device_label(d)
        print(f"    {d.port:<28} {label}")
    print()

    targets: list[tuple[DetectedDevice, BoardFamily]] = []

    for dev in devices:
        candidates = dev.candidate_families
        if not candidates:
            print(f"  skip {dev.port}: unknown VID:PID {_vidpid(dev)}")
            continue

        # Is the device already running MILO?
        milo_version = _probe_milo_firmware(dev.port)
        if milo_version is not None:
            dev.milo_version = milo_version
            print(f"  skip {dev.port}: already running MILO v{milo_version}")
            continue

        family = _pick_family(dev, candidates)
        if family is None:
            print(f"  skip {dev.port}: could not narrow candidate families")
            continue

        dev.confirmed_family = family.family
        targets.append((dev, family))

    if not targets:
        print("  nothing to flash.")
        return 0

    if args.dry_run:
        print()
        print(f"  dry-run: would flash {len(targets)} device(s):")
        for dev, family in targets:
            print(f"    {dev.port}  ->  {family.display_name}")
        return 0

    manifest = None
    for dev, family in targets:
        if not args.yes and not _confirm(f"  flash {family.display_name} on {dev.port}? [Y/n] "):
            continue

        backend = get_backend(family.flash_backend)
        if backend is None:
            print(f"  no backend registered for '{family.flash_backend}'; skipping {dev.port}", file=sys.stderr)
            continue

        probed = backend.probe(dev.port)
        if not probed.ok:
            print(f"  probe failed on {dev.port}: {probed.reason}", file=sys.stderr)
            continue
        variant = probed.variant or (family.variants[0] if family.variants else None)
        print(f"  probed {dev.port}: variant={variant}")

        if manifest is None:
            try:
                manifest = fetch_manifest(args.manifest_url)
            except DownloadError as e:
                print(f"  manifest fetch failed: {e}", file=sys.stderr)
                return 1

        variant_entry = _find_variant_in_manifest(manifest, variant)
        if variant_entry is None:
            print(f"  manifest has no firmware for variant '{variant}'", file=sys.stderr)
            continue

        cache_dir = Path(args.cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        firmware_path = cache_dir / variant_entry["filename"]

        if not firmware_path.exists():
            print(f"  downloading {variant_entry['filename']} ...")
            try:
                download_firmware(variant_entry["url"], firmware_path)
            except DownloadError as e:
                print(f"  download failed: {e}", file=sys.stderr)
                continue

        print(f"  flashing {dev.port} with {firmware_path.name} ...")
        result = backend.flash(dev.port, str(firmware_path), variant=variant)
        if not result.ok:
            print(f"  flash failed: {result.reason}", file=sys.stderr)
            continue
        print(f"  flashed {result.bytes_written} bytes in {dev.port}")

    return 0


def _device_label(d: DetectedDevice) -> str:
    vidpid = _vidpid(d)
    product = d.product or "?"
    cands = ", ".join(f.family for f in d.candidate_families) or "unknown"
    return f"{vidpid}  {product:<24}  [{cands}]"


def _vidpid(d: DetectedDevice) -> str:
    if d.vid is None or d.pid is None:
        return "?:?"
    return f"{d.vid:04x}:{d.pid:04x}"


def _confirm(prompt: str) -> bool:
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("", "y", "yes")


def _pick_family(dev: DetectedDevice, candidates: list[BoardFamily]) -> BoardFamily | None:
    if len(candidates) == 1:
        return candidates[0]
    # Ambiguous VID/PID: prefer the family whose probe() succeeds first.
    for fam in candidates:
        backend = get_backend(fam.flash_backend)
        if backend is None:
            continue
        res = backend.probe(dev.port)
        if res.ok:
            return fam
    return None


def _probe_milo_firmware(port: str, timeout_s: float = 3.0) -> str | None:
    """Send a discovery request frame and check for a MILO response."""
    try:
        import serial  # pyserial
    except ImportError:
        return None

    try:
        ser = serial.Serial(port, 115200, timeout=timeout_s)
    except (serial.SerialException, OSError):
        return None

    try:
        time.sleep(0.3)
        ser.reset_input_buffer()
        # Send a discovery request: OP_DISCOVERY (0x01) with empty payload
        discovery_req = struct.pack(">BI", OP_DISCOVERY, 0)
        ser.write(discovery_req)
        ser.flush()

        deadline = time.time() + timeout_s
        buf = b""
        while time.time() < deadline and len(buf) < 5:
            chunk = ser.read(5 - len(buf))
            if chunk:
                buf += chunk
        if len(buf) < 5:
            return None
        opcode = buf[0]
        if opcode != OP_DISCOVERY:
            return None
        plen = struct.unpack(">I", buf[1:5])[0]
        if plen > 8192:
            return None
        payload = b""
        while time.time() < deadline and len(payload) < plen:
            chunk = ser.read(plen - len(payload))
            if chunk:
                payload += chunk
        if len(payload) < plen:
            return None
        try:
            m = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return m.get("firmware_version") or m.get("version") or "present"
    finally:
        try:
            ser.close()
        except Exception:
            pass


def _find_variant_in_manifest(manifest: dict, variant: str | None) -> dict | None:
    if variant is None:
        return None
    for v in manifest.get("variants", []):
        if v.get("variant", "").lower() == variant.lower():
            return v
    return None
