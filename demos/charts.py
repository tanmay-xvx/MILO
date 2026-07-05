#!/usr/bin/env python3
"""Generate evidence charts (theme-aware SVG) from demo telemetry.

Pure-stdlib SVG generation; palette validated with the dataviz six-checks
validator (light + dark). Charts embed a <style> block keyed off
prefers-color-scheme so they render on both surfaces, and every series is
direct-labeled (the relief obligation for the light aqua/yellow slots; data
tables accompany the charts wherever they are embedded).

Run:  python3 demos/charts.py     (after the demos have produced evidence)
"""

import json
import os
import sys

EVIDENCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence")
ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "blog", "assets"
)

# Validated categorical palette (reference instance), slots 1-5, light/dark.
LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7"]
DARK = ["#3987e5", "#199e70", "#c98500", "#008300", "#9085e9"]
SERIOUS_L, SERIOUS_D = "#c62f2e", "#e66767"  # status: serious

STYLE = """
  <style>
    .bg { fill: #fcfcfb; }
    .txt { fill: #0b0b0b; font: 600 15px -apple-system, 'Segoe UI', sans-serif; }
    .txt2 { fill: #52514e; font: 12px -apple-system, 'Segoe UI', sans-serif; }
    .txtm { fill: #7a7a74; font: 11px -apple-system, 'Segoe UI', sans-serif; }
    .tick { fill: #52514e; font: 11px -apple-system, 'Segoe UI', sans-serif;
            font-variant-numeric: tabular-nums; }
    .grid { stroke: #ececea; stroke-width: 1; }
    .evt { stroke: #9b9a94; stroke-width: 1; stroke-dasharray: 3 3; }
    .serious { stroke: #c62f2e; }
    .serious-txt { fill: #c62f2e; font: 600 11px -apple-system, sans-serif; }
    .ring { stroke: #fcfcfb; stroke-width: 2; }
    __SERIES_LIGHT__
    @media (prefers-color-scheme: dark) {
      .bg { fill: #1a1a19; }
      .txt { fill: #ffffff; }
      .txt2 { fill: #c3c2b7; }
      .txtm { fill: #93928c; }
      .tick { fill: #c3c2b7; }
      .grid { stroke: #2c2c2a; }
      .evt { stroke: #6b6a64; }
      .serious { stroke: #e66767; }
      .serious-txt { fill: #e66767; }
      .ring { stroke: #1a1a19; }
      __SERIES_DARK__
    }
  </style>
"""


def series_css(n: int) -> tuple[str, str]:
    light = "\n    ".join(
        f".s{i} {{ stroke: {LIGHT[i]}; }} .sf{i} {{ fill: {LIGHT[i]}; }}" for i in range(n)
    )
    dark = "\n      ".join(
        f".s{i} {{ stroke: {DARK[i]}; }} .sf{i} {{ fill: {DARK[i]}; }}" for i in range(n)
    )
    return light, dark


def svg_doc(width: int, height: int, n_series: int, body: str) -> str:
    light, dark = series_css(n_series)
    style = STYLE.replace("__SERIES_LIGHT__", light).replace("__SERIES_DARK__", dark)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img">{style}'
        f'<rect class="bg" x="0" y="0" width="{width}" height="{height}" rx="8"/>'
        f"{body}</svg>"
    )


def load_jsonl(path: str) -> list[dict]:
    out = []
    for line in open(path):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def downsample(pts: list[tuple], step_s: float = 0.4) -> list[tuple]:
    out, next_t = [], -1e9
    for p in pts:
        if p[0] >= next_t:
            out.append(p)
            next_t = p[0] + step_s
    return out


def polyline(pts, x_of, y_of, cls: str, width: float = 2.0, extra: str = "") -> str:
    d = " ".join(f"{x_of(t):.1f},{y_of(v):.1f}" for t, v in pts)
    return (
        f'<polyline points="{d}" fill="none" class="{cls}" stroke-width="{width}" '
        f'stroke-linejoin="round" stroke-linecap="round" {extra}/>'
    )


# ── Chart 1: swarm altitude ──────────────────────────────────────────────

