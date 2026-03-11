use wasmi::{Engine, Linker, Module, Store, Caller, Func};
use std::fs;

// --- 1. THE ATOMIC ALPHABET ---
// These are the "Manual" functions that talk to your actual hardware.
fn lial_gpio_set(_caller: Caller<'_, ()>, pin: u32, state: u32) {
    println!(" [SILICON] GPIO {} -> {}", pin, if state == 1 { "ON" } else { "OFF" });
}

fn lial_delay_ms(_caller: Caller<'_, ()>, ms: u32) {
    println!(" [TIMER] Waiting {}ms...", ms);
    // For this laptop test, we use standard thread sleep
    std::thread::sleep(std::time::Duration::from_millis(ms as u64));
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("🚀 LIAL Receiver Active (Rust Engine)");

    // 2. Initialize the Wasm Engine
    let engine = Engine::default();
    let mut store = Store::new(&engine, ());
    let mut linker = <Linker<()>>::new(&engine);

    // 3. WRAPPING & LINKING THE ALPHABET
    // We must wrap Rust functions so Wasm can understand their "signature"
    let gpio_set_func = Func::wrap(&mut store, lial_gpio_set);
    let delay_ms_func = Func::wrap(&mut store, lial_delay_ms);

    linker.define("env", "lial_gpio_set", gpio_set_func)?;
    linker.define("env", "lial_delay_ms", delay_ms_func)?;

    // 4. LOAD THE BYTECODE
    // Make sure you compiled mock_driver.wasm in the examples folder!
    println!("📂 Loading LLM Bytecode...");
    let wasm_bytes = fs::read("../examples/mock_driver/target/wasm32-unknown-unknown/release/mock_driver.wasm")
        .expect("Could not find mock_driver.wasm. Build it with: cargo build --target wasm32-unknown-unknown --release");
    
    let module = Module::new(&engine, &wasm_bytes[..])?;

    // 5. INSTANTIATE AND RUN
    let instance = linker.instantiate_and_start(&mut store, &module)?;

    let run_logic = instance.get_typed_func::<(), ()>(&store, "run_logic")?;

    println!("🚀 Executing LIAL Driver...");
    run_logic.call(&mut store, ())?;

    println!("✅ Task Complete.");
    Ok(())
}