#![cfg_attr(feature = "esp32c3", no_std)]
#![cfg_attr(feature = "esp32c3", no_main)]

// ── ESP32-C3 entry point ──────────────────────────────────────────────
#[cfg(feature = "esp32c3")]
esp_bootloader_esp_idf::esp_app_desc!();

#[cfg(feature = "esp32c3")]
mod esp_entry {
    extern crate alloc;
    use alloc::vec;
    use esp_alloc as _;
    use esp_hal::analog::adc::{AdcConfig, Attenuation};
    use esp_hal::i2c::master::{Config as I2cConfig, I2c};
    use esp_hal::gpio::DriveMode;
    use esp_hal::ledc::channel::{self, ChannelIFace};
    use esp_hal::ledc::timer::{self, config::Duty, TimerIFace};
    use esp_hal::ledc::{LSGlobalClkSource, Ledc, LowSpeed};
    use esp_hal::time::Rate;
    use esp_hal::usb_serial_jtag::UsbSerialJtag;

    use lial_receiver::embedded_hal_adapter::PwmAdapter;
    use lial_receiver::esp32c3::Esp32C3Hal;
    use lial_receiver::link::{Frame, OP_BYTECODE_PUSH, OP_DISCOVERY, OP_EXEC_RESULT};
    use lial_receiver::manifest::{
        build as build_manifest, AdcCapability, Capabilities, GpioCapability, I2cCapability,
        ManifestHeader, PwmCapability,
    };
    use lial_receiver::LialRuntime;

    #[panic_handler]
    fn panic(_info: &core::panic::PanicInfo) -> ! {
        loop {}
    }

    type Hal<'d> = Esp32C3Hal<
        'd,
        esp_hal::usb_serial_jtag::UsbSerialJtagTx<'d, esp_hal::Blocking>,
        esp_hal::usb_serial_jtag::UsbSerialJtagRx<'d, esp_hal::Blocking>,
    >;

    fn read_frame(hal: &mut Hal) -> Frame {
        let mut header = [0u8; 5];
        hal.read_exact(&mut header);
        let opcode = header[0];
        let len =
            u32::from_be_bytes([header[1], header[2], header[3], header[4]]) as usize;
        let mut payload = vec![0u8; len];
        if len > 0 {
            hal.read_exact(&mut payload);
        }
        Frame::new(opcode, payload)
    }

    fn write_frame(hal: &mut Hal, frame: &Frame) {
        let bytes = frame.serialize();
        hal.write_bytes(&bytes);
    }

