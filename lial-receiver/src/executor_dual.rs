//! Dual-core executor for RP2040.
//!
//! Architecture:
//! - Core 0: runs the main loop (transport, opcode dispatch)
//! - Core 1: runs Wasm execution in a blocking loop
//!
//! Communication between cores uses:
//! - FIFO mailbox: lightweight signals (start, stop, done)
//! - Shared static buffer + spinlock: bytecode transfer and result return
//!
//! This module is only compiled for the `rp2040` feature.

use alloc::string::String;
use alloc::vec::Vec;
use core::sync::atomic::{AtomicBool, AtomicU8, Ordering};

use crate::executor::{ExecResult, ExecStatus, LialExecutor};
use crate::LialHardware;

/// FIFO command signals from Core 0 -> Core 1.
const CMD_EXECUTE: u32 = 0x01;
const CMD_STOP: u32 = 0x02;

/// FIFO response signals from Core 1 -> Core 0.
const RSP_DONE: u32 = 0x10;
const RSP_ERROR: u32 = 0x11;

/// Maximum bytecode size for the shared buffer (128 KB).
const MAX_BYTECODE_SIZE: usize = 128 * 1024;

/// Shared state between cores, protected by atomic flags.
/// Core 0 writes bytecode, Core 1 reads and executes.
static EXEC_STATE: AtomicU8 = AtomicU8::new(0); // 0=idle, 1=pending, 2=running, 3=done

/// Flag indicating Core 1 should stop.
static STOP_FLAG: AtomicBool = AtomicBool::new(false);

/// Flag indicating result is ready.
static RESULT_READY: AtomicBool = AtomicBool::new(false);

/// Dual-core executor for RP2040.
///
/// On `submit()`, it writes bytecode to a shared buffer and signals Core 1.
/// Core 1 picks up the bytecode, executes it, and signals completion.
/// `poll_result()` checks if Core 1 has finished and retrieves the result.
pub struct DualCoreExecutor<H: LialHardware + 'static> {
    hardware: Option<H>,
    fuel: Option<u64>,
    status: ExecStatus,
    last_result: Option<ExecResult>,
    /// In the current implementation, we fall back to single-core execution
    /// because actually launching Core 1 requires multicore::Multicore which
    /// needs PAC access that's consumed at init time. The infrastructure is
    /// set up for future dual-core when the entry point can pass the Core 1
    /// spawn handle.
    core1_available: bool,
}

impl<H: LialHardware + 'static> DualCoreExecutor<H> {
    /// Create a new dual-core executor.
    ///
    /// `core1_available`: set to true if Core 1 has been launched with the
    /// Wasm execution loop. If false, falls back to blocking single-core.
    pub fn new(hw: H, fuel: Option<u64>, core1_available: bool) -> Self {
        STOP_FLAG.store(false, Ordering::SeqCst);
        RESULT_READY.store(false, Ordering::SeqCst);
        EXEC_STATE.store(0, Ordering::SeqCst);

        Self {
            hardware: Some(hw),
            fuel,
            status: ExecStatus::Idle,
            last_result: None,
            core1_available,
        }
    }
}

impl<H: LialHardware + 'static> LialExecutor for DualCoreExecutor<H> {
    type Hardware = H;

    fn submit(&mut self, bytecode: &[u8]) {
        if !self.core1_available {
            // Fallback: execute inline on Core 0 (single-core mode)
            let hw = match self.hardware.take() {
                Some(h) => h,
                None => {
                    self.last_result = Some(ExecResult {
                        ok: false,
                        logs: Vec::new(),
                        error: Some(String::from("no hardware available")),
                    });
                    self.status = ExecStatus::Completed;
                    return;
                }
            };

            self.status = ExecStatus::Running;
            STOP_FLAG.store(false, Ordering::SeqCst);

            let mut runtime = crate::LialRuntime::new(hw, self.fuel);
            let result = match runtime.execute(bytecode, "run_logic") {
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

            self.hardware = Some(runtime.into_hardware());
            self.last_result = Some(result);
            self.status = ExecStatus::Completed;
            return;
        }

        // Dual-core path: signal Core 1 to execute
        self.status = ExecStatus::Running;
        STOP_FLAG.store(false, Ordering::SeqCst);
        RESULT_READY.store(false, Ordering::SeqCst);
        EXEC_STATE.store(1, Ordering::SeqCst); // pending

        // In the full implementation, bytecode would be written to a shared
        // buffer and Core 1 signaled via FIFO. For now this is infrastructure.
    }

    fn poll_result(&mut self) -> Option<ExecResult> {
        if self.status == ExecStatus::Completed {
            self.status = ExecStatus::Idle;
            return self.last_result.take();
        }

        if self.core1_available && RESULT_READY.load(Ordering::SeqCst) {
            RESULT_READY.store(false, Ordering::SeqCst);
            self.status = ExecStatus::Completed;
            self.status = ExecStatus::Idle;
            return self.last_result.take();
        }

        None
    }

    fn is_running(&self) -> bool {
        self.status == ExecStatus::Running
    }

    fn stop(&mut self) {
        STOP_FLAG.store(true, Ordering::SeqCst);
        self.status = ExecStatus::Stopped;
    }

    fn status(&self) -> ExecStatus {
        self.status
    }

    fn take_hardware(&mut self) -> Option<H> {
        self.hardware.take()
    }

    fn give_hardware(&mut self, hw: H) {
        self.hardware = Some(hw);
    }
}

/// Check if a stop has been requested (called from Wasm execution context).
pub fn is_stop_requested() -> bool {
    STOP_FLAG.load(Ordering::Relaxed)
}
