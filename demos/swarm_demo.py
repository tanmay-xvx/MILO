#!/usr/bin/env python3
"""Scenario 1 — LLM-commanded drone swarm.

Five emulated quadcopters (real MILO receiver runtime, simulated flight
dynamics) are flown from one laptop:

  Phase 1  One wasm flight controller is compiled once and pushed to all
           five drones in parallel; each holds its own formation altitude
           read live from parameter slot 0.
  Phase 2  The formation is retasked *mid-flight* by broadcasting new
           altitude targets — no recompile, no reflash. Latency is measured.
  Phase 3  A motor fault is injected on one drone; its pure-P controller
           droops ~1.5 m below the commanded altitude.
  Phase 4  GPT-4o is asked, live, to write a repaired controller (PI). The
           new wasm is hot-swapped onto the failing drone in flight and the
           altitude recovers.
  Phase 5  Coordinated landing via the cooperative-stop parameter; every
           drone reports its episode logs.

Evidence written to demos/evidence/: telemetry JSONL (physics ground
truth), events JSON (host-side timeline + measured latencies), the
LLM-generated repair source, and a human-readable transcript.

Run:  python3 demos/swarm_demo.py         (from the repo root)
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demos import drivers  # noqa: E402
from demos.fleet import Fleet  # noqa: E402
from demos.llm import generate_driver, llm_available  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))
from core.compiler import compile_rust_to_wasm  # noqa: E402

EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence")
N_DRONES = 5
FORMATION_ECHELON = [140, 210, 280, 350, 420]  # cm — staggered echelon
LINE_ALT = 300  # cm — retasked line-abreast altitude
FAULTED = "drone-3"

REPAIR_TASK = """\
Drone {name} is holding about 1.5 m BELOW its commanded altitude. Suspected
cause: one motor delivering reduced thrust, so the current pure-proportional
altitude controller droops. Write a REPLACEMENT altitude controller with
proportional-integral action so the drone recovers the commanded altitude
despite the thrust deficit.

