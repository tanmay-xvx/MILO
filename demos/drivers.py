"""Wasm driver source bodies for the demo scenarios.

Each is a Rust `run_logic` body compiled through the standard MILO pipeline
(`host/core/compiler.py`) — exactly what the LLM emits in interactive use.
Scripted baselines keep the scenarios deterministic and reproducible; the
LLM writes the *corrective* drivers live (see demos/llm.py).

Conventions used by these drivers (documented in the system prompt):
  param slot 0  drone target altitude in cm
  param slot 1  conveyor belt duty (0-10000)
  param slot 2  oven target temperature in tenths of °C
  param slot 6  cooperative-stop sentinel (9999 = exit episode loop)
  param slot 7  reserved for the harness (fault injection in the sim)
"""

# Shared no_std helper: log "KEY=<u32>" without alloc/format!.
LOG_HELPER = r"""
fn log_kv(key: &[u8], val: u32) {
    let mut buf = [0u8; 28];
    let mut n = 0;
    for &b in key {
        buf[n] = b;
        n += 1;
    }
    buf[n] = b'=';
    n += 1;
    let mut digits = [0u8; 10];
    let mut d = 0;
    let mut v = val;
    if v == 0 {
        digits[0] = b'0';
        d = 1;
    }
    while v > 0 {
        digits[d] = b'0' + (v % 10) as u8;
        v /= 10;
        d += 1;
    }
    while d > 0 {
        d -= 1;
        buf[n] = digits[d];
        n += 1;
    }
    unsafe { log_msg(buf.as_ptr() as u32, n as u32) };
}
"""

# ── Drone ────────────────────────────────────────────────────────────────

# Baseline flight controller: proportional altitude hold. Target comes from
# param slot 0, so the host can retask the whole formation in flight without
# recompiling. Known limitation (deliberate, for the resilience scenario):
# pure P-control droops under a thrust deficit.
DRONE_HOLD = LOG_HELPER + r"""
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let msg = b"flight: P-hold engaged";
        log_msg(msg.as_ptr() as u32, msg.len() as u32);
        for _ in 0..1200u32 {
            if get_param(6) == 9999 {
                break;
            }
            let mut target_cm = get_param(0);
            if target_cm == 0 {
                target_cm = 200;
            }
            let tx = [0u8; 1];
            let mut rx = [0u8; 3];
            i2c_transfer(0x68, tx.as_ptr() as u32, 1, rx.as_mut_ptr() as u32, 3);
            let alt_cm = ((rx[0] as u32) << 8) | rx[1] as u32;
            let err = target_cm as i32 - alt_cm as i32;
            let mut duty = 5000 + err * 6;
            if duty < 2500 {
                duty = 2500;
            }
            if duty > 8500 {
                duty = 8500;
            }
            pwm_set(0, duty as u32);
            pwm_set(1, duty as u32);
            pwm_set(2, duty as u32);
            pwm_set(3, duty as u32);
            delay_ms(50);
        }
        pwm_set(0, 0);
        pwm_set(1, 0);
        pwm_set(2, 0);
        pwm_set(3, 0);
        let done = b"flight: episode complete, motors safed";
        log_msg(done.as_ptr() as u32, done.len() as u32);
    }
}
"""

# Fallback repair controller (used if the live LLM call fails): adds integral
# action so the formation recovers full altitude even with a degraded motor.
DRONE_REPAIR_FALLBACK = LOG_HELPER + r"""
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let msg = b"flight: PI-repair engaged";
        log_msg(msg.as_ptr() as u32, msg.len() as u32);
        let mut duty: i32 = 5000;
        for _ in 0..1200u32 {
            if get_param(6) == 9999 {
                break;
            }
            let mut target_cm = get_param(0);
            if target_cm == 0 {
                target_cm = 200;
            }
            let tx = [0u8; 1];
            let mut rx = [0u8; 3];
            i2c_transfer(0x68, tx.as_ptr() as u32, 1, rx.as_mut_ptr() as u32, 3);
            let alt_cm = ((rx[0] as u32) << 8) | rx[1] as u32;
            let err = target_cm as i32 - alt_cm as i32;
            duty += err / 24;
            if duty < 2500 {
                duty = 2500;
            }
            if duty > 8500 {
                duty = 8500;
            }
            let p = duty + err * 3;
            let cmd = if p < 2500 {
                2500
            } else if p > 9500 {
                9500
            } else {
                p
            } as u32;
            pwm_set(0, cmd);
            pwm_set(1, cmd);
            pwm_set(2, cmd);
            pwm_set(3, cmd);
            delay_ms(50);
        }
        pwm_set(0, 0);
        pwm_set(1, 0);
        pwm_set(2, 0);
        pwm_set(3, 0);
        let done = b"flight: episode complete, motors safed";
        log_msg(done.as_ptr() as u32, done.len() as u32);
    }
}
"""