def swarm_chart() -> None:
    tele = load_jsonl(os.path.join(EVIDENCE, "swarm_telemetry.jsonl"))
    ev = json.load(open(os.path.join(EVIDENCE, "swarm_events.json")))
    spawn = ev["spawn_walls"]
    t0 = min(spawn.values())

    per: dict[str, list] = {}
    for j in tele:
        if "state" in j and "alt_m" in j["state"]:
            # common clock: shift each device's local t by its spawn offset
            per.setdefault(j["device"], []).append(
                (j["t"] + spawn[j["device"]] - t0, j["state"]["alt_m"])
            )
    names = sorted(per)
    t_max = max(t for pts in per.values() for t, _ in pts)

    W, H = 880, 450
    L, R, T, B = 56, 24, 98, 46
    pw, ph = W - L - R, H - T - B
    y_max = 4.6
    x_of = lambda t: L + t / t_max * pw
    y_of = lambda v: T + ph - min(v, y_max) / y_max * ph

    b = [f'<text class="txt" x="{L}" y="30">Five-drone swarm — altitude under live LLM command</text>']
    b.append(
        f'<text class="txt2" x="{L}" y="50">Emulated fleet running the real MILO runtime · '
        f"retask, fault and in-flight hot-swap repair</text>"
    )

    # grid + y ticks (altitude, m)
    for v in range(0, 5):
        y = y_of(v)
        b.append(f'<line class="grid" x1="{L}" y1="{y:.1f}" x2="{L+pw}" y2="{y:.1f}"/>')
        b.append(f'<text class="tick" x="{L-8}" y="{y+4:.1f}" text-anchor="end">{v}</text>')
    b.append(f'<text class="txt2" x="{L+pw}" y="{T+ph+18}" text-anchor="end">time</text>')
    b.append(
        f'<text class="txt2" transform="rotate(-90 16 {T+ph/2:.0f})" x="16" y="{T+ph/2:.0f}" '
        f'text-anchor="middle">altitude (m)</text>'
    )
    for t in range(0, int(t_max) + 1, 10):
        b.append(f'<text class="tick" x="{x_of(t):.1f}" y="{T+ph+18}" text-anchor="middle">{t}s</text>')

    # event rules
    labels = {"takeoff": "launch", "retask": "retask → 3.0 m", "fault_injected": "motor fault",
              "hot_swap": "LLM hot-swap", "land": "land"}
    for e in ev["events"]:
        if e["event"] not in labels:
            continue
        te = e["wall"] - t0
        x = x_of(te)
        b.append(f'<line class="evt" x1="{x:.1f}" y1="{T-6}" x2="{x:.1f}" y2="{T+ph}"/>')
        anchor = ' text-anchor="end"' if x > L + pw - 60 else ""
        xt = x - 4 if x > L + pw - 60 else x + 4
        b.append(f'<text class="txtm" x="{xt:.1f}" y="{T+2}"{anchor}>{labels[e["event"]]}</text>')

    # series lines (drone-3, the story, drawn last and thicker)
    order = [n for n in names if n != "drone-3"] + ["drone-3"]
    for name in order:
        i = names.index(name)
        pts = downsample(per[name])
        w = 3.0 if name == "drone-3" else 2.0
        b.append(polyline(pts, x_of, y_of, f"s{i}", w))

    # legend row (identity channel) + selective direct label on drone-3
    lx = L
    ly = 68
    for i, name in enumerate(names):
        b.append(f'<line class="s{i}" x1="{lx}" y1="{ly}" x2="{lx+18}" y2="{ly}" stroke-width="3"/>')
        b.append(f'<text class="txt2" x="{lx+24}" y="{ly+4}">{name}</text>')
        lx += 24 + 9 * len(name) + 22
    fault_wall = next(e["wall"] for e in ev["events"] if e["event"] == "fault_injected") - t0
    swap_wall = next(e["wall"] for e in ev["events"] if e["event"] == "hot_swap") - t0
    f3 = per["drone-3"]
    sag_t, sag_v = min(
        (p for p in f3 if fault_wall < p[0] < swap_wall), key=lambda p: p[1]
    )
    b.append(
        f'<text class="txt2" x="{x_of(sag_t)+6:.1f}" y="{y_of(sag_v)+16:.1f}">'
        f"drone-3 droops to {sag_v:.1f} m</text>"
    )
    b.append(
        f'<circle class="sf2 ring" cx="{x_of(sag_t):.1f}" cy="{y_of(sag_v):.1f}" r="4"/>'
    )

    out = svg_doc(W, H, len(names), "".join(b))
    open(os.path.join(ASSETS, "swarm_altitude.svg"), "w").write(out)
    print("wrote swarm_altitude.svg")


# ── Chart 2: oven temperature ────────────────────────────────────────────

