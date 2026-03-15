use lial_receiver::link::{self, Frame, OP_BYTECODE_PUSH, OP_DISCOVERY, OP_EXEC_RESULT};
use lial_receiver::mock::LaptopMock;
use lial_receiver::LialRuntime;
use std::fs;
use std::io;

const HARDWARE_MANIFEST: &str = r#"{"device":"laptop-mock","pins":[0,1,2,3,4,5],"buses":{"i2c":[]},"memory_kb":4096,"alphabet":["lial_gpio_set","lial_gpio_get","lial_delay_ms","lial_get_uptime_us","lial_i2c_transfer","lial_log"]}"#;

fn run_file(path: &str, fuel: Option<u64>) -> Result<(), Box<dyn std::error::Error>> {
    eprintln!("LIAL Receiver Active (Rust Engine)");
    let hw = LaptopMock::new();
    let mut runtime = LialRuntime::new(hw, fuel);

    eprintln!("Loading bytecode from: {path}");
    let wasm_bytes = fs::read(path)?;

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
    Ok(())
}

fn run_stdin(fuel: Option<u64>) -> Result<(), Box<dyn std::error::Error>> {
    let mut stdin = io::stdin().lock();
    let mut stdout = io::stdout().lock();

    let manifest_frame = Frame::new(OP_DISCOVERY, HARDWARE_MANIFEST.as_bytes().to_vec());
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
                format!(r#"{{"ok":true,"logs":{}}}"#, serde_json::to_string(&logs).unwrap_or_else(|_| "[]".into()))
            }
            Err(e) => {
                format!(r#"{{"ok":false,"error":"{}"}}"#, e)
            }
        };

        let resp = Frame::new(OP_EXEC_RESULT, result_json.into_bytes());
        link::write_frame(&mut stdout, &resp)?;
    }

    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
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
        return run_stdin(fuel);
    }

    if let Some(path) = wasm_path {
        return run_file(&path, fuel);
    }

    eprintln!("Usage: lial-receiver [--fuel N] <wasm_path>");
    eprintln!("       lial-receiver [--fuel N] --stdin");
    std::process::exit(1);
}
