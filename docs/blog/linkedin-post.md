# LinkedIn post (copy below the rule; attach docs/site/assets/milo-cover.png)

---

I watched GPT-4o repair a drone. Mid-flight. In 6.9 seconds.

For the past few months I've been building MILO, an open-source project named
after my golden retriever: you describe a task in plain English, an LLM writes
the firmware, and the device runs it inside a sandbox seconds later.

This month it grew up. Three things I'm proud of:

🛰 Simulation that pushes back. I can't buy a drone swarm, so I built emulated
devices that run the *actual* production runtime — same interpreter, same
protocol — over physics models. When I degraded one drone's motor, it didn't
crash; it drooped 1.9 m below altitude, exactly as control theory predicts.
GPT-4o wrote a PI controller and MILO hot-swapped it onto the flying drone.
Recovery in 9.5 s. Same story in a factory cell: the model diagnosed a
drifting oven from telemetry and replaced its firmware between production
cycles. Every number is measured and committed; one command reruns it.

🤖 MCP support. MILO now ships a full Model Context Protocol server, so any
agent (Claude Code, Cursor...) can spawn devices, read their capabilities,
push firmware, and retask fleets through typed tool calls. "Spin up three
drones and hover them at two meters" is now a valid deployment plan.

🔒 The unsexy part that makes it real: Ed25519-signed bytecode, a 12-syscall
import whitelist, fuel-metered 64 KB sandboxes, frame-size caps against
allocation attacks, and CI that ships flashable images for ESP32-C3 and
Raspberry Pi Pico on every commit. Nothing the model emits is trusted — and
that's exactly why you can let it be creative.

The lesson that stuck with me: don't try to make the AI trustworthy. Draw the
trust boundary below it, and its creativity becomes an asset instead of a risk.

Code, evidence, and the firmware GPT-4o wrote (verbatim):
https://github.com/tanmay-xvx/MILO

#embedded #AI #LLM #IoT #Rust #WebAssembly #MCP #robotics #opensource
