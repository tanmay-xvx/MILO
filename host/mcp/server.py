"""MILO MCP Server — drive MILO device fleets from any MCP client.

Speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0):
`initialize` → `notifications/initialized` → `tools/list` → `tools/call`.
Any MCP-compatible agent (Claude Code, Claude Desktop, Cursor) can then
discover, program, retask, and repair MILO devices — emulated fleets or real
hardware — through natural-language-driven tool calls.

The server owns a `DeviceRegistry` and the lifecycle of any emulated devices
it spawns, so an agent can stand up a whole fleet, drive it, and tear it down
within one session. Run it directly for stdio, or import `MiloMcpServer` to
embed it.

    python -m mcp.server              # stdio server for an MCP client

Tools:
  milo_spawn_sim      launch an emulated device (drone|conveyor|oven|arm)
  milo_connect_tcp    register a device reachable over TCP (Wi-Fi/emulated)
  milo_connect_serial register a USB-serial device
  milo_disconnect     unregister (and stop, if spawned) a device
  milo_devices        list registered devices + manifests
  milo_push           compile Rust → wasm → run on a device (waits for result)
  milo_push_signed    same, Ed25519-signed (for signed-only receivers)
  milo_hot_swap       replace running code in place
  milo_set_param      write a runtime parameter slot (live retasking)
  milo_query          device execution status
  milo_stop           stop the running module
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from typing import Any

_HOST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HOST_DIR not in sys.path:
    sys.path.insert(0, _HOST_DIR)

from devices.registry import DeviceRegistry  # noqa: E402
from core.compiler import compile_rust_to_wasm  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "milo", "version": "0.9.0"}

_REPO_ROOT = os.path.dirname(_HOST_DIR)
_RECEIVER_BIN = os.path.join(_REPO_ROOT, "receiver", "target", "debug", "milo-receiver")


class MiloMcpServer:
    """Owns the device registry, spawned emulator processes, and the MCP loop."""

    def __init__(self) -> None:
        self.registry = DeviceRegistry()
        self._procs: dict[str, subprocess.Popen] = {}
        self._next_port = 9600

    # ── device lifecycle ────────────────────────────────────────────────

    def _wait_port(self, port: int, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return True
            except OSError:
                time.sleep(0.05)
        return False

    def tool_milo_spawn_sim(self, args: dict) -> dict:
        profile = args.get("profile", "drone")
        if profile not in ("drone", "conveyor", "oven", "arm"):
            return {"error": f"unknown profile '{profile}' (drone|conveyor|oven|arm)"}
        name = args.get("name") or f"{profile}-{self._next_port}"
        if self.registry.get(name):
            return {"error": f"device '{name}' already registered"}
        if not os.path.exists(_RECEIVER_BIN):
            return {"error": f"receiver binary not built (expected {_RECEIVER_BIN}); "
                             "run `cargo build` in receiver/"}
        port = self._next_port
        self._next_port += 1
        env = os.environ.copy()
        if args.get("telemetry_path"):
            env["MILO_SIM_TELEMETRY"] = args["telemetry_path"]
        proc = subprocess.Popen(
            [_RECEIVER_BIN, "--listen", str(port), "--profile", profile, "--name", name],
            env=env,
            stderr=subprocess.DEVNULL,
        )
        if not self._wait_port(port):
            proc.terminate()
            return {"error": f"emulated device '{name}' did not come up on port {port}"}
        time.sleep(0.3)
        self._procs[name] = proc
        entry = self.registry.register_tcp(name, "127.0.0.1", port, tags=[profile, "sim"])
        return {"ok": True, "device": name, "profile": profile, "port": port,
                "manifest": entry.manifest}

    def tool_milo_connect_tcp(self, args: dict) -> dict:
        name = args.get("name")
        host = args.get("host", "127.0.0.1")
        port = int(args.get("port", 9400))
        if not name:
            return {"error": "missing 'name'"}
        entry = self.registry.register_tcp(name, host, port, tags=["tcp"])
        return {"ok": True, "device": name, "manifest": entry.manifest}

    def tool_milo_connect_serial(self, args: dict) -> dict:
        name = args.get("name")
        port = args.get("port")
        if not name or not port:
            return {"error": "missing 'name' or 'port'"}
        baud = int(args.get("baud", 115200))
        entry = self.registry.register_serial(name, port, baud, tags=["serial"])
        return {"ok": True, "device": name, "manifest": entry.manifest}

    def tool_milo_disconnect(self, args: dict) -> dict:
        name = args.get("device")
        if not name or not self.registry.get(name):
            return {"error": f"device '{name}' not found"}
        self.registry.unregister(name)
        proc = self._procs.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        return {"ok": True, "disconnected": name}

    # ── control ─────────────────────────────────────────────────────────

    def tool_milo_devices(self, args: dict) -> dict:
        devices = self.registry.list_devices()
        return {"devices": devices, "count": len(devices)}

    def _require(self, name: str | None):
        if not name:
            return None, {"error": "missing 'device'"}
        entry = self.registry.get(name)
        if entry is None:
            return None, {"error": f"device '{name}' not registered"}
        return entry, None

    def tool_milo_push(self, args: dict) -> dict:
        entry, err = self._require(args.get("device"))
        if err:
            return err
        code = args.get("code")
        if not code:
            return {"error": "missing 'code'"}
        try:
            wasm = compile_rust_to_wasm(code)
        except RuntimeError as e:
            return {"error": "compilation failed", "detail": str(e)[-800:]}
        r = entry.device.push(wasm, timeout=float(args.get("timeout", 120.0)))
        return {"ok": r.ok, "logs": r.logs, "error": r.error, "wasm_size": len(wasm)}

    def tool_milo_push_signed(self, args: dict) -> dict:
        entry, err = self._require(args.get("device"))
        if err:
            return err
        code = args.get("code")
        if not code:
            return {"error": "missing 'code'"}
        try:
            from core.signing import load_signing_key, sign_wasm
        except RuntimeError as e:
            return {"error": str(e)}
        key = load_signing_key(args.get("signing_key"))
        if not key:
            return {"error": "no signing key (set MILO_SIGNING_KEY, ~/.milo/signing.key, "
                             "or pass signing_key)"}
        try:
            wasm = compile_rust_to_wasm(code)
        except RuntimeError as e:
            return {"error": "compilation failed", "detail": str(e)[-800:]}
        r = entry.device.push_signed(sign_wasm(wasm, key), timeout=float(args.get("timeout", 120.0)))
        return {"ok": r.ok, "logs": r.logs, "error": r.error, "wasm_size": len(wasm), "signed": True}

    def tool_milo_hot_swap(self, args: dict) -> dict:
        entry, err = self._require(args.get("device"))
        if err:
            return err
        code = args.get("code")
        if not code:
            return {"error": "missing 'code'"}
        try:
            wasm = compile_rust_to_wasm(code)
        except RuntimeError as e:
            return {"error": "compilation failed", "detail": str(e)[-800:]}
        r = entry.device.hot_swap(wasm, timeout=float(args.get("timeout", 120.0)))
        return {"ok": r.ok, "logs": r.logs, "error": r.error}

    def tool_milo_set_param(self, args: dict) -> dict:
        entry, err = self._require(args.get("device"))
        if err:
            return err
        slot, value = args.get("slot"), args.get("value")
        if slot is None or value is None:
            return {"error": "missing 'slot' or 'value'"}
        entry.device.set_param(int(slot), int(value))
        return {"ok": True, "slot": int(slot), "value": int(value)}

    def tool_milo_query(self, args: dict) -> dict:
        name = args.get("device", "all")
        if name == "all":
            out = {}
            for n, s in self.registry.query_all().items():
                out[n] = {"status": s.status, "running": s.running} if hasattr(s, "status") else s
            return {"devices": out}
        entry, err = self._require(name)
        if err:
            return err
        s = entry.device.query_status()
        return {"status": s.status, "running": s.running}

    def tool_milo_stop(self, args: dict) -> dict:
        entry, err = self._require(args.get("device"))
        if err:
            return err
        return entry.device.stop()

    # ── dispatch ────────────────────────────────────────────────────────

    def handlers(self) -> dict[str, Any]:
        return {
            "milo_spawn_sim": self.tool_milo_spawn_sim,
            "milo_connect_tcp": self.tool_milo_connect_tcp,
            "milo_connect_serial": self.tool_milo_connect_serial,
            "milo_disconnect": self.tool_milo_disconnect,
            "milo_devices": self.tool_milo_devices,
            "milo_push": self.tool_milo_push,
            "milo_push_signed": self.tool_milo_push_signed,
            "milo_hot_swap": self.tool_milo_hot_swap,
            "milo_set_param": self.tool_milo_set_param,
            "milo_query": self.tool_milo_query,
            "milo_stop": self.tool_milo_stop,
        }

    def call_tool(self, name: str, arguments: dict) -> tuple[dict, bool]:
        """Run a tool. Returns (result_dict, is_error)."""
        handler = self.handlers().get(name)
        if handler is None:
            return {"error": f"unknown tool: {name}"}, True
        try:
            result = handler(arguments or {})
        except Exception as e:  # never crash the server on a tool fault
            return {"error": f"{type(e).__name__}: {e}"}, True
        return result, bool(result.get("error"))

    def shutdown(self) -> None:
        self.registry.close_all()
        for proc in self._procs.values():
            if proc.poll() is None:
                proc.terminate()
        self._procs.clear()

    # ── JSON-RPC / MCP protocol ─────────────────────────────────────────

    def handle_request(self, request: dict) -> dict | None:
        """Return a JSON-RPC response, or None for notifications."""
        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            return _ok(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })

        if method in ("notifications/initialized", "initialized"):
            return None  # notification, no response

        if method == "ping":
            return _ok(req_id, {})

        if method == "tools/list":
            return _ok(req_id, {"tools": MCP_TOOLS})

        if method == "tools/call":
            params = request.get("params", {})
            result, is_error = self.call_tool(params.get("name", ""), params.get("arguments", {}))
            return _ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                "isError": is_error,
            })

        if req_id is None:
            return None  # unknown notification
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"unknown method: {method}"}}

    def serve_stdio(self, stdin=sys.stdin, stdout=sys.stdout) -> None:
        try:
            for line in stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    continue
                response = self.handle_request(request)
                if response is not None:
                    stdout.write(json.dumps(response) + "\n")
                    stdout.flush()
        finally:
            self.shutdown()


def _ok(req_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


# ── Tool manifest (advertised via tools/list and .mcp.json) ─────────────

_DEVICE_ARG = {"type": "string", "description": "Registered device name"}

MCP_TOOLS = [
    {
        "name": "milo_spawn_sim",
        "description": "Launch a new EMULATED MILO device (real receiver runtime, simulated "
                       "physics) and register it. Profiles: drone (quadcopter), conveyor (belt), "
                       "oven (reflow), arm (3-joint). Use this to stand up a fleet with no hardware.",
        "inputSchema": {"type": "object", "properties": {
            "profile": {"type": "string", "enum": ["drone", "conveyor", "oven", "arm"]},
            "name": {"type": "string", "description": "Optional device name"},
            "telemetry_path": {"type": "string", "description": "Optional JSONL telemetry file"},
        }, "required": ["profile"]},
    },
    {
        "name": "milo_connect_tcp",
        "description": "Register a MILO device reachable over TCP (Wi-Fi receiver or a "
                       "manually-started emulator).",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "host": {"type": "string"}, "port": {"type": "integer"},
        }, "required": ["name", "port"]},
    },
    {
        "name": "milo_connect_serial",
        "description": "Register a MILO device on a USB-serial port (real ESP32-C3 / Pico hardware).",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "port": {"type": "string", "description": "e.g. /dev/cu.usbmodem101"},
            "baud": {"type": "integer"},
        }, "required": ["name", "port"]},
    },
    {
        "name": "milo_disconnect",
        "description": "Unregister a device; if it was spawned by milo_spawn_sim, stop its process.",
        "inputSchema": {"type": "object", "properties": {"device": _DEVICE_ARG}, "required": ["device"]},
    },
    {
        "name": "milo_devices",
        "description": "List all registered MILO devices with their hardware manifests "
                       "(pins, buses, ADC channels, memory, wasm limits).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "milo_push",
        "description": "Compile a Rust `run_logic` body to WebAssembly and run it on a device, "
                       "waiting for the result. Use only the 12 MILO syscalls (gpio_set, gpio_get, "
                       "delay_ms, get_uptime_us, i2c_transfer, spi_transfer, uart_write, uart_read, "
                       "pwm_set, adc_read, log_msg, get_param). Read the device manifest first to "
                       "pick valid pins/channels.",
        "inputSchema": {"type": "object", "properties": {
            "device": _DEVICE_ARG,
            "code": {"type": "string", "description": "Rust source: a `#[unsafe(no_mangle)] pub "
                     "extern \"C\" fn run_logic() { ... }` plus any helpers. No std/alloc."},
            "timeout": {"type": "number"},
        }, "required": ["device", "code"]},
    },
    {
        "name": "milo_push_signed",
        "description": "Like milo_push but Ed25519-signs the module first — required by receivers "
                       "provisioned with a trusted key (MILO_REQUIRE_SIGNED). Uses MILO_SIGNING_KEY / "
                       "~/.milo/signing.key unless a signing_key is passed.",
        "inputSchema": {"type": "object", "properties": {
            "device": _DEVICE_ARG, "code": {"type": "string"},
            "signing_key": {"type": "string", "description": "Optional private key hex"},
            "timeout": {"type": "number"},
        }, "required": ["device", "code"]},
    },
    {
        "name": "milo_hot_swap",
        "description": "Stop the running module and immediately start new code in its place — live "
                       "firmware replacement with no reboot. Ideal for in-field repair/adaptation.",
        "inputSchema": {"type": "object", "properties": {
            "device": _DEVICE_ARG, "code": {"type": "string"}, "timeout": {"type": "number"},
        }, "required": ["device", "code"]},
    },
    {
        "name": "milo_set_param",
        "description": "Write a runtime parameter slot (0-7). A running module reads it via "
                       "get_param(slot) on its next loop — retask a device (or a whole fleet, one "
                       "call each) without recompiling. Slot 7 is reserved by the sim for fault "
                       "injection.",
        "inputSchema": {"type": "object", "properties": {
            "device": _DEVICE_ARG, "slot": {"type": "integer"}, "value": {"type": "integer"},
        }, "required": ["device", "slot", "value"]},
    },
    {
        "name": "milo_query",
        "description": "Query device execution status (idle/running/completed/stopped). Pass "
                       "device='all' for the whole fleet.",
        "inputSchema": {"type": "object", "properties": {"device": {"type": "string"}}},
    },
    {
        "name": "milo_stop",
        "description": "Stop the currently running module on a device.",
        "inputSchema": {"type": "object", "properties": {"device": _DEVICE_ARG}, "required": ["device"]},
    },
]


# ── Back-compat shims (older imports / tests) ───────────────────────────

def get_tools_list() -> list[dict]:
    return MCP_TOOLS


_default_server: MiloMcpServer | None = None


def _server() -> MiloMcpServer:
    global _default_server
    if _default_server is None:
        _default_server = MiloMcpServer()
    return _default_server


def handle_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Module-level tool dispatch against a shared server (legacy API)."""
    result, _ = _server().call_tool(tool_name, arguments)
    return result


if __name__ == "__main__":
    MiloMcpServer().serve_stdio()
