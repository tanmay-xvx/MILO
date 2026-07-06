# I let GPT-4o write firmware for a drone swarm. Then I made it safe enough to mean it.

*Cover image: `docs/site/assets/milo-cover.png` · charts: `docs/blog/assets/*.svg`*

---

My dog is named Milo. So is my side project, because both of them do the same
thing: you say something in plain English, and they go make it happen in the
physical world. One of them sheds less.

MILO started as a question I couldn't let go of: LLMs are terrifyingly good at
writing code, and microcontrollers are everywhere, so why does getting the
first to run on the second still feel like 2005? You write C, you flash over a
cable, you stare at a serial console, you repeat. The feedback loop is measured
in coffee breaks.

The answer I landed on is a small piece of infrastructure: the LLM writes Rust,
the host compiles it to a WebAssembly module about a kilobyte in size, and the
device runs it inside a sandbox, streamed over a five-byte-header protocol that
works over anything that moves bytes. The whole hardware interface is twelve
syscalls. Not twelve categories. Twelve functions, total. That constraint turns
out to be the entire trick, because a language model that can only touch the
world through twelve auditable calls is a language model you can actually
reason about.

For months this was a nice demo. You'd type "blink the LED three times," watch
GPT-4o produce Rust, and two seconds later an LED on my desk would blink. Fun,
honest, and completely unconvincing as anything more than a toy. This post is
about the three things that changed that: simulation, MCP, and the boring
industrial work nobody puts in demos.

## The problem with hardware demos is hardware

I wanted to show MILO flying a drone swarm. I own zero drones. I wanted to
show it running a factory cell. You can guess how many factories I own.

The usual move here is to fake it with a mock that returns canned values, and
everyone can smell it. A mock never pushes back. So instead I built emulated
devices that are *the actual receiver* — the same Rust runtime, the same wasmi
interpreter, the same validation and framing that ship to a real ESP32 —
where only the bottom layer, the peripherals, is replaced by small physics
models. Motors produce thrust. Thrust fights gravity and drag. Heaters heat
with a first-order lag. Belts move items past a photo-sensor that actually
pulses.

The distinction matters more than it sounds. When the LLM's code is wrong in
this world, it doesn't get a polite error. It gets consequences.

My favorite example: I gave five emulated quadcopters a proportional altitude
controller and then quietly degraded one motor to 40% thrust. The drone didn't
crash. It *drooped* — settled 1.9 meters below its commanded altitude and sat
there, exactly like the control theory textbook says a P-controller under a
thrust deficit will. The physics did that, not a script I wrote to make a demo
look good.

And then the good part. The host noticed, described the symptom to GPT-4o, and
asked for a fix. 6.9 seconds later the model handed back a
proportional-integral controller — 975 bytes compiled, first try — and MILO
**hot-swapped it onto the drone mid-flight**. No landing. No reflash. The
drone climbed back into formation over the next nine seconds while its four
neighbors held position, oblivious. The whole run, takeoff to five clean
landings, is 48 seconds, and every number in this paragraph is measured from
telemetry committed to the repo. You can rerun it with one command and get
your own numbers.

The factory scenario is the same idea with higher stakes. A reflow oven runs
naive open-loop vendor firmware, the kind that sets a fixed heater duty and
hopes. I drift the heater element 35% hot without telling anyone. The control
station catches the oven crossing its 165°C limit, sends the telemetry to
GPT-4o, and here's the part I keep retelling: the model correctly concluded
that no set-point change would fix this. The *firmware* was wrong. It wrote a
closed-loop controller, 835 bytes in 4.7 seconds, which got pushed between
production cycles. The oven pulled back inside limits in one cycle and held
148°C for the rest of the shift on a heater that no longer matched its spec.

A while later an item jams the conveyor. Belt at full duty, zero items past
the sensor. The station halts the belt, dispatches the pick arm on a
clear-sweep — limit switches confirm the travel — and production resumes 4.1
seconds after detection. Nobody was in the loop. Nobody needed to be.

