"""MCP protocol + tool tests for the MILO MCP server.

Protocol-level tests (handshake, tools/list) run everywhere. The tool tests
that actually drive a device spawn an emulated receiver and are skipped when
the receiver binary is not built.
"""

import json
import os
import subprocess
import sys

import pytest

HOST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(HOST_DIR)
RECEIVER = os.path.join(REPO_ROOT, "receiver", "target", "debug", "milo-receiver")

sys.path.insert(0, HOST_DIR)

from mcp.server import MiloMcpServer, MCP_TOOLS  # noqa: E402


# ── in-process protocol tests ───────────────────────────────────────────

def test_initialize_handshake():
    srv = MiloMcpServer()
    resp = srv.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}
    )
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "milo"
    assert "protocolVersion" in resp["result"]
    assert resp["result"]["capabilities"]["tools"] is not None
    srv.shutdown()


def test_initialized_notification_has_no_response():
    srv = MiloMcpServer()
    assert srv.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    srv.shutdown()


def test_tools_list_shape():
    srv = MiloMcpServer()
    resp = srv.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = resp["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"milo_spawn_sim", "milo_push", "milo_hot_swap", "milo_set_param",
            "milo_push_signed", "milo_devices"} <= names
    for t in tools:  # every tool is well-formed
        assert t["inputSchema"]["type"] == "object"
        assert isinstance(t["description"], str) and len(t["description"]) > 20
    srv.shutdown()


def test_unknown_method_errors():
    srv = MiloMcpServer()
    resp = srv.handle_request({"jsonrpc": "2.0", "id": 3, "method": "bogus/method"})
    assert resp["error"]["code"] == -32601
    srv.shutdown()


def test_unknown_tool_is_error_result():
    srv = MiloMcpServer()
    resp = srv.handle_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}}
    )
    assert resp["result"]["isError"] is True
    srv.shutdown()


# ── end-to-end via stdio subprocess (needs the receiver) ────────────────

@pytest.mark.skipif(not os.path.exists(RECEIVER), reason="std receiver binary not built")
def test_stdio_spawn_and_push():
    """Full MCP session over stdio: initialize → spawn sim → push → devices."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp.server"],
        cwd=HOST_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )

    def rpc(obj, expect_response=True):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()
        if not expect_response:
            return None
        return json.loads(proc.stdout.readline())

    try:
        init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert init["result"]["serverInfo"]["name"] == "milo"
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)

        spawn = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "milo_spawn_sim",
                                "arguments": {"profile": "oven", "name": "oven-mcp"}}})
        payload = json.loads(spawn["result"]["content"][0]["text"])
        assert payload.get("ok"), payload
        assert payload["manifest"]["board"] == "sim-oven"

        push = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "milo_push",
                               "arguments": {"device": "oven-mcp", "code":
                                   '#[unsafe(no_mangle)] pub extern "C" fn run_logic() '
                                   '{ unsafe { let m=b"mcp ok"; log_msg(m.as_ptr() as u32, '
                                   'm.len() as u32);} }'}}})
        pr = json.loads(push["result"]["content"][0]["text"])
        assert pr["ok"], pr
        assert "mcp ok" in pr["logs"]

        devs = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "milo_devices", "arguments": {}}})
        dr = json.loads(devs["result"]["content"][0]["text"])
        assert dr["count"] == 1 and dr["devices"][0]["name"] == "oven-mcp"
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
