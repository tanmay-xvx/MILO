//! Simulated hardware profiles — the virtual fleet emulator (std only).
//!
//! Each profile implements `MiloHardware` with a small continuous-time model
//! stepped from wall-clock time, so LLM-generated drivers run against
//! peripherals that *react*: motors produce thrust, heaters raise
//! temperatures, belts move items past sensors. The wasm runtime, syscall
//! ABI, MILO-Link framing, validation, and fuel metering are exactly the
//! code that ships to real boards — only the peripheral backend is simulated.
//!
//! Telemetry: when `MILO_SIM_TELEMETRY` is set to a file path, every physics
//! step appends a JSONL snapshot, giving demos a measurable evidence trail.
//!
//! Fault injection (for resilience scenarios) uses parameter slot 7, which
//! the *host* writes via OP_SET_PARAM:
//!   drone:    fault != 0 → motor 2 delivers only 40% of commanded thrust
//!   conveyor: fault != 0 → item sensor stops pulsing (jam)
//!   oven:     fault != 0 → heater efficiency +35% (thermal runaway drift)
//! Slot 7 is reserved by convention; slots 0–5 are free for application use.

use crate::MiloHardware;
use std::io::Write as _;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

/// Cooperative-stop flag checked by `delay_ms`. Set by the executor's stop
/// hook; cleared when a new module starts.
static SIM_STOPPED: AtomicBool = AtomicBool::new(false);

pub fn sim_stop_hook() {
    SIM_STOPPED.store(true, Ordering::SeqCst);
}

pub fn sim_start_hook() {
    SIM_STOPPED.store(false, Ordering::SeqCst);
}

const FAULT_SLOT: u32 = 7;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SimProfile {
    /// Quadcopter: PWM 0–3 = motors, ADC 0 = battery (mV/2), I2C 0x68 = IMU
    /// (altitude in cm, u16 BE). Hover at ~50% average duty.
    Drone,
    /// Belt: PWM 0 = motor, GPIO 26 = item photo-sensor, ADC 1 = motor
    /// temperature (tenths of °C). Items pass proportional to belt speed.
    Conveyor,
    /// Reflow oven: PWM 0 = heater, ADC 0 = chamber temperature (tenths of
    /// °C). First-order thermal response toward 25 °C + duty · 300 °C.
    Oven,
    /// 3-joint arm: PWM 0–2 = joint targets, ADC 0 = gripper force, GPIO
    /// 20/21 = limit switches. Joints slew toward targets at bounded rate.
    Arm,
}

impl SimProfile {
    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "drone" => Some(Self::Drone),
            "conveyor" => Some(Self::Conveyor),
            "oven" => Some(Self::Oven),
            "arm" => Some(Self::Arm),
            _ => None,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Drone => "drone",
            Self::Conveyor => "conveyor",
            Self::Oven => "oven",
            Self::Arm => "arm",
        }
    }
}

pub struct SimHal {
    profile: SimProfile,
    name: String,
    start: Instant,
    last_step: f64, // seconds since start at last physics step
    telemetry: Option<std::fs::File>,
    last_telemetry: f64,

    pwm: [f64; 8],   // duty 0.0..1.0 per channel
    gpio: [u32; 32], // written GPIO states

    // Drone state
    altitude_m: f64,
    vertical_v: f64,
    battery_mv: f64,

    // Conveyor state
    belt_pos: f64, // items are 1.0 apart; sensor pulses each integer crossing
    items_passed: u64,
    motor_temp_c: f64,

    // Oven state
    oven_temp_c: f64,

    // Arm state
    joints_deg: [f64; 3],
}

