//! Transport abstraction for MILO-Link.
//!
//! The `MiloTransport` trait decouples frame I/O from hardware so the main loop
//! works identically over USB serial, TCP, BLE, or stdio.

#[cfg(feature = "esp32c3")]
pub mod wifi;

use crate::engine::link::{Frame, LinkError};
use alloc::vec;

/// Platform-agnostic transport layer for MILO-Link frames.
pub trait MiloTransport {
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

impl<W: embedded_io::Write, R: embedded_io::Read> MiloTransport for EmbeddedIoTransport<W, R> {
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
impl MiloTransport for StdioTransport {
    fn read_frame(&mut self) -> Result<Frame, LinkError> {
        crate::engine::link::read_frame(&mut self.stdin)
    }

    fn write_frame(&mut self, frame: &Frame) -> Result<(), LinkError> {
        crate::engine::link::write_frame(&mut self.stdout, frame)
    }
}

/// TCP server transport (std only) — used by the virtual fleet emulator and
/// as the reference implementation for the Wi-Fi receiver transport.
///
/// Accepts one host connection at a time and survives disconnects: when the
/// client drops, the next `read_frame` goes back to accepting. Reads are
/// short-timeout and internally buffered, so `read_frame` returns `Err`
/// periodically even while idle — the main loop uses those gaps to poll the
/// executor for results from long-running modules.
#[cfg(feature = "std")]
pub struct TcpServerTransport {
    listener: std::net::TcpListener,
    client: Option<std::net::TcpStream>,
    buf: alloc::vec::Vec<u8>,
}

#[cfg(feature = "std")]
impl TcpServerTransport {
    pub fn bind(port: u16) -> std::io::Result<Self> {
        let listener = std::net::TcpListener::bind(("127.0.0.1", port))?;
        listener.set_nonblocking(true)?;
        Ok(Self {
            listener,
            client: None,
            buf: alloc::vec::Vec::new(),
        })
    }

    fn ensure_client(&mut self) -> Result<(), LinkError> {
        if self.client.is_some() {
            return Ok(());
        }
        match self.listener.accept() {
            Ok((stream, _)) => {
                stream
                    .set_read_timeout(Some(std::time::Duration::from_millis(50)))
                    .ok();
                stream.set_nodelay(true).ok();
                self.buf.clear();
                self.client = Some(stream);
                Ok(())
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(std::time::Duration::from_millis(20));
                Err(LinkError::Io(alloc::string::String::from("no client")))
            }
            Err(e) => Err(LinkError::Io(alloc::format!("{e}"))),
        }
    }

    /// Try to parse one complete frame out of the internal buffer.
    fn take_frame(&mut self) -> Option<Frame> {
        if self.buf.len() < 5 {
            return None;
        }
        let len = u32::from_be_bytes([self.buf[1], self.buf[2], self.buf[3], self.buf[4]]) as usize;
        if self.buf.len() < 5 + len {
            return None;
        }
        let opcode = self.buf[0];
        let payload = self.buf[5..5 + len].to_vec();
        self.buf.drain(..5 + len);
        Some(Frame::new(opcode, payload))
    }
}

#[cfg(feature = "std")]
impl MiloTransport for TcpServerTransport {
    fn read_frame(&mut self) -> Result<Frame, LinkError> {
        use std::io::Read;

        self.ensure_client()?;
        if let Some(frame) = self.take_frame() {
            return Ok(frame);
        }

        let mut chunk = [0u8; 4096];
        let stream = self.client.as_mut().expect("client checked above");
        match stream.read(&mut chunk) {
            Ok(0) => {
                // Client disconnected; go back to accepting.
                self.client = None;
                self.buf.clear();
                Err(LinkError::ConnectionClosed)
            }
            Ok(n) => {
                self.buf.extend_from_slice(&chunk[..n]);
                self.take_frame()
                    .ok_or(LinkError::Io(alloc::string::String::from("partial frame")))
            }
            Err(e)
                if e.kind() == std::io::ErrorKind::WouldBlock
                    || e.kind() == std::io::ErrorKind::TimedOut =>
            {
                Err(LinkError::Io(alloc::string::String::from("idle")))
            }
            Err(e) => {
                self.client = None;
                self.buf.clear();
                Err(LinkError::Io(alloc::format!("{e}")))
            }
        }
    }

    fn write_frame(&mut self, frame: &Frame) -> Result<(), LinkError> {
        use std::io::Write;

        let Some(stream) = self.client.as_mut() else {
            return Err(LinkError::ConnectionClosed);
        };
        let bytes = frame.serialize();
        match stream.write_all(&bytes).and_then(|_| stream.flush()) {
            Ok(()) => Ok(()),
            Err(e) => {
                self.client = None;
                self.buf.clear();
                Err(LinkError::Io(alloc::format!("{e}")))
            }
        }
    }
}
