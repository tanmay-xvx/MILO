#![cfg_attr(not(feature = "std"), no_std)]

extern crate alloc;

use alloc::format;
use alloc::string::String;
use alloc::vec::Vec;
use core::fmt;
use wasmi::{Caller, Config, Engine, Func, Linker, Memory, Module, Store};

#[cfg(feature = "std")]
pub mod mock;

#[cfg(feature = "esp32c3")]
pub mod esp32c3;

pub mod link;

pub trait LialHardware {
    fn gpio_set(&mut self, pin: u32, state: u32);
    fn gpio_get(&mut self, pin: u32) -> u32;
    fn delay_ms(&mut self, ms: u32);
    fn get_uptime_us(&self) -> u64;
    fn i2c_transfer(&mut self, addr: u8, tx: &[u8], rx: &mut [u8]) -> i32;
    fn log(&mut self, message: &str);
}

#[derive(Debug)]
pub enum LialError {
    ModuleInvalid(String),
    MissingExport(String),
    FuelExhausted,
    Trapped(String),
}

impl fmt::Display for LialError {
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
impl std::error::Error for LialError {}

pub struct HostState<H: LialHardware> {
    pub hw: H,
    pub memory: Option<Memory>,
    pub logs: Vec<String>,
}

pub struct LialRuntime<H: LialHardware> {
    engine: Engine,
    store: Store<HostState<H>>,
    linker: Linker<HostState<H>>,
}

impl<H: LialHardware + 'static> LialRuntime<H> {
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

        let lial_log = Func::wrap(
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

        linker.define("env", "lial_gpio_set", gpio_set).unwrap();
        linker.define("env", "lial_gpio_get", gpio_get).unwrap();
        linker.define("env", "lial_delay_ms", delay_ms).unwrap();
        linker
            .define("env", "lial_get_uptime_us", get_uptime_us)
            .unwrap();
        linker
            .define("env", "lial_i2c_transfer", i2c_transfer)
            .unwrap();
        linker.define("env", "lial_log", lial_log).unwrap();
    }

    pub fn execute(&mut self, wasm_bytes: &[u8], export: &str) -> Result<Vec<String>, LialError> {
        self.store.data_mut().logs.clear();
        self.store.data_mut().memory = None;

        let module = Module::new(&self.engine, wasm_bytes)
            .map_err(|e| LialError::ModuleInvalid(format!("{e}")))?;

        let instance = self
            .linker
            .instantiate_and_start(&mut self.store, &module)
            .map_err(|e| {
                let msg = format!("{e}");
                if msg.contains("fuel") {
                    LialError::FuelExhausted
                } else {
                    LialError::Trapped(msg)
                }
            })?;

        if let Some(mem_export) = instance.get_export(&self.store, "memory") {
            if let Some(mem) = mem_export.into_memory() {
                self.store.data_mut().memory = Some(mem);
            }
        }

        let func = instance
            .get_typed_func::<(), ()>(&self.store, export)
            .map_err(|_| LialError::MissingExport(String::from(export)))?;

        func.call(&mut self.store, ()).map_err(|e| {
            let msg = format!("{e}");
            if msg.contains("fuel") {
                LialError::FuelExhausted
            } else {
                LialError::Trapped(msg)
            }
        })?;

        Ok(self.store.data().logs.clone())
    }

    /// Consume the runtime and return the hardware backend.
    pub fn into_hardware(self) -> H {
        self.store.into_data().hw
    }
}