impl SimHal {
    pub fn new(profile: SimProfile, name: &str) -> Self {
        let telemetry = std::env::var("MILO_SIM_TELEMETRY").ok().map(|path| {
            std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(path)
                .expect("cannot open MILO_SIM_TELEMETRY file")
        });
        Self {
            profile,
            name: String::from(name),
            start: Instant::now(),
            last_step: 0.0,
            telemetry,
            last_telemetry: 0.0,
            pwm: [0.0; 8],
            gpio: [0; 32],
            altitude_m: 0.0,
            vertical_v: 0.0,
            battery_mv: 4200.0,
            belt_pos: 0.0,
            items_passed: 0,
            motor_temp_c: 25.0,
            oven_temp_c: 25.0,
            joints_deg: [0.0; 3],
        }
    }

    fn now_s(&self) -> f64 {
        self.start.elapsed().as_secs_f64()
    }

    fn fault_active(&self) -> bool {
        crate::engine::executor::get_param(FAULT_SLOT) != 0
    }

    /// Advance the physics model to the current wall-clock time.
    fn step(&mut self) {
        let now = self.now_s();
        let mut dt = now - self.last_step;
        self.last_step = now;
        if dt <= 0.0 {
            return;
        }
        // Clamp huge gaps (first call, debugger pauses) to keep models stable.
        if dt > 0.5 {
            dt = 0.5;
        }

        match self.profile {
            SimProfile::Drone => {
                let mut thrust: f64 = self.pwm[..4].iter().sum::<f64>() / 4.0;
                if self.fault_active() {
                    // Motor 2 degraded to 40% output.
                    thrust = (self.pwm[0] + self.pwm[1] + self.pwm[2] * 0.4 + self.pwm[3]) / 4.0;
                }
                // Hover at 50% duty; net accel scales ±6 m/s² with drag.
                let accel = (thrust - 0.5) * 12.0 - self.vertical_v * 0.8;
                self.vertical_v += accel * dt;
                self.altitude_m += self.vertical_v * dt;
                if self.altitude_m < 0.0 {
                    self.altitude_m = 0.0;
                    if self.vertical_v < 0.0 {
                        self.vertical_v = 0.0;
                    }
                }
                self.battery_mv -= thrust * 25.0 * dt;
                if self.battery_mv < 3300.0 {
                    self.battery_mv = 3300.0;
                }
            }
            SimProfile::Conveyor => {
                let speed = self.pwm[0]; // items per second at 100% duty: 2.0
                self.belt_pos += speed * 2.0 * dt;
                while self.belt_pos >= 1.0 {
                    self.belt_pos -= 1.0;
                    self.items_passed += 1;
                }
                // Motor heats toward 25 + duty·55 °C, tau ≈ 8 s.
                let target = 25.0 + self.pwm[0] * 55.0;
                self.motor_temp_c += (target - self.motor_temp_c) * (dt / 8.0);
            }
            SimProfile::Oven => {
                let mut eff = 1.0;
                if self.fault_active() {
                    eff = 1.35; // heater drift → runaway if control law unchanged
                }
                let target = 25.0 + self.pwm[0] * 300.0 * eff;
                self.oven_temp_c += (target - self.oven_temp_c) * (dt / 6.0);
            }
            SimProfile::Arm => {
                for j in 0..3 {
                    let target = self.pwm[j] * 180.0;
                    let diff = target - self.joints_deg[j];
                    let max_step = 90.0 * dt; // 90°/s slew
                    self.joints_deg[j] += diff.clamp(-max_step, max_step);
                }
            }
        }

        self.emit_telemetry(now);
    }

