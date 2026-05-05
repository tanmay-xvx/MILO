//! Execution engine abstraction for LIAL.
//!
//! The `LialExecutor` trait decouples Wasm execution from the main loop so
//! different strategies (single-core blocking, dual-core async) can be used
//! depending on the hardware.

use alloc::string::String;
use alloc::vec::Vec;
use core::sync::atomic::{AtomicU32, Ordering};

use crate::{LialHardware, LialRuntime};

/// Shared parameter slots accessible from both the host (via `OP_SET_PARAM`)
/// and the Wasm module (via `lial_get_param` syscall).
pub static PARAM_SLOTS: [AtomicU32; 8] = [
    AtomicU32::new(0),
    AtomicU32::new(0),
    AtomicU32::new(0),
    AtomicU32::new(0),
    AtomicU32::new(0),
    AtomicU32::new(0),
    AtomicU32::new(0),
    AtomicU32::new(0),
];

/// Read a shared parameter slot value.
pub fn get_param(slot: u32) -> u32 {
    if (slot as usize) < PARAM_SLOTS.len() {
        PARAM_SLOTS[slot as usize].load(Ordering::Relaxed)
    } else {
        0
    }
}

/// Set a shared parameter slot value.
pub fn set_param(slot: u32, value: u32) {
    if (slot as usize) < PARAM_SLOTS.len() {
        PARAM_SLOTS[slot as usize].store(value, Ordering::Relaxed);
    }
}

/// Execution result from a completed Wasm module.
#[derive(Debug)]
pub struct ExecResult {
    pub ok: bool,
    pub logs: Vec<String>,
    pub error: Option<String>,
}

/// Current execution status.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecStatus {
    Idle,
    Running,
    Completed,
    Stopped,
}

/// Abstraction over how Wasm bytecode is executed.
///
/// - `SingleCoreExecutor`: runs inline (blocking) on the current core.
/// - `DualCoreExecutor`: submits to Core 1 and polls for completion.
pub trait LialExecutor {
    /// The hardware type this executor uses.
    type Hardware: LialHardware;

    /// Submit bytecode for execution. Non-blocking on dual-core.
    fn submit(&mut self, bytecode: &[u8]);

    /// Poll for a completed result. Returns `None` if still running.
    fn poll_result(&mut self) -> Option<ExecResult>;

    /// Check if execution is currently in progress.
    fn is_running(&self) -> bool;

    /// Forcefully stop the current execution.
    fn stop(&mut self);

    /// Get current status.
    fn status(&self) -> ExecStatus;

    /// Reclaim the hardware backend (after execution completes or is stopped).
    fn take_hardware(&mut self) -> Option<Self::Hardware>;

    /// Give hardware back to the executor for the next run.
    fn give_hardware(&mut self, hw: Self::Hardware);
}

/// Single-core executor — runs Wasm inline (blocking).
/// Used on ESP32-C3 (single-core) and as fallback.
pub struct SingleCoreExecutor<H: LialHardware + 'static> {
    hardware: Option<H>,
    fuel: Option<u64>,
    status: ExecStatus,
    last_result: Option<ExecResult>,
}

impl<H: LialHardware + 'static> SingleCoreExecutor<H> {
    pub fn new(hw: H, fuel: Option<u64>) -> Self {
        Self {
            hardware: Some(hw),
            fuel,
            status: ExecStatus::Idle,
            last_result: None,
        }
    }
}

impl<H: LialHardware + 'static> LialExecutor for SingleCoreExecutor<H> {
    type Hardware = H;

    fn submit(&mut self, bytecode: &[u8]) {
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

        let mut runtime = LialRuntime::new(hw, self.fuel);
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
    }

    fn poll_result(&mut self) -> Option<ExecResult> {
        if self.status == ExecStatus::Completed {
            self.status = ExecStatus::Idle;
            self.last_result.take()
        } else {
            None
        }
    }

    fn is_running(&self) -> bool {
        self.status == ExecStatus::Running
    }

    fn stop(&mut self) {
        // Single-core: execution is synchronous, so stop is a no-op.
        // The fuel mechanism handles runaway code.
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
