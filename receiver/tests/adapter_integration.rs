//! Integration tests that drive the full wasm pipeline (MiloRuntime ->
//! EmbeddedHalAdapter -> mock peripherals). These mirror what will run on
//! real hardware; the only difference is the peripherals are fake.

use milo_receiver::hal::adapter::{
    DynAdc, DynDelay, DynI2c, DynPin, DynPwm, DynUart, EmbeddedHalAdapter,
};
use milo_receiver::MiloRuntime;

use std::cell::RefCell;
use std::rc::Rc;

/// Record-everything pin used to assert what wasm wrote.
#[derive(Clone)]
struct SpyPin {
    history: Rc<RefCell<Vec<bool>>>,
}

impl SpyPin {
    fn new() -> Self {
        Self {
            history: Rc::new(RefCell::new(Vec::new())),
        }
    }
}

impl DynPin for SpyPin {
    fn set(&mut self, level: bool) {
        self.history.borrow_mut().push(level);
    }
    fn get(&mut self) -> bool {
        *self.history.borrow().last().unwrap_or(&false)
    }
}

struct NullDelay;
impl DynDelay for NullDelay {
    fn delay_ms(&mut self, _ms: u32) {}
}

struct EchoI2c;
impl DynI2c for EchoI2c {
    fn transfer(&mut self, _addr: u8, _tx: &[u8], rx: &mut [u8]) -> i32 {
        for b in rx.iter_mut() {
            *b = 0xAB;
        }
        0
    }
}

fn fixture(name: &str) -> Vec<u8> {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let path = match name {
        "mock_driver" => format!(
            "{manifest_dir}/../examples/mock_driver/target/wasm32-unknown-unknown/release/mock_driver.wasm"
        ),
        "pwm_fade" => format!(
            "{manifest_dir}/../examples/test_drivers/pwm_fade/target/wasm32-unknown-unknown/release/pwm_fade.wasm"
        ),
        "adc_read" => format!(
            "{manifest_dir}/../examples/test_drivers/adc_read/target/wasm32-unknown-unknown/release/adc_read.wasm"
        ),
        "uart_loopback" => format!(
            "{manifest_dir}/../examples/test_drivers/uart_loopback/target/wasm32-unknown-unknown/release/uart_loopback.wasm"
        ),
        _ => panic!("unknown fixture: {name}"),
    };
    std::fs::read(&path).unwrap_or_else(|e| panic!("cannot read {path}: {e}"))
}

#[test]
fn adapter_drives_real_wasm_mock_driver() {
    // mock_driver calls gpio_set(5, ...) and delay_ms repeatedly.
    let pin = SpyPin::new();
    let history = pin.history.clone();

    let adapter = EmbeddedHalAdapter::builder()
        .pin(5, Box::new(pin))
        .delay(Box::new(NullDelay))
        .uptime_fn(|| 0)
        .build();

    let wasm = fixture("mock_driver");
    let mut runtime = MiloRuntime::new(adapter, None);
    let _ = runtime
        .execute(&wasm, "run_logic")
        .expect("wasm should execute");

    let calls = history.borrow();
    assert!(!calls.is_empty(), "mock_driver should have toggled pin 5");
    // mock_driver blinks 5 times with alternating ON/OFF -- so we should see
    // at least one ON and one OFF.
    assert!(calls.iter().any(|&x| x), "expected at least one HIGH");
    assert!(calls.iter().any(|&x| !x), "expected at least one LOW");
}

#[test]
fn adapter_unregistered_pin_is_noop() {
    let adapter = EmbeddedHalAdapter::builder()
        .delay(Box::new(NullDelay))
        .build();
    let wasm = fixture("mock_driver");
    let mut runtime = MiloRuntime::new(adapter, None);
    // No pins registered -- wasm should still execute without panicking.
    let _ = runtime
        .execute(&wasm, "run_logic")
        .expect("wasm with unmapped pins should not trap");
}

// ── Phase D mocks ────────────────────────────────────────────────────────

#[derive(Clone)]
struct SpyPwm {
    history: Rc<RefCell<Vec<u32>>>,
}

