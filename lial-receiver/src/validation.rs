//! Wasm module validation.
//!
//! Before execution, scan the module's imports and reject any that are not
//! in the LIAL Alphabet. This prevents malicious or buggy modules from
//! requesting functionality that doesn't exist.

use alloc::string::String;
use alloc::vec::Vec;

/// The complete set of allowed LIAL syscalls (the "Alphabet").
const ALLOWED_IMPORTS: &[&str] = &[
    "lial_gpio_set",
    "lial_gpio_get",
    "lial_delay_ms",
    "lial_get_uptime_us",
    "lial_i2c_transfer",
    "lial_log",
    "lial_pwm_set",
    "lial_adc_read",
    "lial_spi_transfer",
    "lial_uart_write",
    "lial_uart_read",
    "lial_get_param",
];

/// Result of Wasm validation.
#[derive(Debug)]
pub struct ValidationResult {
    pub valid: bool,
    pub rejected_imports: Vec<String>,
}

/// Validate a Wasm module's imports against the LIAL Alphabet.
///
/// Returns `Ok(())` if all imports are allowed, or `Err` with the list of
/// rejected import names.
pub fn validate_wasm_imports(wasm_bytes: &[u8]) -> ValidationResult {
    // Parse the import section from the Wasm binary.
    // Wasm binary format: magic (4) + version (4) + sections...
    // Each section: id (1) + size (LEB128) + content
    // Import section has id = 2
    let mut rejected = Vec::new();

    if wasm_bytes.len() < 8 {
        return ValidationResult {
            valid: false,
            rejected_imports: alloc::vec![String::from("invalid wasm: too short")],
        };
    }

    // Check magic number
    if &wasm_bytes[0..4] != b"\x00asm" {
        return ValidationResult {
            valid: false,
            rejected_imports: alloc::vec![String::from("invalid wasm: bad magic")],
        };
    }

    let mut pos = 8; // skip magic + version

    while pos < wasm_bytes.len() {
        let section_id = wasm_bytes[pos];
        pos += 1;

        let (section_size, bytes_read) = read_leb128_u32(&wasm_bytes[pos..]);
        pos += bytes_read;

        if section_id == 2 {
            // Import section
            let section_end = pos + section_size as usize;
            let (num_imports, bytes_read) = read_leb128_u32(&wasm_bytes[pos..]);
            pos += bytes_read;

            for _ in 0..num_imports {
                // module name
                let (mod_len, br) = read_leb128_u32(&wasm_bytes[pos..]);
                pos += br;
                let _module_name = core::str::from_utf8(&wasm_bytes[pos..pos + mod_len as usize])
                    .unwrap_or("");
                pos += mod_len as usize;

                // field name
                let (field_len, br) = read_leb128_u32(&wasm_bytes[pos..]);
                pos += br;
                let field_name =
                    core::str::from_utf8(&wasm_bytes[pos..pos + field_len as usize])
                        .unwrap_or("");
                pos += field_len as usize;

                // import descriptor (kind + type index/etc)
                let kind = wasm_bytes[pos];
                pos += 1;
                match kind {
                    0x00 => {
                        // function import — read type index
                        let (_, br) = read_leb128_u32(&wasm_bytes[pos..]);
                        pos += br;
                    }
                    0x01 => {
                        // table import
                        pos += 1; // reftype
                        let (_, br) = read_leb128_u32(&wasm_bytes[pos..]); // limits flag
                        pos += br;
                        let (_, br) = read_leb128_u32(&wasm_bytes[pos..]); // min
                        pos += br;
                        // may have max
                    }
                    0x02 => {
                        // memory import
                        let (flags, br) = read_leb128_u32(&wasm_bytes[pos..]);
                        pos += br;
                        let (_, br) = read_leb128_u32(&wasm_bytes[pos..]); // min
                        pos += br;
                        if flags & 1 != 0 {
                            let (_, br) = read_leb128_u32(&wasm_bytes[pos..]); // max
                            pos += br;
                        }
                    }
                    0x03 => {
                        // global import
                        pos += 1; // valtype
                        pos += 1; // mutability
                    }
                    _ => {}
                }

                // Validate: only function imports from "env" module are checked
                if kind == 0x00 && !ALLOWED_IMPORTS.contains(&field_name) {
                    rejected.push(String::from(field_name));
                }
            }

            break; // Only need to parse the import section
        } else {
            pos += section_size as usize;
        }
    }

    ValidationResult {
        valid: rejected.is_empty(),
        rejected_imports: rejected,
    }
}

/// Read a LEB128-encoded u32. Returns (value, bytes_consumed).
fn read_leb128_u32(data: &[u8]) -> (u32, usize) {
    let mut result: u32 = 0;
    let mut shift = 0;
    let mut pos = 0;

    loop {
        if pos >= data.len() {
            break;
        }
        let byte = data[pos];
        pos += 1;
        result |= ((byte & 0x7F) as u32) << shift;
        if byte & 0x80 == 0 {
            break;
        }
        shift += 7;
        if shift >= 35 {
            break;
        }
    }

    (result, pos)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_leb128_decode() {
        assert_eq!(read_leb128_u32(&[0x00]), (0, 1));
        assert_eq!(read_leb128_u32(&[0x01]), (1, 1));
        assert_eq!(read_leb128_u32(&[0x80, 0x01]), (128, 2));
        assert_eq!(read_leb128_u32(&[0xE5, 0x8E, 0x26]), (624485, 3));
    }
}
