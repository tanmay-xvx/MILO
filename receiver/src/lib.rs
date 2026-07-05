#![cfg_attr(not(feature = "std"), no_std)]

extern crate alloc;

use alloc::format;
use alloc::string::String;
use alloc::vec::Vec;
use core::fmt;
use wasmi::{Caller, Config, Engine, Func, Linker, Memory, Module, Store};

pub mod engine;
pub mod transport;
pub mod hal;
pub mod targets;

pub trait MiloHardware {
    fn gpio_set(&mut self, pin: u32, state: u32);
    fn gpio_get(&mut self, pin: u32) -> u32;
    fn delay_ms(&mut self, ms: u32);
    fn get_uptime_us(&self) -> u64;
    fn i2c_transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32;
    fn log(&mut self, message: &str);

    /// Set the PWM duty cycle on `channel`. `duty_0_10000` is in 1/10000ths
    /// (0 = 0%, 10000 = 100%). Default impl is a no-op so existing backends
    /// don't break.
    fn pwm_set(&mut self, _channel: u32, _duty_0_10000: u32) {}

    /// Read the current ADC value on `channel`. Default impl returns 0.
    fn adc_read(&mut self, _channel: u32) -> u32 {
        0
    }

    /// Blocking SPI transfer on `bus`. Returns 0 on success, non-zero error.
    fn spi_transfer(&mut self, _bus: u32, _tx: &[u8], _rx: &mut [u8]) -> i32 {
        -1
    }

    /// Write to a secondary UART (not the MILO-Link transport).
    fn uart_write(&mut self, _bus: u32, _data: &[u8]) -> i32 {
        -1
    }

    /// Read up to `buf.len()` bytes from a secondary UART; returns the
    /// number read (>= 0) or a negative value on error.
    fn uart_read(&mut self, _bus: u32, _buf: &mut [u8], _timeout_ms: u32) -> i32 {
        -1
    }
}

#[derive(Debug)]
pub enum MiloError {
    ModuleInvalid(String),
    MissingExport(String),
    FuelExhausted,
    Trapped(String),
}

impl fmt::Display for MiloError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ModuleInvalid(e) => write!(f, "invalid wasm module: {e}"),
            Self::MissingExport(name) => write!(f, "missing export: {name}"),
            Self::FuelExhausted => write!(f, "fuel exhausted (gas limit reached)"),
            Self::Trapped(e) => write!(f, "execution trapped: {e}"),
        }
    }
}

#[cfg(feature = "std")]
impl std::error::Error for MiloError {}

pub struct HostState<H: MiloHardware> {
    pub hw: H,
    pub memory: Option<Memory>,
    pub logs: Vec<String>,
}

pub struct MiloRuntime<H: MiloHardware> {
    engine: Engine,
    store: Store<HostState<H>>,
    linker: Linker<HostState<H>>,
}

/// Shared main loop: reads frames from transport, dispatches opcodes, executes
/// Wasm, and writes results back. Used by all board entry points.
pub fn main_loop<T: transport::MiloTransport, H: MiloHardware + 'static>(
    transport: &mut T,
    hal: H,
    manifest_json: &str,
) -> ! {
    let exec = engine::executor::SingleCoreExecutor::new(hal, Some(500_000_000));
    main_loop_with_executor(transport, exec, manifest_json)
}

