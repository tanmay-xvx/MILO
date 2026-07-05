"""
MILO JIT Compiler -- Compiles Rust source code into wasm32-unknown-unknown bytecode
suitable for the MILO receiver (including memory-constrained targets like ESP32-C3).
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading

RUST_LIB_TEMPLATE = """\
#![no_std]

#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! {{ loop {{}} }}

unsafe extern "C" {{
    fn gpio_set(pin: u32, state: u32);
    fn gpio_get(pin: u32) -> u32;
    fn delay_ms(ms: u32);
    fn get_uptime_us() -> u64;
    fn i2c_transfer(addr: u32, tx_ptr: u32, tx_len: u32, rx_ptr: u32, rx_len: u32) -> i32;
    fn log_msg(ptr: u32, len: u32);
    fn pwm_set(channel: u32, duty_0_10000: u32);
    fn adc_read(channel: u32) -> u32;
    fn spi_transfer(bus: u32, tx_ptr: u32, tx_len: u32, rx_ptr: u32, rx_len: u32) -> i32;
    fn uart_write(bus: u32, ptr: u32, len: u32) -> i32;
    fn uart_read(bus: u32, ptr: u32, len_max: u32, timeout_ms: u32) -> i32;
    fn get_param(slot: u32) -> u32;
}}

{body}
"""

CARGO_TOML = """\
[package]
name = "milo_driver"
version = "0.1.0"
edition = "2024"

[lib]
crate-type = ["cdylib"]

[profile.release]
opt-level = "z"
lto = true
"""

CARGO_CONFIG = """\
[target.wasm32-unknown-unknown]
rustflags = [
    "-C", "link-arg=--initial-memory=65536",
    "-C", "link-arg=--max-memory=65536",
    "-C", "link-arg=-z",
    "-C", "link-arg=stack-size=8192",
]
"""


# ── Compile cache ────────────────────────────────────────────────────────
#
# Two layers, both keyed on the exact generated lib.rs so they are always
# correct:
#   1. Content cache: identical source → return the cached .wasm immediately
#      (an LLM retry loop and a fleet-wide push recompile the same body a lot).
#   2. Warm project: one persistent cargo project per process reused across
#      compiles, so cargo's target/ and registry stay hot and only the changed
#      lib.rs is rebuilt — no fresh toolchain setup on every call.
#
# Set MILO_COMPILE_CACHE=0 to disable and fall back to isolated temp dirs.

_CACHE_ROOT = os.path.join(tempfile.gettempdir(), "milo_compile_cache")
_WASM_CACHE = os.path.join(_CACHE_ROOT, "wasm")
_WARM_PROJECT = os.path.join(_CACHE_ROOT, "project")
_compile_lock = threading.Lock()


def _cache_enabled() -> bool:
    return os.environ.get("MILO_COMPILE_CACHE", "1") != "0"


def _ensure_warm_project() -> str:
    src_dir = os.path.join(_WARM_PROJECT, "src")
    cargo_dir = os.path.join(_WARM_PROJECT, ".cargo")
    os.makedirs(src_dir, exist_ok=True)
    os.makedirs(cargo_dir, exist_ok=True)
    _write_if_changed(os.path.join(_WARM_PROJECT, "Cargo.toml"), CARGO_TOML)
    _write_if_changed(os.path.join(cargo_dir, "config.toml"), CARGO_CONFIG)
    return _WARM_PROJECT


def _write_if_changed(path: str, content: str) -> None:
    """Avoid touching mtimes cargo watches unless the content actually changed."""
    try:
        with open(path) as f:
            if f.read() == content:
                return
    except FileNotFoundError:
        pass
    with open(path, "w") as f:
        f.write(content)


def _build_in(project_dir: str, lib_rs: str) -> bytes:
    _write_if_changed(os.path.join(project_dir, "src", "lib.rs"), lib_rs)
    result = subprocess.run(
        ["cargo", "build", "--target", "wasm32-unknown-unknown", "--release"],
        capture_output=True,
        text=True,
        cwd=project_dir,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    wasm_path = os.path.join(
        project_dir, "target", "wasm32-unknown-unknown", "release", "milo_driver.wasm"
    )
    with open(wasm_path, "rb") as f:
        wasm_bytes = f.read()
    assert wasm_bytes[:4] == b"\x00asm", "Compiler produced invalid wasm"
    return wasm_bytes


def compile_rust_to_wasm(body: str) -> bytes:
    """
    Compile a Rust function body into a wasm binary.

    The body should contain at minimum:
        #[unsafe(no_mangle)]
        pub extern "C" fn run_logic() { ... }

    The compiler wraps it with #![no_std], panic handler, and extern declarations.

    Uses a persistent content + warm-project cache (see above) for speed;
    identical source is returned from cache, and a reused cargo project keeps
    the toolchain warm across compiles. Set MILO_COMPILE_CACHE=0 to disable.

    Returns raw .wasm bytes.
    Raises RuntimeError with the compiler stderr on failure.
    """
    lib_rs = RUST_LIB_TEMPLATE.format(body=body)

    if not _cache_enabled():
        with tempfile.TemporaryDirectory(prefix="milo_") as tmpdir:
            os.makedirs(os.path.join(tmpdir, "src"))
            os.makedirs(os.path.join(tmpdir, ".cargo"))
            _write_if_changed(os.path.join(tmpdir, "Cargo.toml"), CARGO_TOML)
            _write_if_changed(os.path.join(tmpdir, ".cargo", "config.toml"), CARGO_CONFIG)
            return _build_in(tmpdir, lib_rs)

    key = hashlib.sha256(lib_rs.encode()).hexdigest()
    cached = os.path.join(_WASM_CACHE, key + ".wasm")

    # Serialize compiles: one warm project can't be built concurrently, and the
    # content cache means contending callers usually hit the cache anyway.
    with _compile_lock:
        if os.path.exists(cached):
            with open(cached, "rb") as f:
                return f.read()

        project = _ensure_warm_project()
        wasm_bytes = _build_in(project, lib_rs)

        os.makedirs(_WASM_CACHE, exist_ok=True)
        tmp = cached + ".tmp"
        with open(tmp, "wb") as f:
            f.write(wasm_bytes)
        os.replace(tmp, cached)
        return wasm_bytes


def clear_compile_cache() -> None:
    """Remove the persistent compile cache (wasm + warm project)."""
    shutil.rmtree(_CACHE_ROOT, ignore_errors=True)


if __name__ == "__main__":
    test_body = '''
#[unsafe(no_mangle)]
pub extern "C" fn run_logic() {
    unsafe {
        let msg = b"hello from compiler test";
        log_msg(msg.as_ptr() as u32, msg.len() as u32);
        for _ in 0..3 {
            gpio_set(5, 1);
            delay_ms(200);
            gpio_set(5, 0);
            delay_ms(200);
        }
    }
}
'''
    print("Testing Rust -> wasm pipeline...")
    wasm = compile_rust_to_wasm(test_body)
    print(f"Success: {len(wasm)} bytes")
