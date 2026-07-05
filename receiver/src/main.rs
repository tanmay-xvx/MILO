#![cfg_attr(any(feature = "esp32c3", feature = "rp2040"), no_std)]
#![cfg_attr(any(feature = "esp32c3", feature = "rp2040"), no_main)]

// ── ESP32-C3 entry point ──────────────────────────────────────────────
#[cfg(feature = "esp32c3")]
esp_bootloader_esp_idf::esp_app_desc!();

#[cfg(feature = "esp32c3")]
mod esp_entry {
    extern crate alloc;
    use esp_alloc as _;
    use esp_hal::analog::adc::{AdcConfig, Attenuation};
    use esp_hal::i2c::master::{Config as I2cConfig, I2c};
    use esp_hal::gpio::DriveMode;
    use esp_hal::ledc::channel::{self, ChannelIFace};
    use esp_hal::ledc::timer::{self, config::Duty, TimerIFace};
    use esp_hal::ledc::{LSGlobalClkSource, Ledc, LowSpeed};
    use esp_hal::time::Rate;
    use esp_hal::usb_serial_jtag::UsbSerialJtag;

    use milo_receiver::hal::adapter::PwmAdapter;
    use milo_receiver::targets::esp32c3::Esp32C3Hal;
    use milo_receiver::engine::link::{Frame, OP_BYTECODE_PUSH, OP_DISCOVERY, OP_EXEC_RESULT};
    use milo_receiver::engine::manifest::{
        build as build_manifest, AdcCapability, Capabilities, GpioCapability, I2cCapability,
        ManifestHeader, PwmCapability,
    };
    use milo_receiver::transport::{EmbeddedIoTransport, MiloTransport};
    use milo_receiver::MiloRuntime;

    #[panic_handler]
    fn panic(_info: &core::panic::PanicInfo) -> ! {
        loop {}
    }

    #[esp_hal::main]
    fn main() -> ! {
        esp_alloc::heap_allocator!(size: 200 * 1024);

        let config = esp_hal::Config::default();
        let peripherals = esp_hal::init(config);

        // ── LEDC PWM on GPIO 5: external LED ────────────────────────────
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
        let lstimer0 = alloc::boxed::Box::leak(alloc::boxed::Box::new(lstimer0));

        let mut channel0 = ledc.channel(channel::Number::Channel0, peripherals.GPIO5);
        channel0
            .configure(channel::config::Config {
                timer: lstimer0,
                duty_pct: 0,
                drive_mode: DriveMode::PushPull,
            })
            .expect("LEDC channel config failed");

        let led_pwm: alloc::boxed::Box<dyn milo_receiver::hal::adapter::DynPwm>
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

        // ── Assemble transport and hardware separately ────────────────
        let mut transport = EmbeddedIoTransport::new(tx, rx);
        let mut hal = Esp32C3Hal::new(led_pwm, i2c, adc1, adc_pin);

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
                resolution_bits: 10,
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

        // ── Main loop (transport-agnostic) ────────────────────────────
        milo_receiver::main_loop(&mut transport, hal, &manifest_json);
    }
}

// ── Raspberry Pi Pico (RP2040) entry point ────────────────────────────
#[cfg(feature = "rp2040")]
mod pico_entry {
    extern crate alloc;
    use embedded_alloc::LlffHeap as Heap;

    #[global_allocator]
    static HEAP: Heap = Heap::empty();

    /// Boot2 bootloader for W25Q080 flash (standard on Raspberry Pi Pico).
    #[unsafe(link_section = ".boot2")]
    #[used]
    pub static BOOT2: [u8; 256] = rp2040_boot2::BOOT_LOADER_W25Q080;

    use cortex_m_rt::entry;
    use rp2040_hal::clocks::init_clocks_and_plls;
    use rp2040_hal::gpio::Pins;
    use rp2040_hal::pac;
    use rp2040_hal::watchdog::Watchdog;
    use rp2040_hal::Sio;
    use rp2040_hal::Timer;
    use rp2040_hal::fugit::RateExtU32;
    use usb_device::class_prelude::UsbBusAllocator;
    use usb_device::prelude::*;
    use usbd_serial::SerialPort;

