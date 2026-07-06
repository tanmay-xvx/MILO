# MILO MCP Server

Drive MILO device fleets — emulated or real hardware — from any Model Context
Protocol client (Claude Code, Claude Desktop, Cursor, or the Agent SDK). The
agent gets tools to stand up devices, read their capabilities, write firmware
to them in natural language, retask them live, and repair them in place.

## Run it

```bash
cd host
python3 -m mcp.server        # stdio JSON-RPC server
```

It speaks MCP over stdio: `initialize` → `notifications/initialized` →
`tools/list` → `tools/call`. Nothing else is required.

## Register with a client

The repo ships a ready `.mcp.json` at its root:

```json
{
  "mcpServers": {
    "milo": { "command": "python3", "args": ["-m", "mcp.server"], "cwd": "host" }
  }
}
```

- **Claude Code**: it auto-discovers `.mcp.json` in the project root. Or:
  `claude mcp add milo -- python3 -m mcp.server` (run from `host/`).
- **Claude Desktop / Cursor**: point the client's MCP config at
  `python3 -m mcp.server` with `cwd` set to this repo's `host/` directory.

Requires the emulator binary for `milo_spawn_sim`: `cd receiver && cargo build`.
For signed pushes, set `MILO_SIGNING_KEY` or run `python cli.py keygen`.

## Tools

| Tool | What the agent does |
|---|---|
| `milo_spawn_sim` | Launch an emulated device (`drone`/`conveyor`/`oven`/`arm`) with reactive physics — a whole fleet, no hardware |
| `milo_connect_tcp` | Register a Wi-Fi / TCP receiver |
| `milo_connect_serial` | Register a USB-serial ESP32-C3 / Pico |
| `milo_disconnect` | Unregister (and stop, if spawned) a device |
| `milo_devices` | List devices + hardware manifests |
| `milo_push` | Compile Rust → wasm → run on a device, wait for result |
| `milo_push_signed` | Same, Ed25519-signed (for signed-only receivers) |
| `milo_hot_swap` | Replace running firmware in place — live repair |
| `milo_set_param` | Write a parameter slot — retask without recompiling |
| `milo_query` | Device (or whole-fleet) execution status |
| `milo_stop` | Stop the running module |

## Example agent session

> "Spin up three drones, make them hover at 2 metres, then move drone-2 to 4 metres."

The agent calls `milo_spawn_sim` ×3, reads each manifest with `milo_devices`,
writes one altitude-hold `run_logic` and `milo_push`es it to all three, then
`milo_set_param(device="drone-2", slot=0, value=400)` — the running module
picks up the new target on its next loop. No recompile, no reflash.

## Safety

Every `milo_push` / `milo_hot_swap` goes through the receiver's four gates
(frame bound → optional signature → import whitelist → sandbox + fuel). The
MCP server never bypasses them; a device provisioned as signed-only rejects a
plain `milo_push` and the agent must use `milo_push_signed`. See
[`../../SECURITY.md`](../../SECURITY.md).