    #[esp_hal::main]
    fn main() -> ! {
        esp_alloc::heap_allocator!(size: 200 * 1024);

        let config = esp_hal::Config::default();
        let peripherals = esp_hal::init(config);

        // ── LEDC PWM on GPIO 5: external LED ────────────────────────────
        // To revert to plain GPIO, replace this block with:
        //   use esp_hal::gpio::{Level, Output, OutputConfig};
        //   use lial_receiver::embedded_hal_adapter::OutputPinAdapter;
        //   let led = Output::new(peripherals.GPIO5, Level::Low, OutputConfig::default());
        //   let led_pwm: alloc::boxed::Box<dyn lial_receiver::embedded_hal_adapter::DynPwm>
        //       = alloc::boxed::Box::new(lial_receiver::embedded_hal_adapter::GpioPwmFallback(led));
        let mut ledc = Ledc::new(peripherals.LEDC);
        ledc.set_global_slow_clock(LSGlobalClkSource::APBClk);

        let mut lstimer0 = ledc.timer::<LowSpeed>(timer::Number::Timer0);
        lstimer0
            .configure(timer::config::Config {
                duty: Duty::Duty10Bit,
                clock_source: timer::LSClockSource::APBClk,
                frequency: Rate::from_hz(5000),
            })
            .expect("LEDC timer config failed");
        // Leak the timer so the channel can hold a 'static reference to it.
        // Safe: main() -> ! never returns, so this memory is never freed.
        let lstimer0 = alloc::boxed::Box::leak(alloc::boxed::Box::new(lstimer0));

        let mut channel0 = ledc.channel(channel::Number::Channel0, peripherals.GPIO5);
        channel0
            .configure(channel::config::Config {
                timer: lstimer0,
                duty_pct: 0,
                drive_mode: DriveMode::PushPull,
            })
            .expect("LEDC channel config failed");

        let led_pwm: alloc::boxed::Box<dyn lial_receiver::embedded_hal_adapter::DynPwm>
            = alloc::boxed::Box::new(PwmAdapter(channel0));

        // ── I2C0: SDA=GPIO8, SCL=GPIO9 (SSD1306 OLED) ───────────────
        let i2c = I2c::new(peripherals.I2C0, I2cConfig::default())
            .expect("I2C init failed")
            .with_sda(peripherals.GPIO8)
            .with_scl(peripherals.GPIO9);

        // ── ADC1: potentiometer on GPIO2, 11dB attenuation (0-3.3V) ──
        let mut adc1_config = AdcConfig::new();
        let adc_pin = adc1_config.enable_pin(peripherals.GPIO2, Attenuation::_11dB);
        let adc1 = esp_hal::analog::adc::Adc::new(peripherals.ADC1, adc1_config);

        // ── USB Serial JTAG ──────────────────────────────────────────
        let usb_serial = UsbSerialJtag::new(peripherals.USB_DEVICE);
        let (rx, tx) = usb_serial.split();

        // ── Assemble Hal ─────────────────────────────────────────────
        let mut hal = Esp32C3Hal::new(led_pwm, i2c, adc1, adc_pin, tx, rx);

        // ── I2C bus scan at boot ─────────────────────────────────────
        let i2c_devices = hal.adapter_mut().scan_i2c_bus(0);

        // ── Discovery manifest ───────────────────────────────────────
        let header = ManifestHeader {
            board: "esp32c3",
            family: "esp32",
            firmware_version: env!("CARGO_PKG_VERSION"),
            ram_kb: 400,
            flash_kb: 4096,
            max_wasm_memory_kb: 64,
            max_wasm_stack_kb: 8,
            fuel_default: 500_000_000,
        };
        let caps = Capabilities {
            gpio: GpioCapability {
                pins: alloc::vec![5],
            },
            pwm: PwmCapability {
                pins: alloc::vec![5],
                resolution_bits: 14,
            },
            adc: AdcCapability {
                pins: alloc::vec![0],
                resolution_bits: 12,
                vref_mv: 3300,
            },
            i2c: alloc::vec![I2cCapability {
                bus_id: 0,
                sda_pin: 8,
                scl_pin: 9,
                devices_present: i2c_devices,
            }],
            ..Default::default()
        };
        let manifest_json = build_manifest(&header, &caps);

        // ── Main loop ────────────────────────────────────────────────
        // The host initiates all exchanges. Supported opcodes:
        //   OP_DISCOVERY    → reply with the capability manifest
        //   OP_BYTECODE_PUSH → execute wasm, reply with exec result
        loop {
            let frame = read_frame(&mut hal);

            if frame.opcode == OP_DISCOVERY {
                let discovery = Frame::new(OP_DISCOVERY, manifest_json.as_bytes().to_vec());
                write_frame(&mut hal, &discovery);
                continue;
            }

            if frame.opcode != OP_BYTECODE_PUSH {
                let err = Frame::new(
                    OP_EXEC_RESULT,
                    alloc::format!(
                        r#"{{"error":"unexpected opcode 0x{:02x}"}}"#,
                        frame.opcode
                    )
                    .into_bytes(),
                );
                write_frame(&mut hal, &err);
                continue;
            }

            let mut runtime = LialRuntime::new(hal, Some(500_000_000));
            let result_json = match runtime.execute(&frame.payload, "run_logic") {
                Ok(logs) => {
                    alloc::format!(r#"{{"ok":true,"logs":{}}}"#, {
                        let mut s = alloc::string::String::from("[");
                        for (i, log) in logs.iter().enumerate() {
                            if i > 0 { s.push(','); }
                            s.push('"');
                            s.push_str(log);
                            s.push('"');
                        }
                        s.push(']');
                        s
                    })
                }
                Err(e) => {
                    alloc::format!(r#"{{"ok":false,"error":"{}"}}"#, e)
                }
            };

            hal = runtime.into_hardware();

            let resp = Frame::new(OP_EXEC_RESULT, result_json.into_bytes());
            write_frame(&mut hal, &resp);
        }
    }
}

