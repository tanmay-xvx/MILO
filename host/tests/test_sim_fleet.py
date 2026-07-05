"""Smoke tests for the virtual fleet emulator (TCP sim receivers).

Requires the std receiver binary (`cargo build` in receiver/); skipped when
it is absent so plain pytest runs stay green on hosts without a Rust
toolchain.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

HOST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HOST_DIR)
RECEIVER = os.path.join(REPO_ROOT, "receiver", "target", "debug", "milo-receiver")

sys.path.insert(0, HOST_DIR)

from core.transport import TcpTransport  # noqa: E402
from devices.device import MiloDevice  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.exists(RECEIVER), reason="std receiver binary not built"
)

PORT = 9911


@pytest.fixture()
def sim_device():
    proc = subprocess.Popen(
        [RECEIVER, "--listen", str(PORT), "--profile", "oven", "--name", "oven-test"],
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    time.sleep(0.3)
    dev = MiloDevice(TcpTransport("127.0.0.1", PORT), name="oven-test")
    try:
        yield dev
    finally:
        dev.close()
        proc.terminate()
        proc.wait(timeout=5)


def test_discovery_manifest(sim_device):
    m = sim_device.discover()
    assert m["board"] == "sim-oven"
    assert m["name"] == "oven-test"
    assert "get_param" in m["alphabet"]
    assert len(m["alphabet"]) == 12


def test_status_and_params_while_idle(sim_device):
    sim_device.discover()
    st = sim_device.query_status()
    assert st.status == "idle" and not st.running
    sim_device.set_param(2, 1200)  # no response expected; must not wedge
    st2 = sim_device.query_status()
    assert st2.status == "idle"


def test_import_validation_rejects_unknown_syscall(sim_device):
    sim_device.discover()
    # Minimal wasm importing a non-Alphabet function "env.evil".
    # (module (import "env" "evil" (func)) (func (export "run_logic")))
    wasm = bytes.fromhex(
        "0061736d01000000"  # magic + version
        "010401600000"  # type: () -> ()
        "020c0103656e76046576696c0000"  # import env.evil (func type 0)
        "03020100"  # function section: 1 func, type 0
        "070d010972756e5f6c6f6769630001"  # export "run_logic" (func 1)
        "0a0401020 00b".replace(" ", "")  # code: empty body
    )
    result = sim_device.push(wasm, timeout=10.0)
    assert not result.ok
    assert "evil" in (result.error or "")
