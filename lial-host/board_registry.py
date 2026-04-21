"""
LIAL Board Registry -- Maps USB VID/PID pairs to board families.

Adding support for a new board family is a matter of adding a new entry to
BOARD_REGISTRY. No code changes required.

Each entry describes:
    family:         canonical family name (used to look up a flash backend)
    display_name:   human-readable label shown in CLI listings
    vid_pid:        list of (vendor_id, product_id) tuples this family matches
    probe:          which FlashBackend.probe() to call for exact chip detection
    flash_backend:  which FlashBackend.flash() to call
    firmware_ext:   expected filename suffix for this family's firmware
    variants:       list of known chip variants in this family
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BoardFamily:
    family: str
    display_name: str
    vid_pid: tuple[tuple[int, int], ...]
    probe: str
    flash_backend: str
    firmware_ext: str
    variants: tuple[str, ...] = ()


BOARD_REGISTRY: tuple[BoardFamily, ...] = (
    BoardFamily(
        family="esp32",
        display_name="Espressif ESP32 family",
        vid_pid=(
            (0x303A, 0x1001),  # Espressif USB JTAG/serial (ESP32-C3/S3/C6)
            (0x303A, 0x4001),  # Espressif USB CDC
            (0x10C4, 0xEA60),  # Silicon Labs CP2102 (common on ESP32 DevKits)
            (0x1A86, 0x7523),  # QinHeng CH340 (cheap ESP32 clones)
            (0x1A86, 0x55D4),  # QinHeng CH9102
            (0x0403, 0x6010),  # FTDI FT2232 (ESP-Prog)
        ),
        probe="esptool",
        flash_backend="esptool",
        firmware_ext=".bin",
        variants=("esp32", "esp32c3", "esp32s2", "esp32s3", "esp32c6", "esp32h2"),
    ),
    BoardFamily(
        family="rp2040",
        display_name="Raspberry Pi RP2040 / RP2350",
        vid_pid=(
            (0x2E8A, 0x0003),  # Pico BOOTSEL mode (RP2040)
            (0x2E8A, 0x000F),  # Pico BOOTSEL mode (RP2350)
            (0x2E8A, 0x0005),  # MicroPython CDC
            (0x2E8A, 0x000A),  # SDK CDC UART
            (0x2E8A, 0x000B),  # CircuitPython
        ),
        probe="picotool",
        flash_backend="uf2",
        firmware_ext=".uf2",
        variants=("rp2040", "rp2350"),
    ),
    BoardFamily(
        family="avr",
        display_name="Arduino AVR (Uno / Mega / Nano)",
        vid_pid=(
            (0x2341, 0x0043),  # Arduino Uno R3
            (0x2341, 0x0010),  # Arduino Mega 2560
            (0x2341, 0x0042),  # Arduino Mega ADK
            (0x2341, 0x0001),  # Arduino Uno (original)
            (0x2A03, 0x0043),  # Arduino.org Uno
            (0x1A86, 0x7523),  # CH340 on Nano clones (also matched by ESP32!)
        ),
        probe="avrdude",
        flash_backend="avrdude",
        firmware_ext=".hex",
        variants=("atmega328p", "atmega2560", "atmega32u4"),
    ),
    BoardFamily(
        family="stm32",
        display_name="STMicroelectronics STM32",
        vid_pid=(
            (0x0483, 0xDF11),  # ST DFU mode
            (0x0483, 0x5740),  # ST Virtual COM
            (0x0483, 0x374B),  # ST-Link V2-1
        ),
        probe="dfu-util",
        flash_backend="stm32loader",
        firmware_ext=".bin",
        variants=("stm32f103", "stm32f411", "stm32f4", "stm32h743"),
    ),
    BoardFamily(
        family="samd",
        display_name="Microchip SAMD (Arduino Zero / Feather M0-M4)",
        vid_pid=(
            (0x2341, 0x804D),  # Arduino Zero
            (0x239A, 0x800B),  # Adafruit Feather M0
            (0x239A, 0x800F),  # Adafruit Feather M4
        ),
        probe="bossac",
        flash_backend="bossac",
        firmware_ext=".bin",
        variants=("samd21", "samd51"),
    ),
)


# VID/PIDs that are ambiguous across families (e.g. CH340 appears on both
# ESP32 clones and Arduino Nano clones). When we see these, we do not pick a
# family by VID/PID alone -- we fall back to probing each candidate.
AMBIGUOUS_VID_PID: frozenset[tuple[int, int]] = frozenset({
    (0x1A86, 0x7523),
})


@dataclass
class DetectedDevice:
    """A USB device we've seen, with whatever we know about it so far."""
    port: str
    vid: int | None
    pid: int | None
    manufacturer: str | None = None
    product: str | None = None
    serial_number: str | None = None
    candidate_families: list[BoardFamily] = field(default_factory=list)
    confirmed_family: str | None = None
    variant: str | None = None
    lial_version: str | None = None  # None = no LIAL firmware detected

    @property
    def has_lial(self) -> bool:
        return self.lial_version is not None

    @property
    def is_blank(self) -> bool:
        """No LIAL firmware but we recognised the hardware family."""
        return self.lial_version is None and self.confirmed_family is not None


def family_by_name(name: str) -> BoardFamily | None:
    for fam in BOARD_REGISTRY:
        if fam.family == name:
            return fam
    return None


def family_has_variant(family: str, variant: str) -> bool:
    fam = family_by_name(family)
    return fam is not None and variant in fam.variants


def candidates_for(vid: int | None, pid: int | None) -> list[BoardFamily]:
    """Return all board families whose VID/PID list includes (vid, pid).

    Returns an empty list for unknown USB devices. Multiple families may be
    returned when the VID/PID is ambiguous (see AMBIGUOUS_VID_PID).
    """
    if vid is None or pid is None:
        return []
    matches: list[BoardFamily] = []
    for fam in BOARD_REGISTRY:
        if (vid, pid) in fam.vid_pid:
            matches.append(fam)
    return matches


def enumerate_usb_devices() -> list[DetectedDevice]:
    """Enumerate all serial/CDC ports and tag them with candidate families."""
    from serial.tools import list_ports  # pyserial

    devices: list[DetectedDevice] = []
    for port in list_ports.comports():
        dev = DetectedDevice(
            port=port.device,
            vid=port.vid,
            pid=port.pid,
            manufacturer=port.manufacturer,
            product=port.product,
            serial_number=port.serial_number,
            candidate_families=candidates_for(port.vid, port.pid),
        )
        devices.append(dev)
    return devices