    fn emit_telemetry(&mut self, now: f64) {
        // Throttle to 20 Hz.
        if now - self.last_telemetry < 0.05 {
            return;
        }
        self.last_telemetry = now;
        let Some(file) = self.telemetry.as_mut() else {
            return;
        };
        let state = match self.profile {
            SimProfile::Drone => format!(
                r#"{{"alt_m":{:.3},"vz":{:.3},"battery_mv":{:.0},"thrust":[{:.3},{:.3},{:.3},{:.3}],"fault":{}}}"#,
                self.altitude_m,
                self.vertical_v,
                self.battery_mv,
                self.pwm[0],
                self.pwm[1],
                self.pwm[2],
                self.pwm[3],
                crate::engine::executor::get_param(FAULT_SLOT),
            ),
            SimProfile::Conveyor => format!(
                r#"{{"belt_duty":{:.3},"items":{},"motor_temp_c":{:.2},"fault":{}}}"#,
                self.pwm[0],
                self.items_passed,
                self.motor_temp_c,
                crate::engine::executor::get_param(FAULT_SLOT),
            ),
            SimProfile::Oven => format!(
                r#"{{"heater_duty":{:.3},"temp_c":{:.2},"fault":{}}}"#,
                self.pwm[0],
                self.oven_temp_c,
                crate::engine::executor::get_param(FAULT_SLOT),
            ),
            SimProfile::Arm => format!(
                r#"{{"joints_deg":[{:.1},{:.1},{:.1}]}}"#,
                self.joints_deg[0], self.joints_deg[1], self.joints_deg[2],
            ),
        };
        let line = format!(
            "{{\"t\":{:.3},\"device\":\"{}\",\"profile\":\"{}\",\"state\":{}}}\n",
            now,
            self.name,
            self.profile.as_str(),
            state
        );
        let _ = file.write_all(line.as_bytes());
    }
}

impl MiloHardware for SimHal {
    fn gpio_set(&mut self, pin: u32, state: u32) {
        self.step();
        if (pin as usize) < self.gpio.len() {
            self.gpio[pin as usize] = state;
        }
    }

    fn gpio_get(&mut self, pin: u32) -> u32 {
        self.step();
        match (self.profile, pin) {
            // Conveyor item photo-sensor: pulses as items pass. A jam fault
            // freezes the sensor low even though the belt is powered.
            (SimProfile::Conveyor, 26) => {
                if self.fault_active() {
                    0
                } else {
                    (self.belt_pos < 0.2) as u32
                }
            }
            // Arm limit switches at travel extremes.
            (SimProfile::Arm, 20) => (self.joints_deg[0] <= 1.0) as u32,
            (SimProfile::Arm, 21) => (self.joints_deg[0] >= 179.0) as u32,
            _ => {
                if (pin as usize) < self.gpio.len() {
                    self.gpio[pin as usize]
                } else {
                    0
                }
            }
        }
    }

    fn delay_ms(&mut self, ms: u32) {
        // Cooperative stop: once the host stops this module, delays become
        // no-ops so the driver's remaining loop unwinds in milliseconds.
        if !SIM_STOPPED.load(Ordering::SeqCst) {
            std::thread::sleep(std::time::Duration::from_millis(ms as u64));
        }
        self.step();
    }

    fn get_uptime_us(&self) -> u64 {
        self.start.elapsed().as_micros() as u64
    }

    fn i2c_transfer(&mut self, addr: u8, _tx: &[u8], rx: &mut [u8]) -> i32 {
        self.step();
        match (self.profile, addr) {
            // Drone IMU at 0x68: altitude in cm as u16 BE, then vz sign byte.
            (SimProfile::Drone, 0x68) => {
                let alt_cm = (self.altitude_m * 100.0).clamp(0.0, 65535.0) as u16;
                if rx.len() >= 2 {
                    rx[0..2].copy_from_slice(&alt_cm.to_be_bytes());
                }
                if rx.len() >= 3 {
                    rx[2] = if self.vertical_v >= 0.0 { 1 } else { 0 };
                }
                0
            }
            _ => -1,
        }
    }

    fn log(&mut self, message: &str) {
        let now = self.now_s();
        if let Some(file) = self.telemetry.as_mut() {
            let escaped = crate::json_escape(message);
            let line = format!(
                "{{\"t\":{:.3},\"device\":\"{}\",\"log\":\"{}\"}}\n",
                now, self.name, escaped
            );
            let _ = file.write_all(line.as_bytes());
        }
        eprintln!("[{}] {}", self.name, message);
    }

    fn pwm_set(&mut self, channel: u32, duty_0_10000: u32) {
        self.step();
        if (channel as usize) < self.pwm.len() {
            self.pwm[channel as usize] = (duty_0_10000.min(10000)) as f64 / 10000.0;
        }
    }

