"""End-to-end signing + frame-cap tests against a signed-only sim receiver.

Skipped when the receiver binary or `cryptography` is unavailable.
"""

import os
import socket
import struct
import subprocess
import sys
import time

import pytest

HOST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HOST_DIR)
RECEIVER = os.path.join(REPO_ROOT, "receiver", "target", "debug", "milo-receiver")

sys.path.insert(0, HOST_DIR)

crypto = pytest.importorskip("cryptography")

from core.compiler import compile_rust_to_wasm  # noqa: E402
from core.signing import generate_keypair, public_key_hex, sign_wasm  # noqa: E402
from core.transport import TcpTransport, OP_BYTECODE_PUSH, MAX_FRAME_LEN  # noqa: E402
from devices.device import MiloDevice  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.exists(RECEIVER), reason="std receiver binary not built"
)

DRIVER = (
    '#[unsafe(no_mangle)]\n'
    'pub extern "C" fn run_logic() { unsafe {\n'
    '  let m = b"signed ok"; log_msg(m.as_ptr() as u32, m.len() as u32);\n'
    '} }'
)


@pytest.fixture(scope="module")
def signed_receiver():
    priv, pub = generate_keypair()
    assert public_key_hex(priv) == pub
    env = os.environ.copy()
    env["MILO_TRUSTED_KEY"] = pub
    env["MILO_REQUIRE_SIGNED"] = "1"
    port = 9522
    proc = subprocess.Popen(
        [RECEIVER, "--listen", str(port), "--profile", "oven", "--name", "sec"],
        env=env,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    time.sleep(0.3)
    try:
        yield priv, port
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _device(port):
    d = MiloDevice(TcpTransport("127.0.0.1", port), name="sec")
    d.discover()
    return d


def test_unsigned_rejected_under_policy(signed_receiver):
    _priv, port = signed_receiver
    d = _device(port)
    try:
        wasm = compile_rust_to_wasm(DRIVER)
        r = d.push(wasm, timeout=15)
        assert not r.ok
        assert "requires signed" in (r.error or "")
    finally:
        d.close()


def test_valid_signature_accepted(signed_receiver):
    priv, port = signed_receiver
    d = _device(port)
    try:
        wasm = compile_rust_to_wasm(DRIVER)
        r = d.push_signed(sign_wasm(wasm, priv), timeout=15)
        assert r.ok, r.error
        assert "signed ok" in r.logs
    finally:
        d.close()


def test_tampered_module_rejected(signed_receiver):
    priv, port = signed_receiver
    d = _device(port)
    try:
        wasm = compile_rust_to_wasm(DRIVER)
        payload = bytearray(sign_wasm(wasm, priv))
        payload[-1] ^= 0xFF
        r = d.push_signed(bytes(payload), timeout=15)
        assert not r.ok
        assert "signature" in (r.error or "").lower()
    finally:
        d.close()


def test_oversized_frame_rejected(signed_receiver):
    """A length prefix over MAX_FRAME_LEN must not wedge or OOM the receiver."""
    _priv, port = signed_receiver
    import socket as _s

    sock = _s.create_connection(("127.0.0.1", port), timeout=2)
    try:
        # Claim a 1 GB payload, send only a few bytes.
        sock.sendall(struct.pack(">BI", OP_BYTECODE_PUSH, MAX_FRAME_LEN + 1) + b"\x00\x61\x73\x6d")
        sock.settimeout(1.0)
        try:
            sock.recv(16)  # connection should be dropped, no giant allocation
        except (TimeoutError, _s.timeout, ConnectionError, OSError):
            pass
    finally:
        sock.close()

    # Receiver must still be alive and serving new connections.
    time.sleep(0.3)
    d = _device(port)
    try:
        st = d.query_status()
        assert st.status in ("idle", "running", "completed", "stopped")
    finally:
        d.close()
