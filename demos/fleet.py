"""Virtual fleet launcher — spawns emulated MILO receivers and registers them.

Each fleet member is a real `milo-receiver` process (the same wasmi runtime,
syscall ABI, validation, and MILO-Link protocol that ships to hardware) with
a simulated peripheral backend, listening on localhost TCP. Telemetry from
the physics models is appended to a JSONL file for evidence charts.
"""

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOST_DIR = os.path.join(_REPO_ROOT, "host")
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

from devices.registry import DeviceRegistry  # noqa: E402

RECEIVER_BIN = os.path.join(_REPO_ROOT, "receiver", "target", "debug", "milo-receiver")
BASE_PORT = 9400


@dataclass
class FleetMember:
    name: str
    profile: str
    port: int
    process: subprocess.Popen
    spawned_at: float = 0.0  # host wall-clock at spawn; aligns telemetry t


class Fleet:
    """A set of emulated receivers + a DeviceRegistry connected to them."""

    def __init__(self, telemetry_path: str | None = None):
        self.members: list[FleetMember] = []
        self.registry = DeviceRegistry()
        self.telemetry_path = telemetry_path
        if telemetry_path:
            os.makedirs(os.path.dirname(telemetry_path) or ".", exist_ok=True)
            open(telemetry_path, "w").close()  # truncate

    def spawn(self, name: str, profile: str, port: int | None = None) -> FleetMember:
        if not os.path.exists(RECEIVER_BIN):
            raise RuntimeError(
                f"receiver binary not found at {RECEIVER_BIN}; run `cargo build` in receiver/"
            )
        port = port or (BASE_PORT + len(self.members))
        env = os.environ.copy()
        if self.telemetry_path:
            env["MILO_SIM_TELEMETRY"] = self.telemetry_path
        spawned_at = time.time()
        proc = subprocess.Popen(
            [RECEIVER_BIN, "--listen", str(port), "--profile", profile, "--name", name],
            env=env,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        member = FleetMember(
            name=name, profile=profile, port=port, process=proc, spawned_at=spawned_at
        )
        self.members.append(member)
        return member

    def _wait_port(self, port: int, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise TimeoutError(f"receiver on port {port} did not come up")

    def connect_all(self) -> None:
        """Wait for all members to listen, then register them (discovery)."""
        for m in self.members:
            self._wait_port(m.port)
        # The TCP transport probe above consumes the listener's single accept
        # slot momentarily; give it a beat before the real connection.
        time.sleep(0.3)
        for m in self.members:
            self.registry.register_tcp(m.name, "127.0.0.1", m.port, tags=[m.profile])

    def shutdown(self) -> None:
        self.registry.close_all()
        for m in self.members:
            if m.process.poll() is None:
                m.process.terminate()
                try:
                    m.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    m.process.kill()
        self.members.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()
