//! Threaded executor (std only) — runs Wasm on a worker thread so the main
//! loop stays responsive to control opcodes (stop, set-param, query, hot-swap)
//! *while a driver is executing*. This is the std/simulation counterpart of
//! the RP2040 `DualCoreExecutor`: same `MiloExecutor` contract, different
//! parallelism substrate.

use alloc::collections::VecDeque;
use alloc::string::String;
use alloc::vec::Vec;
use std::thread::JoinHandle;

use super::executor::{ExecResult, ExecStatus, MiloExecutor};
use crate::{MiloHardware, MiloRuntime};

/// Executor that runs each submitted module on its own thread.
///
/// `stop_hook` / `start_hook` let the hardware backend implement cooperative
/// cancellation (e.g. the sim HAL turns `delay_ms` into a no-op once stopped,
/// so a stopped driver unwinds in milliseconds instead of sleeping out its
/// remaining loop iterations).
pub struct ThreadedExecutor<H: MiloHardware + Send + 'static> {
    hardware: Option<H>,
    fuel: Option<u64>,
    status: ExecStatus,
    handle: Option<JoinHandle<(H, ExecResult)>>,
    // Queue, not a slot: a hot-swap reaps the old module and immediately
    // starts the new one — both results must reach the host.
    results: VecDeque<ExecResult>,
    stop_hook: Option<fn()>,
    start_hook: Option<fn()>,
}

impl<H: MiloHardware + Send + 'static> ThreadedExecutor<H> {
    pub fn new(
        hw: H,
        fuel: Option<u64>,
        stop_hook: Option<fn()>,
        start_hook: Option<fn()>,
    ) -> Self {
        Self {
            hardware: Some(hw),
            fuel,
            status: ExecStatus::Idle,
            handle: None,
            results: VecDeque::new(),
            stop_hook,
            start_hook,
        }
    }

    /// Join a finished (or stopping) worker and reclaim hardware + result.
    fn reap(&mut self) {
        if let Some(handle) = self.handle.take() {
            match handle.join() {
                Ok((hw, result)) => {
                    self.hardware = Some(hw);
                    self.results.push_back(result);
                }
                Err(_) => {
                    self.results.push_back(ExecResult {
                        ok: false,
                        logs: Vec::new(),
                        error: Some(String::from("executor thread panicked")),
                    });
                }
            }
            if self.status != ExecStatus::Stopped {
                self.status = ExecStatus::Completed;
            }
        }
    }
}

impl<H: MiloHardware + Send + 'static> MiloExecutor for ThreadedExecutor<H> {
    type Hardware = H;

    fn submit(&mut self, bytecode: &[u8]) {
        // If a previous run is still going, stop and reap it first.
        if self.handle.is_some() {
            if let Some(hook) = self.stop_hook {
                hook();
            }
            self.reap();
        }

        let hw = match self.hardware.take() {
            Some(h) => h,
            None => {
                self.results.push_back(ExecResult {
                    ok: false,
                    logs: Vec::new(),
                    error: Some(String::from("no hardware available")),
                });
                self.status = ExecStatus::Completed;
                return;
            }
        };

        if let Some(hook) = self.start_hook {
            hook();
        }

        let fuel = self.fuel;
        let bytes: Vec<u8> = bytecode.to_vec();
        self.status = ExecStatus::Running;
        self.handle = Some(std::thread::spawn(move || {
            let mut runtime = MiloRuntime::new(hw, fuel);
            let result = match runtime.execute(&bytes, "run_logic") {
                Ok(logs) => ExecResult {
                    ok: true,
                    logs,
                    error: None,
                },
                Err(e) => ExecResult {
                    ok: false,
                    logs: Vec::new(),
                    error: Some(alloc::format!("{}", e)),
                },
            };
            (runtime.into_hardware(), result)
        }));
    }

    fn poll_result(&mut self) -> Option<ExecResult> {
        if let Some(handle) = &self.handle {
            if handle.is_finished() {
                self.reap();
            }
        }
        if self.handle.is_none() {
            if let Some(result) = self.results.pop_front() {
                self.status = ExecStatus::Idle;
                return Some(result);
            }
        }
        None
    }

    fn is_running(&self) -> bool {
        self.handle.as_ref().is_some_and(|h| !h.is_finished())
    }

    fn stop(&mut self) {
        if let Some(hook) = self.stop_hook {
            hook();
        }
        self.status = ExecStatus::Stopped;
        // The worker unwinds cooperatively; the result is collected on the
        // next poll_result() and reported to the host as a normal EXEC_RESULT.
    }

    fn status(&self) -> ExecStatus {
        if self.is_running() {
            ExecStatus::Running
        } else {
            self.status
        }
    }

    fn take_hardware(&mut self) -> Option<H> {
        if self.handle.is_some() {
            if let Some(hook) = self.stop_hook {
                hook();
            }
            self.reap();
        }
        self.hardware.take()
    }

    fn give_hardware(&mut self, hw: H) {
        self.hardware = Some(hw);
    }
}