    use milo_receiver::engine::link::{Frame, OP_BYTECODE_PUSH, OP_DISCOVERY, OP_EXEC_RESULT};
    use milo_receiver::engine::manifest::{
        build as build_manifest, AdcCapability, Capabilities, GpioCapability, I2cCapability,
        ManifestHeader,
    };
    use milo_receiver::targets::rp2040::{self, Rp2040AdcChannel, Rp2040Hal};
    use milo_receiver::MiloRuntime;

    const XTAL_FREQ_HZ: u32 = 12_000_000;
    const HEAP_SIZE: usize = 150 * 1024;

    #[panic_handler]
    fn panic(_info: &core::panic::PanicInfo) -> ! {
        loop {
            cortex_m::asm::wfe();
        }
    }

    #[entry]
    fn main() -> ! {
        // Initialize heap
        {
            use core::mem::MaybeUninit;
            static mut HEAP_MEM: [MaybeUninit<u8>; HEAP_SIZE] =
                [MaybeUninit::uninit(); HEAP_SIZE];
            unsafe {
                let heap_start = core::ptr::addr_of!(HEAP_MEM) as usize;
                HEAP.init(heap_start, HEAP_SIZE);
            }
        }

        let mut pac = pac::Peripherals::take().unwrap();
        let mut watchdog = Watchdog::new(pac.WATCHDOG);
        let sio = Sio::new(pac.SIO);

        let clocks = init_clocks_and_plls(
            XTAL_FREQ_HZ,
            pac.XOSC,
            pac.CLOCKS,
            pac.PLL_SYS,
            pac.PLL_USB,
            &mut pac.RESETS,
            &mut watchdog,
        )
        .ok()
        .unwrap();

        let timer = Timer::new(pac.TIMER, &mut pac.RESETS, &clocks);
        let timer: &'static Timer = unsafe {
            static mut TIMER_STORAGE: core::mem::MaybeUninit<Timer> =
                core::mem::MaybeUninit::uninit();
            let ptr = core::ptr::addr_of_mut!(TIMER_STORAGE);
            (*ptr).write(timer);
            &*(*ptr).as_ptr()
        };

        let pins = Pins::new(pac.IO_BANK0, pac.PADS_BANK0, sio.gpio_bank0, &mut pac.RESETS);

        // GPIO 25: onboard LED
        let led_pin = pins.gpio25.into_push_pull_output();

        // I2C0: GPIO 4 (SDA), GPIO 5 (SCL) — pull-ups required for I2C
        let sda_pin = pins.gpio4.into_pull_up_input().into_function();
        let scl_pin = pins.gpio5.into_pull_up_input().into_function();
        let i2c = rp2040_hal::i2c::I2C::i2c0(
            pac.I2C0,
            sda_pin,
            scl_pin,
            100.kHz(),
            &mut pac.RESETS,
            &clocks.system_clock,
        );

        // ADC channel 0 on GPIO 26 (potentiometer / analog input)
        let adc = rp2040_hal::adc::Adc::new(pac.ADC, &mut pac.RESETS);
        let adc_pin = rp2040_hal::adc::AdcPin::new(pins.gpio26.into_floating_input()).unwrap();
        let adc_channel = Rp2040AdcChannel { adc, pin: adc_pin };

        let mut hal = Rp2040Hal::new(led_pin, i2c, adc_channel, timer);

        // Skip I2C bus scan — it can leave write-only devices (SSD1306) in
        // a confused state, corrupting the bus for subsequent transfers.
        let i2c_devices = alloc::vec![0x3C];

