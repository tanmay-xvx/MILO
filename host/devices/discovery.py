"""
MILO Device Discovery via mDNS and BLE.

Discovers already-flashed MILO receivers on the local network using mDNS
(service type `_milo._tcp.local.`) or via BLE advertising.
"""

import time
from dataclasses import dataclass, field


@dataclass
class DiscoveredDevice:
    """A MILO receiver found via network discovery."""

    name: str
    host: str
    port: int
    board: str = ""
    family: str = ""
    firmware_version: str = ""
    transport: str = "tcp"
    properties: dict = field(default_factory=dict)


MILO_MDNS_SERVICE = "_milo._tcp.local."


def discover_mdns(timeout: float = 5.0) -> list[DiscoveredDevice]:
    """Discover MILO devices via mDNS. Requires `zeroconf` package.

    Returns a list of devices found within the timeout period.
    """
    try:
        from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange
    except ImportError:
        print("  [discovery] `zeroconf` package not installed. Install with: pip install zeroconf")
        return []

    devices: list[DiscoveredDevice] = []

    class Listener:
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if info is None:
                return
            addresses = info.parsed_addresses()
            if not addresses:
                return

            props = {}
            if info.properties:
                for k, v in info.properties.items():
                    key = k.decode() if isinstance(k, bytes) else k
                    val = v.decode() if isinstance(v, bytes) else str(v)
                    props[key] = val

            dev = DiscoveredDevice(
                name=name.replace(f".{MILO_MDNS_SERVICE}", ""),
                host=addresses[0],
                port=info.port or 9100,
                board=props.get("board", ""),
                family=props.get("family", ""),
                firmware_version=props.get("version", ""),
                properties=props,
            )
            devices.append(dev)

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

    zc = Zeroconf()
    listener = Listener()
    browser = ServiceBrowser(zc, MILO_MDNS_SERVICE, listener)

    time.sleep(timeout)

    browser.cancel()
    zc.close()

    return devices


def discover_all(timeout: float = 5.0) -> list[DiscoveredDevice]:
    """Run all discovery methods and return combined results."""
    devices = discover_mdns(timeout=timeout)
    return devices
