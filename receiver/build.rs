fn main() {
    // The hand-written memory.x is only for the RP2040 (cortex-m-rt's link.x
    // does `INCLUDE memory.x`). It must NOT live in the crate root: lld
    // resolves INCLUDE from the cwd first, so a root memory.x shadows the
    // esp-hal-generated one and breaks the ESP32-C3 link (undefined IROM).
    if std::env::var("CARGO_FEATURE_RP2040").is_ok() {
        println!(
            "cargo:rustc-link-search={}/ld/rp2040",
            std::env::var("CARGO_MANIFEST_DIR").unwrap()
        );
    }
    println!("cargo:rerun-if-changed=ld/rp2040/memory.x");
}