        // Discovery manifest
        let header = ManifestHeader {
            board: "rp2040-pico",
            family: "rp2040",
            firmware_version: env!("CARGO_PKG_VERSION"),
            ram_kb: 264,
            flash_kb: 2048,
            max_wasm_memory_kb: 64,
            max_wasm_stack_kb: 8,
            fuel_default: 500_000_000,
        };
        let caps = Capabilities {
            gpio: GpioCapability {
                pins: alloc::vec![25],
            },
            adc: AdcCapability {
                pins: alloc::vec![26],
                resolution_bits: 12,
                vref_mv: 3300,
            },
            i2c: alloc::vec![I2cCapability {
                bus_id: 0,
                sda_pin: 4,
                scl_pin: 5,
                devices_present: i2c_devices,
            }],
            ..Default::default()
        };
        let manifest_json = build_manifest(&header, &caps);

        // USB CDC Serial transport
        static mut USB_BUS: Option<UsbBusAllocator<rp2040_hal::usb::UsbBus>> = None;
        let usb_bus_ref = unsafe {
            let ptr = core::ptr::addr_of_mut!(USB_BUS);
            (*ptr) = Some(UsbBusAllocator::new(rp2040_hal::usb::UsbBus::new(
                pac.USBCTRL_REGS,
                pac.USBCTRL_DPRAM,
                clocks.usb_clock,
                true,
                &mut pac.RESETS,
            )));
            (*ptr).as_ref().unwrap()
        };

        let mut serial = SerialPort::new(usb_bus_ref);
        let mut usb_dev = UsbDeviceBuilder::new(usb_bus_ref, UsbVidPid(0x2E8A, 0x000A))
            .strings(&[StringDescriptors::default()
                .manufacturer("MILO")
                .product("MILO Receiver (Pico)")
                .serial_number("MILO-PICO-001")])
            .unwrap()
            .device_class(usbd_serial::USB_CLASS_CDC)
            .build();

        // Chunked USB CDC write: splits large frames into 64-byte USB packets,
        // polling the USB bus between chunks so the host can drain them.
        fn usb_write_all(
            serial: &mut SerialPort<rp2040_hal::usb::UsbBus>,
            usb_dev: &mut UsbDevice<rp2040_hal::usb::UsbBus>,
            data: &[u8],
        ) {
            let mut offset = 0;
            while offset < data.len() {
                usb_dev.poll(&mut [serial]);
                match serial.write(&data[offset..]) {
                    Ok(n) if n > 0 => offset += n,
                    _ => {}
                }
            }
            // Final poll to flush the last packet
            usb_dev.poll(&mut [serial]);
        }

        // Main loop: poll USB and handle MILO-Link frames
        let mut frame_buf: alloc::vec::Vec<u8> = alloc::vec::Vec::new();
        let mut read_buf = [0u8; 64];