/// Main loop over any executor strategy. With a blocking executor
/// (`SingleCoreExecutor`) results are sent from the push handler as before;
/// with a non-blocking one (`ThreadedExecutor`, `DualCoreExecutor`) the loop
/// keeps servicing control opcodes while the module runs and sends the
/// EXEC_RESULT once `poll_result` yields it.
pub fn main_loop_with_executor<T: transport::MiloTransport, E: engine::executor::MiloExecutor>(
    transport: &mut T,
    mut exec: E,
    manifest_json: &str,
) -> ! {
    use engine::executor::ExecStatus;

    loop {
        let frame = match transport.read_frame() {
            Ok(f) => f,
            Err(_) => {
                // Idle gap: flush any result from a module that finished
                // while we were servicing other traffic.
                if let Some(result) = exec.poll_result() {
                    let json = exec_result_to_json(&result);
                    let resp =
                        engine::link::Frame::new(engine::link::OP_EXEC_RESULT, json.into_bytes());
                    let _ = transport.write_frame(&resp);
                }
                continue;
            }
        };

        match frame.opcode {
            engine::link::OP_DISCOVERY => {
                let discovery =
                    engine::link::Frame::new(engine::link::OP_DISCOVERY, manifest_json.as_bytes().to_vec());
                let _ = transport.write_frame(&discovery);
            }

            engine::link::OP_BYTECODE_PUSH => {
                let v = engine::validation::validate_wasm_imports(&frame.payload);
                if !v.valid {
                    let err_msg = rejected_imports_json(&v.rejected_imports);
                    let resp = engine::link::Frame::new(engine::link::OP_EXEC_RESULT, err_msg.into_bytes());
                    let _ = transport.write_frame(&resp);
                } else {
                    exec.submit(&frame.payload);
                    if let Some(result) = exec.poll_result() {
                        let json = exec_result_to_json(&result);
                        let resp = engine::link::Frame::new(engine::link::OP_EXEC_RESULT, json.into_bytes());
                        let _ = transport.write_frame(&resp);
                    }
                }
            }

            engine::link::OP_STOP => {
                exec.stop();
                let resp = engine::link::Frame::new(
                    engine::link::OP_STATUS_RESPONSE,
                    format!(r#"{{"stopped":true}}"#).into_bytes(),
                );
                let _ = transport.write_frame(&resp);
            }

            engine::link::OP_QUERY_STATUS => {
                let status = exec.status();
                let json = format!(
                    r#"{{"status":"{}","running":{}}}"#,
                    match status {
                        ExecStatus::Idle => "idle",
                        ExecStatus::Running => "running",
                        ExecStatus::Completed => "completed",
                        ExecStatus::Stopped => "stopped",
                    },
                    exec.is_running()
                );
                let resp = engine::link::Frame::new(engine::link::OP_STATUS_RESPONSE, json.into_bytes());
                let _ = transport.write_frame(&resp);
            }

            engine::link::OP_SET_PARAM => {
                if frame.payload.len() >= 8 {
                    let slot = u32::from_be_bytes([
                        frame.payload[0],
                        frame.payload[1],
                        frame.payload[2],
                        frame.payload[3],
                    ]);
                    let value = u32::from_be_bytes([
                        frame.payload[4],
                        frame.payload[5],
                        frame.payload[6],
                        frame.payload[7],
                    ]);
                    engine::executor::set_param(slot, value);
                }
            }

            engine::link::OP_HOT_SWAP => {
                let v = engine::validation::validate_wasm_imports(&frame.payload);
                if !v.valid {
                    let err_msg = rejected_imports_json(&v.rejected_imports);
                    let resp = engine::link::Frame::new(engine::link::OP_EXEC_RESULT, err_msg.into_bytes());
                    let _ = transport.write_frame(&resp);
                } else {
                    exec.stop();
                    exec.submit(&frame.payload);
                    if let Some(result) = exec.poll_result() {
                        let json = exec_result_to_json(&result);
                        let resp = engine::link::Frame::new(engine::link::OP_EXEC_RESULT, json.into_bytes());
                        let _ = transport.write_frame(&resp);
                    }
                }
            }

            _ => {
                let err = engine::link::Frame::new(
                    engine::link::OP_EXEC_RESULT,
                    format!(r#"{{"error":"unknown opcode 0x{:02x}"}}"#, frame.opcode)
                        .into_bytes(),
                );
                let _ = transport.write_frame(&err);
            }
        }
    }
}

/// Escape a string for embedding inside a JSON string literal.
pub fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

fn rejected_imports_json(rejected: &[String]) -> String {
    let mut names = String::new();
    for (i, name) in rejected.iter().enumerate() {
        if i > 0 {
            names.push_str(", ");
        }
        names.push_str(name);
    }
    format!(
        r#"{{"ok":false,"error":"rejected imports: {}"}}"#,
        json_escape(&names)
    )
}

pub fn exec_result_to_json(result: &engine::executor::ExecResult) -> String {
    if result.ok {
        let mut s = String::from(r#"{"ok":true,"logs":["#);
        for (i, log) in result.logs.iter().enumerate() {
            if i > 0 {
                s.push(',');
            }
            s.push('"');
            s.push_str(&json_escape(log));
            s.push('"');
        }
        s.push_str("]}");
        s
    } else {
        format!(
            r#"{{"ok":false,"error":"{}"}}"#,
            json_escape(result.error.as_deref().unwrap_or("unknown"))
        )
    }
}

impl<H: MiloHardware + 'static> MiloRuntime<H> {
    pub fn new(hw: H, fuel: Option<u64>) -> Self {
        let mut config = Config::default();
        if fuel.is_some() {
            config.consume_fuel(true);
        }
        let engine = Engine::new(&config);
        let host = HostState {
            hw,
            memory: None,
            logs: Vec::new(),
        };
        let mut store = Store::new(&engine, host);
        if let Some(amount) = fuel {
            store.set_fuel(amount).expect("fuel metering is enabled");
        }

        let mut linker = <Linker<HostState<H>>>::new(&engine);
        Self::register_syscalls(&mut linker, &mut store);

        Self {
            engine,
            store,
            linker,
        }
    }

    fn register_syscalls(linker: &mut Linker<HostState<H>>, store: &mut Store<HostState<H>>) {
        let gpio_set = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>, pin: u32, state: u32| {
                caller.data_mut().hw.gpio_set(pin, state);
            },
        );

        let gpio_get = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>, pin: u32| -> u32 {
                caller.data_mut().hw.gpio_get(pin)
            },
        );

        let delay_ms = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>, ms: u32| {
                caller.data_mut().hw.delay_ms(ms);
            },
        );

        let get_uptime_us = Func::wrap(
            &mut *store,
            |caller: Caller<'_, HostState<H>>| -> u64 { caller.data().hw.get_uptime_us() },
        );

        let i2c_transfer = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>,
             addr: u32,
             tx_ptr: u32,
             tx_len: u32,
             rx_ptr: u32,
             rx_len: u32|
             -> i32 {
                let memory = match caller.data().memory {
                    Some(m) => m,
                    None => return -1,
                };
                let data = memory.data(&caller);
                let tx_owned: Vec<u8> =
                    data[tx_ptr as usize..(tx_ptr + tx_len) as usize].to_vec();

                let mut rx_buf = alloc::vec![0u8; rx_len as usize];
                let result =
                    caller
                        .data_mut()
                        .hw
                        .i2c_transfer(addr as u8, &tx_owned, &mut rx_buf);

                if result == 0 && rx_len > 0 {
                    let mem_data = memory.data_mut(&mut caller);
                    mem_data[rx_ptr as usize..(rx_ptr + rx_len) as usize]
                        .copy_from_slice(&rx_buf);
                }
                result
            },
        );

        let log_msg = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>, ptr: u32, len: u32| {
                let memory = match caller.data().memory {
                    Some(m) => m,
                    None => return,
                };
                let data = memory.data(&caller);
                let start = ptr as usize;
                let end = start + len as usize;
                if end > data.len() {
                    return;
                }
                if let Ok(msg) = core::str::from_utf8(&data[start..end]) {
                    let msg_owned = String::from(msg);
                    caller.data_mut().hw.log(&msg_owned);
                    caller.data_mut().logs.push(msg_owned);
                }
            },
        );

        let pwm_set = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>, channel: u32, duty: u32| {
                caller.data_mut().hw.pwm_set(channel, duty);
            },
        );

        let adc_read = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>, channel: u32| -> u32 {
                caller.data_mut().hw.adc_read(channel)
            },
        );

        let spi_transfer = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>,
             bus: u32,
             tx_ptr: u32,
             tx_len: u32,
             rx_ptr: u32,
             rx_len: u32|
             -> i32 {
                let memory = match caller.data().memory {
                    Some(m) => m,
                    None => return -1,
                };
                let data = memory.data(&caller);
                let tx_owned: Vec<u8> =
                    data[tx_ptr as usize..(tx_ptr + tx_len) as usize].to_vec();

                let mut rx_buf = alloc::vec![0u8; rx_len as usize];
                let result = caller
                    .data_mut()
                    .hw
                    .spi_transfer(bus, &tx_owned, &mut rx_buf);

                if result == 0 && rx_len > 0 {
                    let mem_data = memory.data_mut(&mut caller);
                    mem_data[rx_ptr as usize..(rx_ptr + rx_len) as usize]
                        .copy_from_slice(&rx_buf);
                }
                result
            },
        );

        let uart_write = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>,
             bus: u32,
             ptr: u32,
             len: u32|
             -> i32 {
                let memory = match caller.data().memory {
                    Some(m) => m,
                    None => return -1,
                };
                let data = memory.data(&caller);
                let start = ptr as usize;
                let end = start + len as usize;
                if end > data.len() {
                    return -1;
                }
                let owned: Vec<u8> = data[start..end].to_vec();
                caller.data_mut().hw.uart_write(bus, &owned)
            },
        );

        let uart_read = Func::wrap(
            &mut *store,
            |mut caller: Caller<'_, HostState<H>>,
             bus: u32,
             ptr: u32,
             len_max: u32,
             timeout_ms: u32|
             -> i32 {
                let memory = match caller.data().memory {
                    Some(m) => m,
                    None => return -1,
                };
                let mut rx_buf = alloc::vec![0u8; len_max as usize];
                let n = caller
                    .data_mut()
                    .hw
                    .uart_read(bus, &mut rx_buf, timeout_ms);
                if n > 0 {
                    let copy_len = (n as usize).min(len_max as usize);
                    let mem_data = memory.data_mut(&mut caller);
                    let start = ptr as usize;
                    let end = start + copy_len;
                    if end <= mem_data.len() {
                        mem_data[start..end].copy_from_slice(&rx_buf[..copy_len]);
                    }
                }
                n
            },
        );

        let get_param = Func::wrap(
            &mut *store,
            |_caller: Caller<'_, HostState<H>>, slot: u32| -> u32 {
                crate::engine::executor::get_param(slot)
            },
        );

        linker.define("env", "gpio_set", gpio_set).unwrap();
        linker.define("env", "gpio_get", gpio_get).unwrap();
        linker.define("env", "delay_ms", delay_ms).unwrap();
        linker
            .define("env", "get_uptime_us", get_uptime_us)
            .unwrap();
        linker
            .define("env", "i2c_transfer", i2c_transfer)
            .unwrap();
        linker.define("env", "log_msg", log_msg).unwrap();
        linker.define("env", "pwm_set", pwm_set).unwrap();
        linker.define("env", "adc_read", adc_read).unwrap();
        linker
            .define("env", "spi_transfer", spi_transfer)
            .unwrap();
        linker
            .define("env", "uart_write", uart_write)
            .unwrap();
        linker
            .define("env", "uart_read", uart_read)
            .unwrap();
        linker.define("env", "get_param", get_param).unwrap();
    }

    pub fn execute(&mut self, wasm_bytes: &[u8], export: &str) -> Result<Vec<String>, MiloError> {
        self.store.data_mut().logs.clear();
        self.store.data_mut().memory = None;

        let module = Module::new(&self.engine, wasm_bytes)
            .map_err(|e| MiloError::ModuleInvalid(format!("{e}")))?;

        let instance = self
            .linker
            .instantiate_and_start(&mut self.store, &module)
            .map_err(|e| {
                let msg = format!("{e}");
                if msg.contains("fuel") {
                    MiloError::FuelExhausted
                } else {
                    MiloError::Trapped(msg)
                }
            })?;

        if let Some(mem_export) = instance.get_export(&self.store, "memory") {
            if let Some(mem) = mem_export.into_memory() {
                self.store.data_mut().memory = Some(mem);
            }
        }

        let func = instance
            .get_typed_func::<(), ()>(&self.store, export)
            .map_err(|_| MiloError::MissingExport(String::from(export)))?;

        func.call(&mut self.store, ()).map_err(|e| {
            let msg = format!("{e}");
            if msg.contains("fuel") {
                MiloError::FuelExhausted
            } else {
                MiloError::Trapped(msg)
            }
        })?;

        Ok(self.store.data().logs.clone())
    }

    /// Consume the runtime and return the hardware backend.
    pub fn into_hardware(self) -> H {
        self.store.into_data().hw
    }
}