def oven_chart() -> None:
    tele = load_jsonl(os.path.join(EVIDENCE, "factory_telemetry.jsonl"))
    pts, fault_t, fix_t = [], None, None
    for j in tele:
        if j.get("device") == "oven-1" and "state" in j:
            s = j["state"]
            pts.append((j["t"], s["temp_c"]))
            if fault_t is None and s["fault"] == 1:
                fault_t = j["t"]
            if fault_t is not None and fix_t is None and s["fault"] == 1 and abs(s["heater_duty"] - 0.417) > 0.02 and s["heater_duty"] > 0:
                fix_t = j["t"]
    pts = downsample(pts, 0.3)
    t_max = pts[-1][0]

    W, H = 880, 400
    L, R, T, B = 56, 24, 78, 46
    pw, ph = W - L - R, H - T - B
    y_max = 200.0
    x_of = lambda t: L + t / t_max * pw
    y_of = lambda v: T + ph - v / y_max * ph

    b = [f'<text class="txt" x="{L}" y="30">Oven chamber temperature — heater drift caught and corrected</text>']
    b.append(
        f'<text class="txt2" x="{L}" y="50">Open-loop vendor firmware overheats after the element drifts; '
        f"GPT-4o writes closed-loop firmware, pushed mid-production</text>"
    )
    for v in range(0, 201, 50):
        y = y_of(v)
        b.append(f'<line class="grid" x1="{L}" y1="{y:.1f}" x2="{L+pw}" y2="{y:.1f}"/>')
        b.append(f'<text class="tick" x="{L-8}" y="{y+4:.1f}" text-anchor="end">{v}</text>')
    b.append(
        f'<text class="txt2" transform="rotate(-90 16 {T+ph/2:.0f})" x="16" y="{T+ph/2:.0f}" '
        f'text-anchor="middle">temperature (°C)</text>'
    )
    for t in range(0, int(t_max) + 1, 10):
        b.append(f'<text class="tick" x="{x_of(t):.1f}" y="{T+ph+18}" text-anchor="middle">{t}s</text>')

    # alarm threshold (status: serious) with label
    yl = y_of(165)
    b.append(f'<line class="serious" x1="{L}" y1="{yl:.1f}" x2="{L+pw}" y2="{yl:.1f}" stroke-width="1.5" stroke-dasharray="6 4"/>')
    b.append(f'<text class="serious-txt" x="{L+pw-4}" y="{yl-6:.1f}" text-anchor="end">▲ alarm limit 165 °C</text>')

    for te, lbl in [(fault_t, "element drifts +35%"), (fix_t, "LLM firmware replaces open-loop")]:
        if te is None:
            continue
        x = x_of(te)
        b.append(f'<line class="evt" x1="{x:.1f}" y1="{T-6}" x2="{x:.1f}" y2="{T+ph}"/>')
        b.append(f'<text class="txtm" x="{x+4:.1f}" y="{T+2}">{lbl}</text>')

    b.append(polyline(pts, x_of, y_of, "s0", 2.5))
    # end label (single series → no legend; direct label the endpoint)
    b.append(
        f'<circle class="sf0 ring" cx="{x_of(pts[-1][0]):.1f}" cy="{y_of(pts[-1][1]):.1f}" r="4"/>'
    )
    b.append(
        f'<text class="txt2" x="{x_of(pts[-1][0])-6:.1f}" y="{y_of(pts[-1][1])-10:.1f}" '
        f'text-anchor="end">holds {pts[-1][1]:.0f} °C despite drifted heater</text>'
    )

    out = svg_doc(W, H, 1, "".join(b))
    open(os.path.join(ASSETS, "factory_oven.svg"), "w").write(out)
    print("wrote factory_oven.svg")


# ── Chart 3: conveyor throughput per cycle ───────────────────────────────

def throughput_chart() -> None:
    ev = json.load(open(os.path.join(EVIDENCE, "factory_events.json")))
    cycles = ev["cycles"]

    W, H = 880, 340
    L, R, T, B = 56, 24, 78, 46
    pw, ph = W - L - R, H - T - B
    y_max = 5
    n = len(cycles)
    band = pw / n
    bar_w = min(24, band - 8)
    y_of = lambda v: T + ph - v / y_max * ph

    b = [f'<text class="txt" x="{L}" y="30">Conveyor throughput — jam detected, cleared by the arm, restored</text>']
    b.append(
        f'<text class="txt2" x="{L}" y="50">Items past the photo-sensor per 3-second episode; '
        f"the zero at cycle 10 is the jam the station caught autonomously</text>"
    )
    for v in range(0, y_max + 1):
        y = y_of(v)
        b.append(f'<line class="grid" x1="{L}" y1="{y:.1f}" x2="{L+pw}" y2="{y:.1f}"/>')
        b.append(f'<text class="tick" x="{L-8}" y="{y+4:.1f}" text-anchor="end">{v}</text>')
    b.append(
        f'<text class="txt2" transform="rotate(-90 16 {T+ph/2:.0f})" x="16" y="{T+ph/2:.0f}" '
        f'text-anchor="middle">items / episode</text>'
    )

    for i, c in enumerate(cycles):
        items = c["items"] or 0
        x = L + i * band + (band - bar_w) / 2
        y = y_of(items)
        h = T + ph - y
        if h > 0:
            # 4px rounded data-end, square at the baseline
            b.append(
                f'<path class="sf0" d="M{x:.1f},{T+ph:.1f} v{-(h-4):.1f} q0,-4 4,-4 '
                f'h{bar_w-8:.1f} q4,0 4,4 v{h-4:.1f} z"/>'
            )
        b.append(
            f'<text class="tick" x="{x+bar_w/2:.1f}" y="{T+ph+18}" text-anchor="middle">{c["cycle"]}</text>'
        )
        if items == 0:
            b.append(
                f'<text class="serious-txt" x="{x+bar_w/2:.1f}" y="{y_of(0)-8:.1f}" '
                f'text-anchor="middle">✕ jam</text>'
            )
    b.append(f'<text class="txt2" x="{L+pw/2:.1f}" y="{H-8}" text-anchor="middle">cycle</text>')

    out = svg_doc(W, H, 1, "".join(b))
    open(os.path.join(ASSETS, "factory_throughput.svg"), "w").write(out)
    print("wrote factory_throughput.svg")


if __name__ == "__main__":
    os.makedirs(ASSETS, exist_ok=True)
    swarm_chart()
    oven_chart()
    throughput_chart()
    sys.exit(0)