// ── Laptop (std) entry point ──────────────────────────────────────────
#[cfg(feature = "std")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use lial_receiver::link::{self, Frame, OP_BYTECODE_PUSH, OP_DISCOVERY, OP_EXEC_RESULT};
    use lial_receiver::manifest::{
        build as build_manifest, AdcCapability, Capabilities, GpioCapability, ManifestHeader,
        PwmCapability, UartCapability,
    };
    use lial_receiver::mock::LaptopMock;
    use lial_receiver::LialRuntime;
    use std::fs;
    use std::io;

    let laptop_header = ManifestHeader {
        board: "laptop-mock",
        family: "mock",
        firmware_version: env!("CARGO_PKG_VERSION"),
        ram_kb: 4096,
        flash_kb: 0,
        max_wasm_memory_kb: 256,
        max_wasm_stack_kb: 32,
        fuel_default: 10_000_000,
    };
    let laptop_caps = Capabilities {
        gpio: GpioCapability {
            pins: vec![0, 1, 2, 3, 4, 5],
        },
        pwm: PwmCapability {
            pins: vec![0, 1, 2, 3, 4, 5],
            resolution_bits: 16,
        },
        adc: AdcCapability {
            pins: vec![0, 1, 2, 3],
            resolution_bits: 12,
            vref_mv: 3300,
        },
        uart: vec![UartCapability {
            bus_id: 1,
            tx_pin: 0,
            rx_pin: 0,
        }],
        ..Default::default()
    };
    let hardware_manifest = build_manifest(&laptop_header, &laptop_caps);

    let args: Vec<String> = std::env::args().collect();

    let mut fuel: Option<u64> = None;
    let mut wasm_path: Option<String> = None;
    let mut stdin_mode = false;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--fuel" => {
                i += 1;
                fuel = Some(args[i].parse().expect("--fuel requires a number"));
            }
            "--stdin" => {
                stdin_mode = true;
            }
            other => {
                wasm_path = Some(other.to_string());
            }
        }
        i += 1;
    }

    if stdin_mode {
        let mut stdin = io::stdin().lock();
        let mut stdout = io::stdout().lock();

        loop {
            let frame = match link::read_frame(&mut stdin) {
                Ok(f) => f,
                Err(link::LinkError::ConnectionClosed) => break,
                Err(e) => {
                    let err_json = format!(r#"{{"error":"{}"}}"#, e);
                    let resp = Frame::new(OP_EXEC_RESULT, err_json.into_bytes());
                    link::write_frame(&mut stdout, &resp)?;
                    continue;
                }
            };

            if frame.opcode == OP_DISCOVERY {
                let manifest_frame =
                    Frame::new(OP_DISCOVERY, hardware_manifest.as_bytes().to_vec());
                link::write_frame(&mut stdout, &manifest_frame)?;
                continue;
            }

            if frame.opcode != OP_BYTECODE_PUSH {
                let err_json = format!(
                    r#"{{"error":"unexpected opcode 0x{:02x}, expected 0x01 or 0x02"}}"#,
                    frame.opcode
                );
                let resp = Frame::new(OP_EXEC_RESULT, err_json.into_bytes());
                link::write_frame(&mut stdout, &resp)?;
                continue;
            }

            let hw = LaptopMock::new();
            let mut runtime = LialRuntime::new(hw, fuel);

            let result_json = match runtime.execute(&frame.payload, "run_logic") {
                Ok(logs) => {
                    format!(
                        r#"{{"ok":true,"logs":{}}}"#,
                        serde_json::to_string(&logs).unwrap_or_else(|_| "[]".into())
                    )
                }
                Err(e) => {
                    format!(r#"{{"ok":false,"error":"{}"}}"#, e)
                }
            };

            let resp = Frame::new(OP_EXEC_RESULT, result_json.into_bytes());
            link::write_frame(&mut stdout, &resp)?;
        }

        return Ok(());
    }

    if let Some(path) = wasm_path {
        eprintln!("LIAL Receiver Active (Rust Engine)");
        let hw = LaptopMock::new();
        let mut runtime = LialRuntime::new(hw, fuel);

        eprintln!("Loading bytecode from: {path}");
        let wasm_bytes = fs::read(&path)?;

        eprintln!("Executing LIAL Driver...");
        match runtime.execute(&wasm_bytes, "run_logic") {
            Ok(logs) => {
                eprintln!("Task Complete. ({} log entries)", logs.len());
            }
            Err(e) => {
                eprintln!("Execution failed: {e}");
                std::process::exit(1);
            }
        }
        return Ok(());
    }

    eprintln!("Usage: lial-receiver [--fuel N] <wasm_path>");
    eprintln!("       lial-receiver [--fuel N] --stdin");
    std::process::exit(1);
}

#[cfg(all(not(feature = "std"), not(feature = "esp32c3")))]
fn main() {}
