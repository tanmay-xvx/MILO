"""
MILO Device Registry — manages multiple connected MILO receivers.

Tracks devices by name, their manifests, transport handles, and status.
Supports parallel Wasm push to multiple devices and LLM device routing.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from devices.device import DeviceStatus, ExecResult, MiloDevice
from core.transport import MiloTransport, SerialTransport, TcpTransport


@dataclass
class RegisteredDevice:
    """A device registered in the MILO device registry."""

    name: str
    device: MiloDevice
    manifest: dict | None = None
    last_status: DeviceStatus | None = None
    tags: list[str] = field(default_factory=list)


class DeviceRegistry:
    """Registry for managing multiple MILO receivers.

    Provides device lookup by name, parallel push, and status aggregation.
    """

    def __init__(self):
        self._devices: dict[str, RegisteredDevice] = {}

    @property
    def devices(self) -> dict[str, RegisteredDevice]:
        return self._devices

    def register(self, name: str, transport: MiloTransport, tags: list[str] | None = None) -> RegisteredDevice:
        """Register a new device with the given transport."""
        device = MiloDevice(transport, name=name)
        manifest = device.discover(timeout=5.0)

        entry = RegisteredDevice(
            name=name,
            device=device,
            manifest=manifest,
            tags=tags or [],
        )
        self._devices[name] = entry
        return entry

    def register_serial(self, name: str, port: str, baud: int = 115200, tags: list[str] | None = None) -> RegisteredDevice:
        """Register a device connected via USB serial."""
        transport = SerialTransport(port, baud)
        return self.register(name, transport, tags)

    def register_tcp(self, name: str, host: str, port: int = 9100, tags: list[str] | None = None) -> RegisteredDevice:
        """Register a device connected via TCP/WiFi."""
        transport = TcpTransport(host, port)
        return self.register(name, transport, tags)

    def unregister(self, name: str) -> None:
        """Remove a device from the registry and close its transport."""
        if name in self._devices:
            self._devices[name].device.close()
            del self._devices[name]

    def get(self, name: str) -> RegisteredDevice | None:
        """Get a registered device by name."""
        return self._devices.get(name)

    def list_devices(self) -> list[dict[str, Any]]:
        """List all registered devices with their manifests."""
        result = []
        for name, entry in self._devices.items():
            result.append({
                "name": name,
                "connected": entry.device.is_connected,
                "manifest": entry.manifest,
                "tags": entry.tags,
            })
        return result

    def push_to(self, name: str, wasm_bytes: bytes, timeout: float = 120.0) -> ExecResult:
        """Push Wasm bytecode to a specific device."""
        entry = self._devices.get(name)
        if entry is None:
            raise KeyError(f"device '{name}' not registered")
        return entry.device.push(wasm_bytes, timeout=timeout)

    def push_to_all(self, wasm_bytes: bytes, timeout: float = 120.0) -> dict[str, ExecResult]:
        """Push bytecode to all registered devices in parallel.

        One thread per device: transports are independent connections, and a
        slow or dead device must not stall the rest of the fleet.
        """
        connected = [
            (name, entry.device)
            for name, entry in self._devices.items()
            if entry.device.is_connected
        ]
        if not connected:
            return {}

        def _push(device: MiloDevice) -> ExecResult:
            try:
                return device.push(wasm_bytes, timeout=timeout)
            except Exception as e:
                return ExecResult(ok=False, logs=[], error=str(e))

        with ThreadPoolExecutor(max_workers=len(connected)) as pool:
            futures = {name: pool.submit(_push, dev) for name, dev in connected}
            return {name: fut.result() for name, fut in futures.items()}

    def push_async_to_all(self, wasm_bytes: bytes) -> list[str]:
        """Fire-and-forget push to every connected device (collect results
        later with `wait_all_results`). Returns the device names pushed to."""
        pushed = []
        for name, entry in self._devices.items():
            if entry.device.is_connected:
                entry.device.push_async(wasm_bytes)
                pushed.append(name)
        return pushed

    def wait_all_results(self, timeout: float = 120.0) -> dict[str, ExecResult]:
        """Collect one execution result from every connected device (parallel)."""
        connected = [
            (name, entry.device)
            for name, entry in self._devices.items()
            if entry.device.is_connected
        ]
        if not connected:
            return {}

        def _wait(device: MiloDevice) -> ExecResult:
            try:
                return device.wait_result(timeout=timeout)
            except Exception as e:
                return ExecResult(ok=False, logs=[], error=str(e))

        with ThreadPoolExecutor(max_workers=len(connected)) as pool:
            futures = {name: pool.submit(_wait, dev) for name, dev in connected}
            return {name: fut.result() for name, fut in futures.items()}

    def broadcast_param(self, slot: int, value: int) -> float:
        """Set a parameter slot on every connected device.

        Returns the wall-clock seconds the whole broadcast took — running
        modules pick the value up on their next `get_param` call, so this is
        the fleet-wide "formation change" latency.
        """
        start = time.perf_counter()
        for entry in self._devices.values():
            if entry.device.is_connected:
                entry.device.set_param(slot, value)
        return time.perf_counter() - start

    def stop_all(self) -> dict[str, dict]:
        """Stop execution on all registered devices."""
        results = {}
        for name, entry in self._devices.items():
            if entry.device.is_connected:
                try:
                    results[name] = entry.device.stop()
                except Exception as e:
                    results[name] = {"error": str(e)}
        return results

    def query_all(self) -> dict[str, DeviceStatus | dict]:
        """Query status of all registered devices."""
        results = {}
        for name, entry in self._devices.items():
            if entry.device.is_connected:
                try:
                    status = entry.device.query_status()
                    entry.last_status = status
                    results[name] = status
                except Exception as e:
                    results[name] = {"error": str(e)}
        return results

    def close_all(self) -> None:
        """Close all device connections."""
        for entry in self._devices.values():
            entry.device.close()
        self._devices.clear()

    def get_manifests_summary(self) -> str:
        """Get a JSON summary of all device manifests (for LLM system prompts)."""
        summary = []
        for name, entry in self._devices.items():
            summary.append({
                "name": name,
                "manifest": entry.manifest,
            })
        return json.dumps(summary, indent=2)