    fn adc_read(&mut self, channel: u32) -> u32 {
        self.step();
        match (self.profile, channel) {
            // Battery: mV/2 so 4200 mV → 2100 counts (fits 12-bit).
            (SimProfile::Drone, 0) => (self.battery_mv / 2.0) as u32,
            (SimProfile::Conveyor, 1) => (self.motor_temp_c * 10.0) as u32,
            (SimProfile::Oven, 0) => (self.oven_temp_c * 10.0) as u32,
            (SimProfile::Arm, 0) => (self.pwm[3] * 4095.0) as u32,
            _ => 0,
        }
    }

    fn spi_transfer(&mut self, _bus: u32, tx: &[u8], rx: &mut [u8]) -> i32 {
        let n = core::cmp::min(tx.len(), rx.len());
        rx[..n].copy_from_slice(&tx[..n]);
        0
    }

    fn uart_write(&mut self, _bus: u32, data: &[u8]) -> i32 {
        data.len() as i32
    }

    fn uart_read(&mut self, _bus: u32, _buf: &mut [u8], _timeout_ms: u32) -> i32 {
        0
    }
}

/// Build the discovery manifest for a sim profile.
pub fn sim_manifest(profile: SimProfile, name: &str) -> String {
    use crate::engine::manifest::{
        build, AdcCapability, Capabilities, GpioCapability, I2cCapability, ManifestHeader,
        PwmCapability,
    };

    let header = ManifestHeader {
        board: match profile {
            SimProfile::Drone => "sim-drone",
            SimProfile::Conveyor => "sim-conveyor",
            SimProfile::Oven => "sim-oven",
            SimProfile::Arm => "sim-arm",
        },
        family: "sim",
        firmware_version: env!("CARGO_PKG_VERSION"),
        ram_kb: 4096,
        flash_kb: 0,
        max_wasm_memory_kb: 64,
        max_wasm_stack_kb: 8,
        fuel_default: 500_000_000,
    };

    let caps = match profile {
        SimProfile::Drone => Capabilities {
            gpio: GpioCapability { pins: vec![25] },
            pwm: PwmCapability {
                pins: vec![0, 1, 2, 3],
                resolution_bits: 14,
            },
            adc: AdcCapability {
                pins: vec![0],
                resolution_bits: 12,
                vref_mv: 3300,
            },
            i2c: vec![I2cCapability {
                bus_id: 0,
                sda_pin: 4,
                scl_pin: 5,
                devices_present: vec![0x68],
            }],
            ..Default::default()
        },
        SimProfile::Conveyor => Capabilities {
            gpio: GpioCapability { pins: vec![25, 26] },
            pwm: PwmCapability {
                pins: vec![0],
                resolution_bits: 14,
            },
            adc: AdcCapability {
                pins: vec![1],
                resolution_bits: 12,
                vref_mv: 3300,
            },
            ..Default::default()
        },
        SimProfile::Oven => Capabilities {
            gpio: GpioCapability { pins: vec![25] },
            pwm: PwmCapability {
                pins: vec![0],
                resolution_bits: 14,
            },
            adc: AdcCapability {
                pins: vec![0],
                resolution_bits: 12,
                vref_mv: 3300,
            },
            ..Default::default()
        },
        SimProfile::Arm => Capabilities {
            gpio: GpioCapability {
                pins: vec![20, 21, 25],
            },
            pwm: PwmCapability {
                pins: vec![0, 1, 2, 3],
                resolution_bits: 14,
            },
            adc: AdcCapability {
                pins: vec![0],
                resolution_bits: 12,
                vref_mv: 3300,
            },
            ..Default::default()
        },
    };

    let mut manifest = build(&header, &caps);
    // Tag the device name into the manifest for fleet bookkeeping.
    manifest.replace_range(
        0..1,
        &format!("{{\"name\":\"{}\",", crate::json_escape(name)),
    );
    manifest
}
