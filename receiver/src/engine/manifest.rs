//! Discovery manifest builder.
//!
//! Produces a JSON payload that the host receives on connection. The schema
//! is intentionally board-agnostic -- board modules (`esp32c3.rs` etc.) fill
//! in their specific capabilities and this module serialises them.
//!
//! This is deliberately hand-rolled JSON (no `serde_derive`) to keep the
//! `no_std` footprint tiny on flash-constrained targets.

use alloc::format;
use alloc::string::String;
use alloc::vec::Vec;

/// Manifest header fields that apply to every capability set.
pub struct ManifestHeader<'a> {
    pub board: &'a str,
    pub family: &'a str,
    pub firmware_version: &'a str,
    pub ram_kb: u32,
    pub flash_kb: u32,
    pub max_wasm_memory_kb: u32,
    pub max_wasm_stack_kb: u32,
    pub fuel_default: u64,
}

#[derive(Default, Clone)]
pub struct GpioCapability {
    pub pins: Vec<u32>,
}

#[derive(Default, Clone)]
pub struct PwmCapability {
    pub pins: Vec<u32>,
    pub resolution_bits: u8,
}

#[derive(Default, Clone)]
pub struct AdcCapability {
    pub pins: Vec<u32>,
    pub resolution_bits: u8,
    pub vref_mv: u32,
}

#[derive(Clone)]
pub struct I2cCapability {
    pub bus_id: u32,
    pub sda_pin: u32,
    pub scl_pin: u32,
    /// 7-bit I2C addresses that responded to a scan at boot.
    pub devices_present: Vec<u8>,
}

#[derive(Clone)]
pub struct SpiCapability {
    pub bus_id: u32,
    pub mosi_pin: u32,
    pub miso_pin: u32,
    pub sck_pin: u32,
}

#[derive(Clone)]
pub struct UartCapability {
    pub bus_id: u32,
    pub tx_pin: u32,
    pub rx_pin: u32,
}

#[derive(Default)]
pub struct Capabilities {
    pub gpio: GpioCapability,
    pub pwm: PwmCapability,
    pub adc: AdcCapability,
    pub i2c: Vec<I2cCapability>,
    pub spi: Vec<SpiCapability>,
    pub uart: Vec<UartCapability>,
}

/// Build a capability manifest as pretty-ish JSON (one line, no pretty
/// printing -- keeps the serialisation free of heap churn).
pub fn build(header: &ManifestHeader<'_>, caps: &Capabilities) -> String {
    let mut s = String::with_capacity(512);
    s.push('{');
    json_string(&mut s, "board", header.board);
    s.push(',');
    json_string(&mut s, "family", header.family);
    s.push(',');
    json_string(&mut s, "firmware_version", header.firmware_version);
    s.push(',');

    s.push_str("\"capabilities\":{");
    json_array_u32(&mut s, "\"gpio\":{\"pins\":", &caps.gpio.pins);
    s.push('}');
    s.push(',');

    s.push_str("\"pwm\":{");
    json_u32_array_field(&mut s, "pins", &caps.pwm.pins);
    s.push(',');
    s.push_str(&format!("\"resolution_bits\":{}", caps.pwm.resolution_bits));
    s.push('}');
    s.push(',');

    s.push_str("\"adc\":{");
    json_u32_array_field(&mut s, "pins", &caps.adc.pins);
    s.push(',');
    s.push_str(&format!(
        "\"resolution_bits\":{},\"vref_mv\":{}",
        caps.adc.resolution_bits, caps.adc.vref_mv
    ));
    s.push('}');

    if !caps.i2c.is_empty() {
        s.push(',');
        s.push_str("\"i2c\":[");
        for (i, bus) in caps.i2c.iter().enumerate() {
            if i > 0 {
                s.push(',');
            }
            s.push_str(&format!(
                "{{\"bus_id\":{},\"sda_pin\":{},\"scl_pin\":{},\"devices_present\":[",
                bus.bus_id, bus.sda_pin, bus.scl_pin
            ));
            for (j, addr) in bus.devices_present.iter().enumerate() {
                if j > 0 {
                    s.push(',');
                }
                s.push_str(&format!("\"0x{:02x}\"", addr));
            }
            s.push_str("]}");
        }
        s.push(']');
    }

    if !caps.spi.is_empty() {
        s.push(',');
        s.push_str("\"spi\":[");
        for (i, bus) in caps.spi.iter().enumerate() {
            if i > 0 {
                s.push(',');
            }
            s.push_str(&format!(
                "{{\"bus_id\":{},\"mosi_pin\":{},\"miso_pin\":{},\"sck_pin\":{}}}",
                bus.bus_id, bus.mosi_pin, bus.miso_pin, bus.sck_pin
            ));
        }
        s.push(']');
    }

    if !caps.uart.is_empty() {
        s.push(',');
        s.push_str("\"uart\":[");
        for (i, bus) in caps.uart.iter().enumerate() {
            if i > 0 {
                s.push(',');
            }
            s.push_str(&format!(
                "{{\"bus_id\":{},\"tx_pin\":{},\"rx_pin\":{}}}",
                bus.bus_id, bus.tx_pin, bus.rx_pin
            ));
        }
        s.push(']');
    }

    s.push('}'); // close capabilities
    s.push(',');

    s.push_str(&format!(
        "\"memory\":{{\"ram_kb\":{},\"flash_kb\":{}}}",
        header.ram_kb, header.flash_kb
    ));
    s.push(',');
    s.push_str(&format!(
        "\"wasm_limits\":{{\"max_memory_kb\":{},\"max_stack_kb\":{},\"fuel_default\":{}}}",
        header.max_wasm_memory_kb, header.max_wasm_stack_kb, header.fuel_default
    ));

    s.push(',');
    s.push_str("\"alphabet\":[");
    for (i, name) in ALPHABET.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push('"');
        s.push_str(name);
        s.push('"');
    }
    s.push(']');

    s.push('}');
    s
}