        loop {
            usb_dev.poll(&mut [&mut serial]);

            match serial.read(&mut read_buf) {
                Ok(count) if count > 0 => {
                    frame_buf.extend_from_slice(&read_buf[..count]);
                }
                _ => {
                    continue;
                }
            }

            // Try to parse a complete frame from the buffer
            while frame_buf.len() >= 5 {
                let opcode = frame_buf[0];
                let plen = u32::from_be_bytes([
                    frame_buf[1],
                    frame_buf[2],
                    frame_buf[3],
                    frame_buf[4],
                ]) as usize;

                if frame_buf.len() < 5 + plen {
                    break;
                }

                let payload = frame_buf[5..5 + plen].to_vec();
                frame_buf.drain(..5 + plen);

                let frame = Frame::new(opcode, payload);

                if frame.opcode == OP_DISCOVERY {
                    let resp = Frame::new(OP_DISCOVERY, manifest_json.as_bytes().to_vec());
                    let bytes = resp.serialize();
                    usb_write_all(&mut serial, &mut usb_dev, &bytes);
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
                    let bytes = err.serialize();
                    usb_write_all(&mut serial, &mut usb_dev, &bytes);
                    continue;
                }

                // Same import whitelist as the shared main_loop: reject
                // modules that ask for anything outside the MILO Alphabet.
                let v = milo_receiver::engine::validation::validate_wasm_imports(&frame.payload);
                if !v.valid {
                    let mut names = alloc::string::String::new();
                    for (i, name) in v.rejected_imports.iter().enumerate() {
                        if i > 0 {
                            names.push_str(", ");
                        }
                        names.push_str(name);
                    }
                    let err = Frame::new(
                        OP_EXEC_RESULT,
                        alloc::format!(
                            r#"{{"ok":false,"error":"rejected imports: {}"}}"#,
                            milo_receiver::json_escape(&names)
                        )
                        .into_bytes(),
                    );
                    let bytes = err.serialize();
                    usb_write_all(&mut serial, &mut usb_dev, &bytes);
                    continue;
                }

                // Register USB poll callback so delays keep USB alive
                struct UsbCtx {
                    serial: *mut SerialPort<'static, rp2040_hal::usb::UsbBus>,
                    usb_dev: *mut UsbDevice<'static, rp2040_hal::usb::UsbBus>,
                }
                unsafe fn usb_poll_cb(ctx: *mut ()) {
                    unsafe {
                        let c = &mut *(ctx as *mut UsbCtx);
                        (*c.usb_dev).poll(&mut [&mut *c.serial]);
                    }
                }
                let mut usb_ctx = UsbCtx {
                    serial: &mut serial as *mut _,
                    usb_dev: &mut usb_dev as *mut _,
                };
                unsafe {
                    rp2040::set_usb_poll(usb_poll_cb, &mut usb_ctx as *mut UsbCtx as *mut ());
                }

                let mut runtime = MiloRuntime::new(hal, Some(500_000_000));
                let exec_result = match runtime.execute(&frame.payload, "run_logic") {
                    Ok(logs) => milo_receiver::engine::executor::ExecResult {
                        ok: true,
                        logs,
                        error: None,
                    },
                    Err(e) => milo_receiver::engine::executor::ExecResult {
                        ok: false,
                        logs: alloc::vec::Vec::new(),
                        error: Some(alloc::format!("{}", e)),
                    },
                };
                let result_json = milo_receiver::exec_result_to_json(&exec_result);

                rp2040::clear_usb_poll();
                hal = runtime.into_hardware();

                let resp = Frame::new(OP_EXEC_RESULT, result_json.into_bytes());
                let bytes = resp.serialize();
                usb_write_all(&mut serial, &mut usb_dev, &bytes);
            }
        }
    }
}