impl DynPwm for SpyPwm {
    fn set_duty(&mut self, duty_0_10000: u32) {
        self.history.borrow_mut().push(duty_0_10000);
    }
}

struct FixedAdc {
    value: u32,
}

impl DynAdc for FixedAdc {
    fn read(&mut self) -> u32 {
        self.value
    }
}

/// Ring-buffered "loopback" UART: whatever is written is queued to be read
/// back on the next `read()`.
#[derive(Clone)]
struct LoopbackUart {
    buf: Rc<RefCell<Vec<u8>>>,
}

impl DynUart for LoopbackUart {
    fn write(&mut self, data: &[u8]) -> i32 {
        self.buf.borrow_mut().extend_from_slice(data);
        data.len() as i32
    }

    fn read(&mut self, out: &mut [u8], _timeout_ms: u32) -> i32 {
        let mut buf = self.buf.borrow_mut();
        let n = core::cmp::min(out.len(), buf.len());
        out[..n].copy_from_slice(&buf[..n]);
        buf.drain(..n);
        n as i32
    }
}

#[test]
fn adapter_pwm_fade_drives_wasm() {
    let pwm = SpyPwm {
        history: Rc::new(RefCell::new(Vec::new())),
    };
    let history = pwm.history.clone();

    let adapter = EmbeddedHalAdapter::builder()
        .pwm(5, Box::new(pwm))
        .delay(Box::new(NullDelay))
        .build();

    let wasm = fixture("pwm_fade");
    let mut runtime = MiloRuntime::new(adapter, None);
    let logs = runtime
        .execute(&wasm, "run_logic")
        .expect("pwm_fade should execute");

    let calls = history.borrow();
    // pwm_fade ramps 0, 500, 1000, ..., 10000, then sets 0 at the end.
    assert_eq!(calls.first().copied(), Some(0), "should start at 0%");
    assert_eq!(
        calls.last().copied(),
        Some(0),
        "should end at 0% after ramp"
    );
    assert!(
        calls.iter().any(|&d| d == 10000),
        "should reach 100% during ramp"
    );
    // Log frames should be "pwm fade start" and "pwm fade done".
    assert!(logs.contains(&String::from("pwm fade start")));
    assert!(logs.contains(&String::from("pwm fade done")));
}

#[test]
fn adapter_adc_read_averages_correctly() {
    let adapter = EmbeddedHalAdapter::builder()
        .adc(0, Box::new(FixedAdc { value: 2048 }))
        .delay(Box::new(NullDelay))
        .build();

    let wasm = fixture("adc_read");
    let mut runtime = MiloRuntime::new(adapter, None);
    let logs = runtime
        .execute(&wasm, "run_logic")
        .expect("adc_read should execute");

    let avg_log = logs
        .iter()
        .find(|m| m.starts_with("adc_avg="))
        .expect("driver should emit adc_avg log");
    assert_eq!(avg_log, "adc_avg=2048");
}

#[test]
fn adapter_uart_loopback_roundtrip() {
    let uart = LoopbackUart {
        buf: Rc::new(RefCell::new(Vec::new())),
    };
    let adapter = EmbeddedHalAdapter::builder()
        .uart(1, Box::new(uart))
        .delay(Box::new(NullDelay))
        .build();

    let wasm = fixture("uart_loopback");
    let mut runtime = MiloRuntime::new(adapter, None);
    let logs = runtime
        .execute(&wasm, "run_logic")
        .expect("uart_loopback should execute");

    assert!(
        logs.iter().any(|m| m == "uart_loopback ok"),
        "expected uart_loopback ok log, got {:?}",
        logs
    );
}

#[test]
fn adapter_i2c_path_works() {
    // Custom wasm that calls i2c_transfer isn't in fixtures yet; instead
    // drive the trait method directly via the adapter.
    let mut adapter = EmbeddedHalAdapter::builder()
        .i2c(0, Box::new(EchoI2c))
        .delay(Box::new(NullDelay))
        .build();

    use milo_receiver::MiloHardware;

    let mut rx = [0u8; 3];
    let rc = adapter.i2c_transfer(0x48, &[1, 2], &mut rx);
    assert_eq!(rc, 0);
    assert_eq!(rx, [0xAB, 0xAB, 0xAB]);
}
