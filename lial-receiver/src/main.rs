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
    use esp_hal::gpio::{Level, Output, OutputConfig};
    use esp_hal::usb_serial_jtag::UsbSerialJtag;

    use lial_receiver::esp32c3::Esp32C3Hal;
    use lial_receiver::link::{Frame, OP_BYTECODE_PUSH, OP_DISCOVERY, OP_EXEC_RESULT};
    use lial_receiver::LialRuntime;

    const MANIFEST: &[u8] = br#"{"device":"esp32c3","pins":[5],"buses":{"i2c":[]},"memory_kb":320,"alphabet":["lial_gpio_set","lial_gpio_get","lial_delay_ms","lial_get_uptime_us","lial_i2c_transfer","lial_log"]}"#;

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

        let led = Output::new(peripherals.GPIO5, Level::Low, OutputConfig::default());
        let usb_serial = UsbSerialJtag::new(peripherals.USB_DEVICE);
        let (rx, tx) = usb_serial.split();

        let mut hal = Esp32C3Hal::new(led, tx, rx);

        // Send discovery manifest
        let discovery = Frame::new(OP_DISCOVERY, MANIFEST.to_vec());
        write_frame(&mut hal, &discovery);

        // Main loop: receive wasm, execute, respond
        loop {
            let frame = read_frame(&mut hal);

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

            let mut runtime = LialRuntime::new(hal, Some(1_000_000));
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
    use lial_receiver::mock::LaptopMock;
    use lial_receiver::LialRuntime;
    use std::fs;
    use std::io;

    const HARDWARE_MANIFEST: &str = r#"{"device":"laptop-mock","pins":[0,1,2,3,4,5],"buses":{"i2c":[]},"memory_kb":4096,"alphabet":["lial_gpio_set","lial_gpio_get","lial_delay_ms","lial_get_uptime_us","lial_i2c_transfer","lial_log"]}"#;

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

        let manifest_frame =
            Frame::new(OP_DISCOVERY, HARDWARE_MANIFEST.as_bytes().to_vec());
        link::write_frame(&mut stdout, &manifest_frame)?;

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

            if frame.opcode != OP_BYTECODE_PUSH {
                let err_json = format!(
                    r#"{{"error":"unexpected opcode 0x{:02x}, expected 0x02"}}"#,
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
