"""
MILO Flash Backends

Each supported board family has a FlashBackend subclass that knows how to:
    probe()  - confirm the hardware at a given port and return the chip variant
    flash()  - write a firmware file to the device

Backends are selected by name from the BoardFamily.flash_backend field of the
board registry. The ABC intentionally has a tiny surface so new backends can
be added without touching the orchestration code.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class ProbeResult:
    """Result of probing a port for a specific family.

    If `ok` is False, `reason` should explain why (e.g. "chip not in ROM
    bootloader"). If `ok` is True, `variant` is the detected chip variant
    (e.g. "esp32c3") and `details` holds any extra diagnostics.
    """
    ok: bool
    variant: str | None = None
    reason: str | None = None
    details: dict | None = None


@dataclass
class FlashResult:
    ok: bool
    bytes_written: int = 0
    reason: str | None = None


class FlashBackend(abc.ABC):
    """Abstract base for all flash backends."""

    name: str

    @abc.abstractmethod
    def probe(self, port: str) -> ProbeResult:
        """Confirm the chip type at `port`. Does NOT flash."""

    @abc.abstractmethod
    def flash(
        self,
        port: str,
        firmware_path: str,
        variant: str | None = None,
    ) -> FlashResult:
        """Write `firmware_path` to the device at `port`."""


_BACKENDS: dict[str, FlashBackend] = {}


def register_backend(backend: FlashBackend) -> None:
    _BACKENDS[backend.name] = backend


def get_backend(name: str) -> FlashBackend | None:
    if not _BACKENDS:
        _lazy_register_all()
    return _BACKENDS.get(name)


def available_backends() -> list[str]:
    if not _BACKENDS:
        _lazy_register_all()
    return sorted(_BACKENDS.keys())


def _lazy_register_all() -> None:
    from . import esp32, rp2040, avr, stm32  # noqa: F401

    for mod in (esp32, rp2040, avr, stm32):
        backend_cls = getattr(mod, "BACKEND", None)
        if backend_cls is None:
            continue
        try:
            register_backend(backend_cls())
        except Exception as e:
            import warnings
            warnings.warn(f"failed to register backend from {mod.__name__}: {e}")
