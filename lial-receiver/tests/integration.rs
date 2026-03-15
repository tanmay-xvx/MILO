use lial_receiver::{LialError, LialRuntime, mock::LaptopMock};
use std::fs;

fn fixture_path(name: &str) -> String {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    match name {
        "mock_driver" => format!(
            "{manifest_dir}/../examples/mock_driver/target/wasm32-unknown-unknown/release/mock_driver.wasm"
        ),
        "infinite_loop" => format!(
            "{manifest_dir}/../examples/test_drivers/infinite_loop/target/wasm32-unknown-unknown/release/infinite_loop.wasm"
        ),
        "no_export" => format!(
            "{manifest_dir}/../examples/test_drivers/no_export/target/wasm32-unknown-unknown/release/no_export.wasm"
        ),
        _ => panic!("unknown fixture: {name}"),
    }
}

#[test]
fn test_happy_path() {
    let wasm = fs::read(fixture_path("mock_driver")).expect("mock_driver.wasm not found");
    let hw = LaptopMock::new();
    let mut runtime = LialRuntime::new(hw, None);
    let logs = runtime.execute(&wasm, "run_logic").expect("should succeed");
    // mock_driver calls gpio_set and delay_ms -- no lial_log calls, so logs is empty
    assert!(logs.is_empty());
}

#[test]
fn test_missing_export() {
    let wasm = fs::read(fixture_path("no_export")).expect("no_export.wasm not found");
    let hw = LaptopMock::new();
    let mut runtime = LialRuntime::new(hw, None);
    let result = runtime.execute(&wasm, "run_logic");
    assert!(
        matches!(result, Err(LialError::MissingExport(_))),
        "expected MissingExport, got: {result:?}"
    );
}

#[test]
fn test_fuel_exhaustion() {
    let wasm = fs::read(fixture_path("infinite_loop")).expect("infinite_loop.wasm not found");
    let hw = LaptopMock::new();
    let mut runtime = LialRuntime::new(hw, Some(10_000));
    let result = runtime.execute(&wasm, "run_logic");
    assert!(
        matches!(result, Err(LialError::FuelExhausted)),
        "expected FuelExhausted, got: {result:?}"
    );
}

#[test]
fn test_bad_module() {
    let garbage = b"this is not wasm";
    let hw = LaptopMock::new();
    let mut runtime = LialRuntime::new(hw, None);
    let result = runtime.execute(garbage, "run_logic");
    assert!(
        matches!(result, Err(LialError::ModuleInvalid(_))),
        "expected ModuleInvalid, got: {result:?}"
    );
}
