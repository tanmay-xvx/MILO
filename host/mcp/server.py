"""
MILO MCP Server — expose MILO as Model Context Protocol tools.

This server allows any MCP-compatible LLM agent (Claude, Cursor, etc.)
to discover, program, and control MILO hardware devices via tool calls.

Tools exposed:
  - milo_devices: List connected devices and their capabilities
  - milo_push: Compile and push Rust code to a named device
  - milo_stop: Stop execution on a device
  - milo_query: Get device status
  - milo_set_param: Set a runtime parameter on a device
  - milo_hot_swap: Replace running code on a device
"""

import json
import sys
from typing import Any

from devices.registry import DeviceRegistry
from core.compiler import compile_rust_to_wasm

# Global registry instance
registry = DeviceRegistry()


def handle_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route MCP tool calls to the appropriate handler."""
    handlers = {
        "milo_devices": tool_milo_devices,
        "milo_push": tool_milo_push,
        "milo_stop": tool_milo_stop,
        "milo_query": tool_milo_query,
        "milo_set_param": tool_milo_set_param,
        "milo_hot_swap": tool_milo_hot_swap,
    }

    handler = handlers.get(tool_name)
    if handler is None:
        return {"error": f"unknown tool: {tool_name}"}

    try:
        return handler(arguments)
    except Exception as e:
        return {"error": str(e)}


def tool_milo_devices(args: dict) -> dict:
    """List all connected MILO devices with their hardware manifests."""
    devices = registry.list_devices()
    return {"devices": devices, "count": len(devices)}


def tool_milo_push(args: dict) -> dict:
    """Compile Rust code and push to a named device.

    Arguments:
      - device: str — name of the target device
      - code: str — Rust source code for the Wasm module
    """
    device_name = args.get("device")
    code = args.get("code")

    if not device_name:
        return {"error": "missing 'device' argument"}
    if not code:
        return {"error": "missing 'code' argument"}

    entry = registry.get(device_name)
    if entry is None:
        return {"error": f"device '{device_name}' not found in registry"}

    try:
        wasm_bytes = compile_rust_to_wasm(code)
    except RuntimeError as e:
        return {"error": f"compilation failed: {e}"}

    result = entry.device.push(wasm_bytes)
    return {
        "ok": result.ok,
        "logs": result.logs,
        "error": result.error,
        "wasm_size": len(wasm_bytes),
    }


def tool_milo_stop(args: dict) -> dict:
    """Stop execution on a named device.

    Arguments:
      - device: str — name of the target device
    """
    device_name = args.get("device")
    if not device_name:
        return {"error": "missing 'device' argument"}

    entry = registry.get(device_name)
    if entry is None:
        return {"error": f"device '{device_name}' not found"}

    return entry.device.stop()


def tool_milo_query(args: dict) -> dict:
    """Query status of a named device.

    Arguments:
      - device: str — name of the target device (or "all" for all devices)
    """
    device_name = args.get("device", "all")

    if device_name == "all":
        statuses = registry.query_all()
        return {
            name: {"status": s.status, "running": s.running}
            if hasattr(s, "status")
            else s
            for name, s in statuses.items()
        }

    entry = registry.get(device_name)
    if entry is None:
        return {"error": f"device '{device_name}' not found"}

    status = entry.device.query_status()
    return {"status": status.status, "running": status.running}


def tool_milo_set_param(args: dict) -> dict:
    """Set a runtime parameter on a device.

    Arguments:
      - device: str — name of the target device
      - slot: int — parameter slot (0-7)
      - value: int — parameter value (u32)
    """
    device_name = args.get("device")
    slot = args.get("slot")
    value = args.get("value")

    if not device_name:
        return {"error": "missing 'device' argument"}
    if slot is None or value is None:
        return {"error": "missing 'slot' or 'value' argument"}

    entry = registry.get(device_name)
    if entry is None:
        return {"error": f"device '{device_name}' not found"}

    entry.device.set_param(int(slot), int(value))
    return {"ok": True, "slot": slot, "value": value}


def tool_milo_hot_swap(args: dict) -> dict:
    """Stop current code and push new code to a device.

    Arguments:
      - device: str — name of the target device
      - code: str — new Rust source code
    """
    device_name = args.get("device")
    code = args.get("code")

    if not device_name:
        return {"error": "missing 'device' argument"}
    if not code:
        return {"error": "missing 'code' argument"}

    entry = registry.get(device_name)
    if entry is None:
        return {"error": f"device '{device_name}' not found"}

    try:
        wasm_bytes = compile_rust_to_wasm(code)
    except RuntimeError as e:
        return {"error": f"compilation failed: {e}"}

    result = entry.device.hot_swap(wasm_bytes)
    return {
        "ok": result.ok,
        "logs": result.logs,
        "error": result.error,
    }


# MCP tool definitions for integration with mcp.json
MCP_TOOLS = [
    {
        "name": "milo_devices",
        "description": "List all connected MILO hardware devices and their capabilities (pins, buses, memory).",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "milo_push",
        "description": "Compile Rust code to WebAssembly and push it to a specific MILO device for execution. The code should use MILO syscalls (gpio_set, delay_ms, log_msg, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Name of the target device"},
                "code": {"type": "string", "description": "Rust source code for the Wasm module"},
            },
            "required": ["device", "code"],
        },
    },
    {
        "name": "milo_stop",
        "description": "Stop the currently running program on a MILO device.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Name of the target device"},
            },
            "required": ["device"],
        },
    },
    {
        "name": "milo_query",
        "description": "Query the execution status of a MILO device (idle, running, completed, stopped).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device name or 'all'"},
            },
        },
    },
    {
        "name": "milo_set_param",
        "description": "Set a runtime parameter on a MILO device. The running Wasm module can read parameters via get_param(slot). Useful for adjusting behavior without redeployment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Name of the target device"},
                "slot": {"type": "integer", "description": "Parameter slot (0-7)"},
                "value": {"type": "integer", "description": "Parameter value (u32)"},
            },
            "required": ["device", "slot", "value"],
        },
    },
    {
        "name": "milo_hot_swap",
        "description": "Stop the current program and immediately start new code on a MILO device. Enables live code replacement without device reboot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Name of the target device"},
                "code": {"type": "string", "description": "New Rust source code"},
            },
            "required": ["device", "code"],
        },
    },
]


def get_tools_list() -> list[dict]:
    """Return the MCP tools manifest."""
    return MCP_TOOLS


if __name__ == "__main__":
    # Simple stdio-based MCP server loop (JSON-RPC over stdin/stdout)
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id")

        if method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": MCP_TOOLS},
            }
        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            result = handle_tool_call(tool_name, arguments)
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"unknown method: {method}"},
            }

        print(json.dumps(response), flush=True)