## MCP, or: giving every agent hands

The demos above are orchestrated by Python scripts, which is fine for
reproducibility and useless for the future I actually care about. The
interesting version is: your AI agent, whichever one you use, discovers a
fleet of devices and drives them itself.

That's what the Model Context Protocol turned out to be for. MILO now ships a
full MCP server, and the integration is one small JSON file at the repo root.
Claude Code finds it on its own. After that, the agent has eleven typed tools:
spawn an emulated device, register a real board over USB, read a device's
hardware manifest, compile-and-push firmware, hot-swap it, retask a running
module through parameter slots, query, stop.

Which means this sentence is now a valid deployment plan:

> "Spin up three drones, hover them at two meters, then move drone-2 to four."

The agent spawns three emulated drones, reads their manifests, writes one
altitude-hold controller, pushes it to all three, and pokes a single parameter
on drone-2. I've watched Claude do exactly this. There is something quietly
absurd about watching one AI write firmware and a protocol hand it to the
hardware while you drink tea, and I mean absurd in the way that eventually
becomes normal.

## The unsexy part, which is the actual point

Everything above is a demo until you answer the question every embedded
engineer asks in the first thirty seconds: *you're letting a language model
load code onto devices — are you insane?*

The honest answer is: only if you trust the model. So MILO doesn't. Nothing
the model emits is trusted, and every module crosses four independent gates
before its first instruction runs.

First, the frame itself is bounded — a payload length is checked against a
512 KB ceiling *before* any buffer is allocated, so a hostile length prefix
can't talk a device into a four-gigabyte allocation. Second, fleets can
require **Ed25519-signed bytecode**: provision a device with the operator's
public key and unsigned pushes are refused outright; verification runs on the
RISC-V and ARM targets, not just on a laptop. Third, the import whitelist:
a module may import the twelve syscalls and nothing else, or it is never
instantiated. Fourth, the sandbox — 64 KB of memory, per-instruction fuel
metering, no pointers into firmware. An infinite loop runs out of gas and
returns an error like a well-behaved citizen.

And one detail I'm disproportionately proud of: if generated code touches a
pin the device never advertised, the attempt is *denied and logged* —
`denied: pwm_set 7 not in manifest` — instead of silently doing nothing. Silent
failure is how you erode trust in a system like this. Loud failure is how the
model learns, because the log goes right back into its context.

The rest of industry-readiness is even less glamorous and I'll be brief: CI
builds flashable images for both supported boards on every commit (ESP32-C3
as a `.bin`, Raspberry Pi Pico as a `.uf2`), the test matrix runs the
end-to-end signing and fleet tests on every push, and a compile cache brings
repeated builds — the fleet-push case, the LLM-retry case — down to a tenth
of a millisecond. When I dispatched CI on the release branch it caught two
real bugs my laptop had been hiding, a stricter linker and a missing system
library. That round-trip annoyed me for an hour and is precisely why any of
this is worth calling ready.

## What I'd tell you to take away

Not "LLMs can write firmware." They obviously can; that stopped being
interesting a year ago.

The takeaway is that the trust boundary can be drawn *below* the model. Put a
twelve-call ABI, a signature check, and a metered sandbox between the model
and the silicon, and suddenly the model's creativity becomes an asset instead
of a liability — it can rewrite a control law on a drifting oven mid-shift,
because the worst it can ever do is fail one episode on one device.

Repos and receipts: everything, including the verbatim firmware GPT-4o wrote
during these runs, is at **github.com/tanmay-xvx/MILO**. The two flagship
scenarios rerun with one command each. If you own an ESP32-C3 or a Pico, the
CI artifacts flash directly, and the porting guide is about one trait long if
your board isn't on the list yet.

Milo the dog remains unimpressed. Everyone's a critic.

---

*MILO is Apache-2.0. If you build something with it — or break something in
it — I want to hear about it.*
