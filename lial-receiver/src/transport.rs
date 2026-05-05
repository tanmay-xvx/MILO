//! Transport abstraction for LIAL-Link.
//!
//! The `LialTransport` trait decouples frame I/O from hardware so the main loop
//! works identically over USB serial, TCP, BLE, or stdio.

use crate::link::{Frame, LinkError};
use alloc::vec;

/// Platform-agnostic transport layer for LIAL-Link frames.
pub trait LialTransport {
    fn read_frame(&mut self) -> Result<Frame, LinkError>;
    fn write_frame(&mut self, frame: &Frame) -> Result<(), LinkError>;
}

/// Transport over any `embedded_io::Read` + `embedded_io::Write` pair.
///
/// Used for USB-Serial-JTAG on ESP32-C3 and USB CDC on RP2040.
pub struct EmbeddedIoTransport<W, R> {
    writer: W,
    reader: R,
}

impl<W, R> EmbeddedIoTransport<W, R> {
    pub fn new(writer: W, reader: R) -> Self {
        Self { writer, reader }
    }
}

impl<W: embedded_io::Write, R: embedded_io::Read> EmbeddedIoTransport<W, R> {
    fn read_exact_bytes(&mut self, buf: &mut [u8]) {
        let mut pos = 0;
        while pos < buf.len() {
            match self.reader.read(&mut buf[pos..]) {
                Ok(n) if n > 0 => pos += n,
                _ => {}
            }
        }
    }
}

impl<W: embedded_io::Write, R: embedded_io::Read> LialTransport for EmbeddedIoTransport<W, R> {
    fn read_frame(&mut self) -> Result<Frame, LinkError> {
        let mut header = [0u8; 5];
        self.read_exact_bytes(&mut header);
        let opcode = header[0];
        let len = u32::from_be_bytes([header[1], header[2], header[3], header[4]]) as usize;
        let mut payload = vec![0u8; len];
        if len > 0 {
            self.read_exact_bytes(&mut payload);
        }
        Ok(Frame::new(opcode, payload))
    }

    fn write_frame(&mut self, frame: &Frame) -> Result<(), LinkError> {
        let bytes = frame.serialize();
        self.writer
            .write_all(&bytes)
            .map_err(|_| LinkError::Io(alloc::string::String::from("write failed")))?;
        Ok(())
    }
}

/// Stdio-based transport for laptop testing (std feature only).
#[cfg(feature = "std")]
pub struct StdioTransport {
    stdin: std::io::StdinLock<'static>,
    stdout: std::io::StdoutLock<'static>,
}

#[cfg(feature = "std")]
impl StdioTransport {
    pub fn new() -> Self {
        Self {
            stdin: std::io::stdin().lock(),
            stdout: std::io::stdout().lock(),
        }
    }
}

#[cfg(feature = "std")]
impl LialTransport for StdioTransport {
    fn read_frame(&mut self) -> Result<Frame, LinkError> {
        crate::link::read_frame(&mut self.stdin)
    }

    fn write_frame(&mut self, frame: &Frame) -> Result<(), LinkError> {
        crate::link::write_frame(&mut self.stdout, frame)
    }
}
