#!/usr/bin/env python3
"""Scenario 2 — LLM-supervised autonomous factory cell.

Three emulated machines (real MILO receiver runtime, simulated plant
physics) run under one central control station:

  conveyor-1  belt with item photo-sensor and motor-temperature sensor
  oven-1      reflow oven running naive open-loop "vendor firmware"
  arm-1       3-joint pick/clear arm with limit switches

The control station pushes short monitoring/control episodes to every
machine each cycle and reads their reports back over MILO-Link. It handles
two live incidents autonomously:

  Incident A  the oven's heater element drifts hot → sustained overheat.
              GPT-4o diagnoses the telemetry, then *writes new closed-loop
              controller firmware*, which is pushed to the oven mid-run.
  Incident B  an item jams the conveyor (belt powered, sensor silent).
              The station halts the belt, dispatches the arm to clear the
              obstruction, and restores throughput.

Evidence written to demos/evidence/: telemetry JSONL, cycle-by-cycle
readings + incident timeline JSON, the LLM diagnosis and generated oven
firmware, and a transcript.

Run:  python3 demos/factory_demo.py        (from the repo root)
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demos import drivers  # noqa: E402
from demos.fleet import Fleet  # noqa: E402
from demos.llm import generate_driver, llm_available, llm_diagnose  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "host"))
from core.compiler import compile_rust_to_wasm  # noqa: E402

EVIDENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence")

OVEN_LIMIT_TENTHS = 1650  # alarm threshold: 165.0 °C
OVEN_TARGET_TENTHS = 1500

OVEN_FIX_TASK = """\
The reflow oven's heater element has drifted and now runs roughly 35% hotter
than spec, so the existing OPEN-LOOP firmware (fixed 41.7% heater duty)
overshoots badly. Write REPLACEMENT firmware implementing a closed-loop
integral temperature controller.