Requirements (match the fleet's driver conventions exactly):
- Read the commanded altitude in cm from get_param(0); if it is 0 use 200.
- Read altitude from the IMU at I2C address 0x68: send 1 dummy byte, read 3
  bytes; altitude_cm = (rx[0] as u32) << 8 | rx[1] as u32.
- Control loop: at most 1200 iterations of delay_ms(50); exit the loop early
  when get_param(6) == 9999.
- Integral term: a duty accumulator starting at 5000, incremented by err/24
  each iteration, clamped to [2500, 8500]. Command = accumulator + err*3,
  clamped to [2500, 9500]. err = target_cm - altitude_cm (i32 math).
- Apply the same command to all four motors: pwm_set channels 0,1,2,3.
- After the loop set all four motor duties to 0 and log a completion message.
- Log a short message when the controller engages.
"""


class Transcript:
    def __init__(self, path: str):
        self.f = open(path, "w")
        self.t0 = time.time()

    def say(self, msg: str) -> None:
        stamp = time.time() - self.t0
        line = f"[t+{stamp:7.2f}s] {msg}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def main() -> int:
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    telemetry_path = os.path.join(EVIDENCE_DIR, "swarm_telemetry.jsonl")
    say = Transcript(os.path.join(EVIDENCE_DIR, "swarm_transcript.txt")).say
    events: list[dict] = []
    metrics: dict = {"llm_mode": None}

    def mark(event: str, **kw) -> None:
        events.append({"wall": time.time(), "event": event, **kw})

    say(f"MILO swarm demo — {N_DRONES} emulated drones, LLM={'live (gpt-4o)' if llm_available() else 'fallback'}")

    with Fleet(telemetry_path=telemetry_path) as fleet:
        for i in range(1, N_DRONES + 1):
            fleet.spawn(f"drone-{i}", "drone")
        fleet.connect_all()
        spawn_walls = {m.name: m.spawned_at for m in fleet.members}
        say(f"fleet up: {', '.join(d['name'] for d in fleet.registry.list_devices())}")
        boards = {d["name"]: d["manifest"]["board"] for d in fleet.registry.list_devices()}
        say(f"discovery manifests: {boards}")

        # ── Phase 1: compile once, arm formation ─────────────────────────
        t = time.perf_counter()
        wasm_hold = compile_rust_to_wasm(drivers.DRONE_HOLD)
        compile_ms = (time.perf_counter() - t) * 1000
        metrics["controller_wasm_bytes"] = len(wasm_hold)
        metrics["compile_ms"] = round(compile_ms)
        say(f"compiled flight controller: {len(wasm_hold)} bytes in {compile_ms:.0f} ms")

        for i, name in enumerate(sorted(spawn_walls)):
            fleet.registry.get(name).device.set_param(0, FORMATION_ECHELON[i])
        t = time.perf_counter()
        fleet.registry.push_async_to_all(wasm_hold)
        push_ms = (time.perf_counter() - t) * 1000
        metrics["parallel_push_ms"] = round(push_ms, 1)
        mark("takeoff", formation_cm=FORMATION_ECHELON)
        say(f"pushed controller to all {N_DRONES} drones in {push_ms:.1f} ms — echelon-formation takeoff")
        time.sleep(10)
        statuses = {n: s.status for n, s in fleet.registry.query_all().items()}
        say(f"fleet status while flying: {statuses}")

        # ── Phase 2: retask the formation mid-flight ─────────────────────
        t = time.perf_counter()
        broadcast_s = fleet.registry.broadcast_param(0, LINE_ALT)
        metrics["retask_broadcast_ms"] = round(broadcast_s * 1000, 2)
        mark("retask", target_cm=LINE_ALT)
        say(f"RETASK: formation → line at {LINE_ALT} cm; broadcast to {N_DRONES} drones took {broadcast_s*1000:.2f} ms (no recompile, no reflash)")
        time.sleep(9)

        # ── Phase 3: inject a motor fault on one drone ───────────────────
        fleet.registry.get(FAULTED).device.set_param(7, 1)
        mark("fault_injected", device=FAULTED)
        say(f"FAULT: {FAULTED} motor 2 degraded to 40% thrust — watch it droop")
        time.sleep(9)

        # ── Phase 4: LLM writes the repair, hot-swap in flight ───────────
        manifest = fleet.registry.get(FAULTED).manifest
        say("asking GPT-4o to write a PI repair controller for the degraded drone …")
        t = time.perf_counter()
        wasm_fix, source, mode = generate_driver(
            REPAIR_TASK.format(name=FAULTED),
            manifest,
            fallback_body=drivers.DRONE_REPAIR_FALLBACK,
        )
        gen_ms = (time.perf_counter() - t) * 1000
        metrics["llm_mode"] = mode
        metrics["repair_gen_ms"] = round(gen_ms)
        metrics["repair_wasm_bytes"] = len(wasm_fix)
        with open(os.path.join(EVIDENCE_DIR, "swarm_llm_driver.rs"), "w") as f:
            f.write(f"// Repair controller for {FAULTED} — generated_by: {mode}\n{source}\n")
        say(f"repair controller ready ({mode}): {len(wasm_fix)} bytes in {gen_ms:.0f} ms")

        t = time.perf_counter()
        fleet.registry.get(FAULTED).device.hot_swap_async(wasm_fix)
        swap_ms = (time.perf_counter() - t) * 1000
        metrics["hot_swap_send_ms"] = round(swap_ms, 2)
        mark("hot_swap", device=FAULTED)
        say(f"HOT-SWAP: new controller onto {FAULTED} in flight ({swap_ms:.2f} ms send) — no landing, no reflash")
        time.sleep(12)

        # ── Phase 5: coordinated landing ─────────────────────────────────
        fleet.registry.broadcast_param(6, 9999)
        mark("land")
        say("LAND: cooperative-stop broadcast to the whole formation")
        results = fleet.registry.wait_all_results(timeout=20)
        for name in sorted(results):
            r = results[name]
            say(f"  {name}: ok={r.ok} logs={r.logs}")
        # The hot-swapped drone has a second episode result (the repair
        # controller's own landing report) queued behind the first.
        repair_r = fleet.registry.get(FAULTED).device.wait_result(timeout=20)
        say(f"  {FAULTED} (repair episode): ok={repair_r.ok} logs={repair_r.logs}")
        metrics["landing_ok"] = all(r.ok for r in results.values()) and repair_r.ok

        # Persist the event timeline with per-device time offsets so charts
        # can align host events to telemetry timestamps.
        with open(os.path.join(EVIDENCE_DIR, "swarm_events.json"), "w") as f:
            json.dump(
                {"spawn_walls": spawn_walls, "events": events, "metrics": metrics},
                f,
                indent=2,
            )

    # ── Post-flight analysis from physics ground truth ───────────────────
    per_drone: dict[str, list] = {}
    for line in open(telemetry_path):
        try:
            j = json.loads(line)
        except json.JSONDecodeError:
            continue  # tolerate a rare torn line from concurrent appenders
        if "state" in j and "alt_m" in j["state"]:
            per_drone.setdefault(j["device"], []).append(
                (j["t"], j["state"]["alt_m"], j["state"]["fault"])
            )

    fault_wall = next(e["wall"] for e in events if e["event"] == "fault_injected")
    swap_wall = next(e["wall"] for e in events if e["event"] == "hot_swap")
    fseries = per_drone[FAULTED]
    f_t0 = spawn_walls[FAULTED]
    sag = min(a for t_, a, _ in fseries if fault_wall - f_t0 < t_ < swap_wall - f_t0 + 2)
    recovered_at = None
    for t_, a, _ in fseries:
        if t_ > swap_wall - f_t0 and abs(a - LINE_ALT / 100) < 0.15:
            recovered_at = t_
            break
    metrics["fault_sag_m"] = round(LINE_ALT / 100 - sag, 2)
    if recovered_at:
        metrics["recovery_s_after_swap"] = round(recovered_at - (swap_wall - f_t0), 1)
    say(f"analysis: {FAULTED} sagged {metrics['fault_sag_m']} m under fault; "
        f"recovered to ±15 cm of target {metrics.get('recovery_s_after_swap', '?')} s after hot-swap")
    say(f"metrics: {json.dumps(metrics)}")

    with open(os.path.join(EVIDENCE_DIR, "swarm_events.json"), "w") as f:
        json.dump(
            {"spawn_walls": spawn_walls, "events": events, "metrics": metrics},
            f,
            indent=2,
        )
    say("evidence written to demos/evidence/ (telemetry, events, transcript, LLM driver)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
