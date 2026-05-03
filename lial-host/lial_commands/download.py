"""`lial download` -- interactive firmware picker + fetcher.

Flow:
    1. Fetch manifest.json from the release URL (GitHub Release asset).
    2. Show the user the list of variants.
    3. User picks one (CLI flag or interactive prompt).
    4. Download the firmware binary into ./firmware/<filename>.
    5. Print the flash command they can run themselves (or chain into `lial init`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_MANIFEST_URL = os.environ.get(
    "LIAL_MANIFEST_URL",
    "https://github.com/tanmay-xvx/LIAL/releases/latest/download/manifest.json",
)

DEFAULT_CACHE_DIR = Path(os.environ.get("LIAL_FIRMWARE_DIR", "./firmware"))


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "download",
        help="Download LIAL receiver firmware for a board",
    )
    p.add_argument("--board", help="Board variant (e.g. esp32c3). If omitted, an interactive picker runs.")
    p.add_argument("--manifest-url", default=DEFAULT_MANIFEST_URL)
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.add_argument("--verify", action="store_true", help="Verify sha256 after download")
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    try:
        manifest = fetch_manifest(args.manifest_url)
    except DownloadError as e:
        print(f"  manifest fetch failed: {e}", file=sys.stderr)
        return 1

    variants = manifest.get("variants", [])
    if not variants:
        print("  manifest contains no variants", file=sys.stderr)
        return 1

    if args.board:
        variant = _lookup_variant(variants, args.board)
        if variant is None:
            print(f"  variant '{args.board}' not in manifest; options: {[v['variant'] for v in variants]}", file=sys.stderr)
            return 1
    else:
        variant = interactive_pick(variants)
        if variant is None:
            return 1

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / variant["filename"]

    try:
        download_firmware(variant["url"], dest)
    except DownloadError as e:
        print(f"  download failed: {e}", file=sys.stderr)
        return 1

    if args.verify and variant.get("sha256"):
        actual = _sha256(dest)
        if actual != variant["sha256"]:
            print(f"  sha256 mismatch: expected {variant['sha256']}, got {actual}", file=sys.stderr)
            return 1
        print("  sha256 verified")

    print(f"  saved: {dest}")
    flash_tool = variant.get("flash_tool", "lial init")
    print(f"  to flash: {flash_tool}")
    if "flash_instructions" in variant:
        print(f"  notes: {variant['flash_instructions']}")
    return 0


class DownloadError(RuntimeError):
    pass


def fetch_manifest(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            body = resp.read()
    except urllib.error.URLError as e:
        raise DownloadError(f"{url}: {e}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise DownloadError(f"{url}: invalid JSON: {e}") from e


def download_firmware(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except urllib.error.URLError as e:
        if tmp.exists():
            tmp.unlink()
        raise DownloadError(f"{url}: {e}") from e
    os.replace(tmp, dest)


def interactive_pick(variants: list[dict]) -> dict | None:
    print()
    print("  Available LIAL receiver firmware:")
    print()
    for i, v in enumerate(variants, 1):
        size_kb = v.get("size_bytes", 0) // 1024
        label = v.get("display_name") or v["variant"]
        print(f"    {i:>2}. {label:<32} [{v['variant']}, {size_kb} KB, {v.get('family', '?')}]")
    print()
    try:
        raw = input("  choose a number (or name) -> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw:
        return None

    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(variants):
            return variants[idx]
        print(f"  out of range", file=sys.stderr)
        return None

    return _lookup_variant(variants, raw)


def _lookup_variant(variants: list[dict], name: str) -> dict | None:
    for v in variants:
        if v["variant"].lower() == name.lower():
            return v
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