/// The canonical list of syscalls the current receiver exposes. Keep in sync
/// with `MiloHardware` + `register_syscalls`.
pub const ALPHABET: &[&str] = &[
    "gpio_set",
    "gpio_get",
    "delay_ms",
    "get_uptime_us",
    "i2c_transfer",
    "log_msg",
    "pwm_set",
    "adc_read",
    "spi_transfer",
    "uart_write",
    "uart_read",
    "get_param",
];

fn json_string(s: &mut String, key: &str, value: &str) {
    s.push('"');
    s.push_str(key);
    s.push_str("\":\"");
    for c in value.chars() {
        match c {
            '"' => s.push_str("\\\""),
            '\\' => s.push_str("\\\\"),
            '\n' => s.push_str("\\n"),
            '\r' => s.push_str("\\r"),
            '\t' => s.push_str("\\t"),
            c if (c as u32) < 0x20 => s.push_str(&format!("\\u{:04x}", c as u32)),
            c => s.push(c),
        }
    }
    s.push('"');
}

fn json_array_u32(s: &mut String, prefix: &str, vs: &[u32]) {
    s.push_str(prefix);
    s.push('[');
    for (i, v) in vs.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!("{}", v));
    }
    s.push(']');
}

fn json_u32_array_field(s: &mut String, key: &str, vs: &[u32]) {
    s.push('"');
    s.push_str(key);
    s.push_str("\":[");
    for (i, v) in vs.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        s.push_str(&format!("{}", v));
    }
    s.push(']');
}

#[cfg(all(test, feature = "std"))]
mod tests {
    use super::*;

    fn header() -> ManifestHeader<'static> {
        ManifestHeader {
            board: "esp32c3",
            family: "esp32",
            firmware_version: "0.3.0",
            ram_kb: 400,
            flash_kb: 4096,
            max_wasm_memory_kb: 64,
            max_wasm_stack_kb: 4,
            fuel_default: 1_000_000,
        }
    }

    #[test]
    fn emits_valid_json_minimal() {
        let caps = Capabilities {
            gpio: GpioCapability {
                pins: alloc::vec![5],
            },
            ..Default::default()
        };
        let s = build(&header(), &caps);
        let v: serde_json::Value =
            serde_json::from_str(&s).expect("manifest must be valid JSON");
        assert_eq!(v["board"], "esp32c3");
        assert_eq!(v["capabilities"]["gpio"]["pins"][0], 5);
    }

    #[test]
    fn includes_all_capability_types() {
        let caps = Capabilities {
            gpio: GpioCapability {
                pins: alloc::vec![0, 1, 2, 3, 4, 5],
            },
            pwm: PwmCapability {
                pins: alloc::vec![0, 1, 2, 3, 4, 5],
                resolution_bits: 14,
            },
            adc: AdcCapability {
                pins: alloc::vec![0, 1, 2, 3, 4],
                resolution_bits: 12,
                vref_mv: 3300,
            },
            i2c: alloc::vec![I2cCapability {
                bus_id: 0,
                sda_pin: 8,
                scl_pin: 9,
                devices_present: alloc::vec![0x48, 0x76],
            }],
            spi: alloc::vec![SpiCapability {
                bus_id: 0,
                mosi_pin: 7,
                miso_pin: 2,
                sck_pin: 6,
            }],
            uart: alloc::vec![UartCapability {
                bus_id: 1,
                tx_pin: 20,
                rx_pin: 21,
            }],
        };
        let s = build(&header(), &caps);
        let v: serde_json::Value = serde_json::from_str(&s).unwrap();
        assert_eq!(v["capabilities"]["adc"]["resolution_bits"], 12);
        assert_eq!(v["capabilities"]["adc"]["vref_mv"], 3300);
        let i2c_devs = v["capabilities"]["i2c"][0]["devices_present"]
            .as_array()
            .unwrap();
        assert!(i2c_devs.iter().any(|x| x == "0x48"));
        assert!(i2c_devs.iter().any(|x| x == "0x76"));
        assert_eq!(v["capabilities"]["uart"][0]["tx_pin"], 20);
        assert_eq!(v["wasm_limits"]["fuel_default"], 1_000_000);

        let alphabet = v["alphabet"].as_array().unwrap();
        assert_eq!(alphabet.len(), 12);
        assert!(alphabet.iter().any(|x| x == "pwm_set"));
        assert!(alphabet.iter().any(|x| x == "uart_read"));
        assert!(alphabet.iter().any(|x| x == "get_param"));
    }

    #[test]
    fn json_string_escapes_special_chars() {
        let mut out = String::new();
        json_string(&mut out, "k", "a\"b\\c\nd");
        assert!(out.contains("\\\""));
        assert!(out.contains("\\\\"));
        assert!(out.contains("\\n"));
    }
}