Requirements (match the plant's conventions exactly):
- Target temperature in tenths of °C from get_param(2); if 0 use 1500.
- Chamber temperature in tenths of °C from adc_read(0).
- Keep an i32 duty accumulator starting at 3000. Each iteration:
  err = target - temperature (i32); accumulator += err / 4; clamp the
  accumulator to [0, 10000]; apply it with pwm_set(0, duty).
- Run exactly 15 iterations of delay_ms(200).
- Each iteration log the temperature as "T=<value>" using manual digit
  extraction (no format!/alloc).
- Log a short message when the controller engages.
"""


class Transcript:
    def __init__(self, path: str):
        self.f = open(path, "w")
        self.t0 = time.time()

    def say(self, msg: str) -> None:
        line = f"[t+{time.time() - self.t0:7.2f}s] {msg}"
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def parse_kv(logs: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in logs:
        m = re.fullmatch(r"([A-Z_]+)=(\d+)", line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def oven_temps(logs: list[str]) -> list[int]:
    return [int(m.group(1)) for line in logs for m in [re.fullmatch(r"T=(\d+)", line)] if m]


def main() -> int:
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    telemetry_path = os.path.join(EVIDENCE_DIR, "factory_telemetry.jsonl")
    say = Transcript(os.path.join(EVIDENCE_DIR, "factory_transcript.txt")).say
    cycles: list[dict] = []
    incidents: list[dict] = []
    metrics: dict = {}

    say(f"MILO factory demo — 3 emulated machines, LLM={'live (gpt-4o)' if llm_available() else 'fallback'}")

    t = time.perf_counter()
    wasm_conveyor = compile_rust_to_wasm(drivers.CONVEYOR_RUN)
    wasm_oven_naive = compile_rust_to_wasm(drivers.OVEN_OPEN_LOOP)
    wasm_halt = compile_rust_to_wasm(drivers.CONVEYOR_HALT)
    wasm_sweep = compile_rust_to_wasm(drivers.ARM_CLEAR_SWEEP)
    say(f"compiled 4 baseline drivers in {(time.perf_counter()-t)*1000:.0f} ms "
        f"({len(wasm_conveyor)}/{len(wasm_oven_naive)}/{len(wasm_halt)}/{len(wasm_sweep)} bytes)")

    with Fleet(telemetry_path=telemetry_path) as fleet:
        fleet.spawn("conveyor-1", "conveyor")
        fleet.spawn("oven-1", "oven")
        fleet.spawn("arm-1", "arm")
        fleet.connect_all()
        say("cell online: " + ", ".join(
            f"{d['name']} ({d['manifest']['board']})" for d in fleet.registry.list_devices()
        ))

        conveyor = fleet.registry.get("conveyor-1").device
        oven = fleet.registry.get("oven-1").device
        arm = fleet.registry.get("arm-1").device

        conveyor.set_param(1, 6000)  # belt duty
        oven.set_param(2, OVEN_TARGET_TENTHS)

        wasm_oven_current = wasm_oven_naive
        oven_mode = "open-loop (vendor firmware)"
        oven_fixed = False
        jam_cleared = False

        for cycle in range(1, 13):
            # Scripted incident injections at fixed cycles for reproducibility.
            if cycle == 5:
                oven.set_param(7, 1)
                incidents.append({"cycle": cycle, "incident": "heater_drift_injected"})
                say("‼ INCIDENT A (unannounced to the controller): oven heater element drifts +35%")
            if cycle == 10:
                conveyor.set_param(7, 1)
                incidents.append({"cycle": cycle, "incident": "jam_injected"})
                say("‼ INCIDENT B (unannounced to the controller): item jams the conveyor")

            # One monitoring/control episode on conveyor + oven in parallel.
            with ThreadPoolExecutor(max_workers=2) as pool:
                fc = pool.submit(conveyor.push, wasm_conveyor, 30.0)
                fo = pool.submit(oven.push, wasm_oven_current, 30.0)
                rc, ro = fc.result(), fo.result()

            kv = parse_kv(rc.logs)
            temps = oven_temps(ro.logs)
            reading = {
                "cycle": cycle,
                "items": kv.get("ITEMS"),
                "belt_duty": kv.get("DUTY"),
                "motor_temp_tenths": kv.get("TEMP"),
                "oven_temps_tenths": temps,
                "oven_mode": oven_mode,
            }
            cycles.append(reading)
            say(f"cycle {cycle:2d}: belt items={kv.get('ITEMS')} motor={kv.get('TEMP', 0)/10:.1f}°C | "
                f"oven {temps[0]/10:.1f}→{temps[-1]/10:.1f}°C [{oven_mode}]")

            # ── Autonomous supervision ────────────────────────────────────
            if not oven_fixed and temps and max(temps) > OVEN_LIMIT_TENTHS:
                say(f"⚠ ALARM: oven peaked at {max(temps)/10:.1f}°C (limit {OVEN_LIMIT_TENTHS/10:.0f}°C) — consulting LLM supervisor")
                diagnosis, diag_mode = llm_diagnose(
                    "The reflow oven temperature is rising past its 165°C alarm limit "
                    "while running fixed-duty open-loop firmware. Recent episode "
                    "temperatures (tenths of °C) attached.",
                    {"recent_cycles": cycles[-3:]},
                )
                say("LLM diagnosis: " + diagnosis.replace("\n", " "))
                t = time.perf_counter()
                wasm_fix, source, gen_mode = generate_driver(
                    OVEN_FIX_TASK, fleet.registry.get("oven-1").manifest,
                    fallback_body=drivers.OVEN_CLOSED_LOOP_FALLBACK,
                )
                gen_ms = (time.perf_counter() - t) * 1000
                with open(os.path.join(EVIDENCE_DIR, "factory_llm_oven_fix.rs"), "w") as f:
                    f.write(f"// Oven closed-loop controller — generated_by: {gen_mode}\n{source}\n")
                with open(os.path.join(EVIDENCE_DIR, "factory_llm_diagnosis.txt"), "w") as f:
                    f.write(f"[incident A — diagnosis mode: {diag_mode}]\n{diagnosis}\n")
                wasm_oven_current = wasm_fix
                oven_mode = f"closed-loop ({gen_mode}-written)"
                oven_fixed = True
                incidents.append({
                    "cycle": cycle, "incident": "oven_firmware_replaced",
                    "gen_mode": gen_mode, "gen_ms": round(gen_ms),
                    "wasm_bytes": len(wasm_fix), "diag_mode": diag_mode,
                })
                metrics["oven_fix_gen_ms"] = round(gen_ms)
                metrics["oven_fix_mode"] = gen_mode
                say(f"↻ FIRMWARE REPLACED: oven now runs {oven_mode} firmware "
                    f"({len(wasm_fix)} bytes, generated in {gen_ms:.0f} ms) — production continues")

            if not jam_cleared and kv.get("ITEMS") == 0 and kv.get("DUTY", 0) > 0:
                say("⚠ ALARM: belt powered but zero items past the sensor — consulting LLM supervisor")
                diagnosis, diag_mode = llm_diagnose(
                    "The conveyor belt motor is at commanded duty but the item "
                    "photo-sensor counted zero items this episode (normally ~3). "
                    "A physical jam is suspected.",
                    {"recent_cycles": cycles[-3:]},
                )
                say("LLM diagnosis: " + diagnosis.replace("\n", " "))
                with open(os.path.join(EVIDENCE_DIR, "factory_llm_jam_diagnosis.txt"), "w") as f:
                    f.write(f"[incident B — diagnosis mode: {diag_mode}]\n{diagnosis}\n")

                t = time.perf_counter()
                say("→ halting belt")
                conveyor.push(wasm_halt, timeout=15.0)
                say("→ dispatching arm-1 to clear the obstruction")
                ra = arm.push(wasm_sweep, timeout=30.0)
                say(f"   arm report: {ra.logs}")
                # The sweep physically removes the jammed item.
                conveyor.set_param(7, 0)
                recover_s = time.perf_counter() - t
                jam_cleared = True
                incidents.append({
                    "cycle": cycle, "incident": "jam_cleared",
                    "recovery_s": round(recover_s, 1), "arm_logs": ra.logs,
                    "diag_mode": diag_mode,
                })
                metrics["jam_recovery_s"] = round(recover_s, 1)
                say(f"✓ RECOVERED: belt restored after {recover_s:.1f} s — resuming production")

        # ── Shift summary ─────────────────────────────────────────────────
        healthy = [c["items"] for c in cycles if c["items"] and c["cycle"] < 10]
        after = [c["items"] for c in cycles if c["items"] is not None and c["cycle"] > 10]
        final_temps = cycles[-1]["oven_temps_tenths"]
        metrics["total_items"] = sum(c["items"] or 0 for c in cycles)
        metrics["throughput_healthy_avg"] = round(sum(healthy) / max(len(healthy), 1), 1)
        metrics["throughput_after_jam_avg"] = round(sum(after) / max(len(after), 1), 1)
        metrics["oven_final_c"] = final_temps[-1] / 10 if final_temps else None
        peak = max(max(c["oven_temps_tenths"] or [0]) for c in cycles)
        metrics["oven_peak_c"] = peak / 10
        say(f"shift complete: {metrics['total_items']} items processed; oven peaked at "
            f"{metrics['oven_peak_c']:.1f}°C during the incident and finished at "
            f"{metrics['oven_final_c']:.1f}°C under {oven_mode} firmware")
        say(f"metrics: {json.dumps(metrics)}")

        with open(os.path.join(EVIDENCE_DIR, "factory_events.json"), "w") as f:
            json.dump(
                {
                    "spawn_walls": {m.name: m.spawned_at for m in fleet.members},
                    "cycles": cycles,
                    "incidents": incidents,
                    "metrics": metrics,
                },
                f,
                indent=2,
            )
    say("evidence written to demos/evidence/ (telemetry, events, transcript, LLM firmware + diagnoses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