# ── Factory: oven ────────────────────────────────────────────────────────

# "Vendor firmware": open-loop heater setting calibrated for a healthy
# heater (150 °C at ~41.7% duty). Logs the chamber temperature every 200 ms.
# When the heater element drifts, this holds the wrong temperature — that is
# the anomaly the control station must catch and fix.
OVEN_OPEN_LOOP = LOG_HELPER + r"""
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        pwm_set(0, 4170);
        for _ in 0..15u32 {
            let t = adc_read(0);
            log_kv(b"T", t);
            delay_ms(200);
        }
    }
}
"""

# Fallback closed-loop controller if the live LLM call fails.
OVEN_CLOSED_LOOP_FALLBACK = LOG_HELPER + r"""
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let msg = b"oven: closed-loop control engaged";
        log_msg(msg.as_ptr() as u32, msg.len() as u32);
        let mut duty: i32 = 4170;
        for _ in 0..15u32 {
            let mut target = get_param(2);
            if target == 0 {
                target = 1500;
            }
            let t = adc_read(0);
            let err = target as i32 - t as i32;
            duty += err / 2;
            if duty < 0 {
                duty = 0;
            }
            if duty > 10000 {
                duty = 10000;
            }
            pwm_set(0, duty as u32);
            log_kv(b"T", t);
            delay_ms(200);
        }
    }
}
"""

# ── Factory: conveyor ────────────────────────────────────────────────────

# Belt controller: speed from param slot 1, counts items past the
# photo-sensor for one 3-second episode, reports throughput + motor temp.
CONVEYOR_RUN = LOG_HELPER + r"""
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let mut duty = get_param(1);
        if duty == 0 {
            duty = 6000;
        }
        pwm_set(0, duty);
        let mut items: u32 = 0;
        let mut prev: u32 = 0;
        for _ in 0..60u32 {
            let s = gpio_get(26);
            if s == 1 && prev == 0 {
                items += 1;
            }
            prev = s;
            delay_ms(50);
        }
        log_kv(b"ITEMS", items);
        log_kv(b"TEMP", adc_read(1));
        log_kv(b"DUTY", duty);
    }
}
"""

# Belt halt: safe the motor immediately (pushed during jam recovery).
CONVEYOR_HALT = LOG_HELPER + r"""
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        pwm_set(0, 0);
        let msg = b"belt: halted for obstruction clear";
        log_msg(msg.as_ptr() as u32, msg.len() as u32);
    }
}
"""

# ── Factory: arm ─────────────────────────────────────────────────────────

# One full clear-sweep cycle: joint 0 sweeps to 180° and back, confirming
# with the limit switches; used to clear a jammed item off the belt.
ARM_CLEAR_SWEEP = LOG_HELPER + r"""
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let msg = b"arm: clear sweep start";
        log_msg(msg.as_ptr() as u32, msg.len() as u32);
        pwm_set(0, 10000);
        let mut reached = 0u32;
        for _ in 0..40u32 {
            if gpio_get(21) == 1 {
                reached = 1;
                break;
            }
            delay_ms(100);
        }
        log_kv(b"REACHED_END", reached);
        pwm_set(0, 0);
        let mut home = 0u32;
        for _ in 0..40u32 {
            if gpio_get(20) == 1 {
                home = 1;
                break;
            }
            delay_ms(100);
        }
        log_kv(b"HOME", home);
        let done = b"arm: clear sweep complete";
        log_msg(done.as_ptr() as u32, done.len() as u32);
    }
}
"""