// ── Laptop (std) entry point ──────────────────────────────────────────
#[cfg(feature = "std")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    use milo_receiver::engine::link::{Frame, OP_BYTECODE_PUSH, OP_DISCOVERY, OP_EXEC_RESULT};
    use milo_receiver::engine::manifest::{
        build as build_manifest, AdcCapability, Capabilities, GpioCapability, ManifestHeader,
        PwmCapability, UartCapability,
    };
    use milo_receiver::targets::mock::LaptopMock;
    use milo_receiver::transport::{MiloTransport, StdioTransport};
    use milo_receiver::MiloRuntime;
    use std::fs;

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

    // Default matches the fuel_default advertised in the manifest above;
    // override with --fuel N.
    let mut fuel: Option<u64> = Some(10_000_000);
    let mut wasm_path: Option<String> = None;
    let mut stdin_mode = false;
    let mut listen_port: Option<u16> = None;
    let mut profile_name: Option<String> = None;
    let mut device_name: Option<String> = None;
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
            "--listen" => {
                i += 1;
                listen_port = Some(args[i].parse().expect("--listen requires a port"));
            }
            "--profile" => {
                i += 1;
                profile_name = Some(args[i].clone());
            }
            "--name" => {
                i += 1;
                device_name = Some(args[i].clone());
            }
            other => {
                wasm_path = Some(other.to_string());
            }
        }
        i += 1;
    }

    // ── Virtual fleet emulator: TCP server with a simulated hardware profile.
    // Uses the shared main_loop (import validation + all extended opcodes)
    // and the ThreadedExecutor, so the device keeps answering stop/set-param/
    // query/hot-swap while a driver is running — same contract a dual-core
    // board provides.
    if let Some(port) = listen_port {
        use milo_receiver::engine::executor_threaded::ThreadedExecutor;
        use milo_receiver::targets::sim::{
            sim_manifest, sim_start_hook, sim_stop_hook, SimHal, SimProfile,
        };
        use milo_receiver::transport::TcpServerTransport;

        let profile_str = profile_name.as_deref().unwrap_or("drone");
        let profile = SimProfile::parse(profile_str)
            .unwrap_or_else(|| panic!("unknown profile '{profile_str}' (drone|conveyor|oven|arm)"));
        let name = device_name.unwrap_or_else(|| format!("{profile_str}-{port}"));

        let hal = SimHal::new(profile, &name);
        let manifest = sim_manifest(profile, &name);
        let exec = ThreadedExecutor::new(
            hal,
            Some(500_000_000),
            Some(sim_stop_hook),
            Some(sim_start_hook),
        );
        let mut transport = TcpServerTransport::bind(port)?;
        eprintln!("[{name}] sim receiver ({profile_str}) listening on 127.0.0.1:{port}");
        milo_receiver::main_loop_with_executor(&mut transport, exec, &manifest);
    }

    if stdin_mode {
        let mut transport = StdioTransport::new();

        loop {
            let frame = match transport.read_frame() {
                Ok(f) => f,
                Err(milo_receiver::engine::link::LinkError::ConnectionClosed) => break,
                Err(e) => {
                    let err_json = format!(r#"{{"error":"{}"}}"#, e);
                    let resp = Frame::new(OP_EXEC_RESULT, err_json.into_bytes());
                    let _ = transport.write_frame(&resp);
                    continue;
                }
            };

            if frame.opcode == OP_DISCOVERY {
                let manifest_frame =
                    Frame::new(OP_DISCOVERY, hardware_manifest.as_bytes().to_vec());
                let _ = transport.write_frame(&manifest_frame);
                continue;
            }

            if frame.opcode != OP_BYTECODE_PUSH {
                let err_json = format!(
                    r#"{{"error":"unexpected opcode 0x{:02x}, expected 0x01 or 0x02"}}"#,
                    frame.opcode
                );
                let resp = Frame::new(OP_EXEC_RESULT, err_json.into_bytes());
                let _ = transport.write_frame(&resp);
                continue;
            }

            let v = milo_receiver::engine::validation::validate_wasm_imports(&frame.payload);
            if !v.valid {
                let err_json = format!(
                    r#"{{"ok":false,"error":"rejected imports: {}"}}"#,
                    milo_receiver::json_escape(&v.rejected_imports.join(", "))
                );
                let resp = Frame::new(OP_EXEC_RESULT, err_json.into_bytes());
                let _ = transport.write_frame(&resp);
                continue;
            }

            let hw = LaptopMock::new();
            let mut runtime = MiloRuntime::new(hw, fuel);

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
            let _ = transport.write_frame(&resp);
        }

        return Ok(());
    }

    if let Some(path) = wasm_path {
        eprintln!("MILO Receiver Active (Rust Engine)");
        let hw = LaptopMock::new();
        let mut runtime = MiloRuntime::new(hw, fuel);

        eprintln!("Loading bytecode from: {path}");
        let wasm_bytes = fs::read(&path)?;

        eprintln!("Executing MILO Driver...");
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

    eprintln!("Usage: milo-receiver [--fuel N] <wasm_path>");
    eprintln!("       milo-receiver [--fuel N] --stdin");
    std::process::exit(1);
}

#[cfg(all(not(feature = "std"), not(feature = "esp32c3"), not(feature = "rp2040")))]
fn main() {}
