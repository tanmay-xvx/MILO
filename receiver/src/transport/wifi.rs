//! WiFi TCP transport for ESP32-C3.
//!
//! Implements `MiloTransport` over a persistent TCP socket on port 9100.
//! The receiver acts as a TCP server: it listens for one client connection
//! and processes MILO-Link frames over that socket.

use crate::engine::link::{Frame, LinkError};
use super::MiloTransport;
use alloc::string::String;
use alloc::vec;

/// TCP port for MILO-Link WiFi transport.
pub const MILO_TCP_PORT: u16 = 9100;

/// WiFi credentials configuration.
pub struct WifiConfig {
    pub ssid: &'static str,
    pub password: &'static str,
}

/// WiFi TCP server transport.
///
/// After WiFi association and DHCP, this listens on `MILO_TCP_PORT` and
/// accepts a single client. Frames are read/written over the TCP stream
/// using the standard MILO-Link 5-byte header protocol.
///
/// This is a skeleton implementation. The actual `esp-wifi` + `smoltcp`
/// integration requires runtime initialization that depends on the specific
/// `esp-hal` version and is wired up in the entry point (`main.rs`).
pub struct WifiTcpTransport {
    /// Opaque handle to the active TCP socket.
    /// In practice this wraps `smoltcp::socket::tcp::Socket` state.
    rx_buf: [u8; 1536],
    tx_buf: [u8; 1536],
    connected: bool,
}

impl WifiTcpTransport {
    pub fn new() -> Self {
        Self {
            rx_buf: [0u8; 1536],
            tx_buf: [0u8; 1536],
            connected: false,
        }
    }

    pub fn is_client_connected(&self) -> bool {
        self.connected
    }
}

impl MiloTransport for WifiTcpTransport {
    fn read_frame(&mut self) -> Result<Frame, LinkError> {
        // Placeholder: actual implementation requires smoltcp socket polling.
        // The real implementation will be wired when esp-wifi integration
        // is tested on hardware.
        Err(LinkError::Io(String::from("wifi transport not yet active")))
    }

    fn write_frame(&mut self, frame: &Frame) -> Result<(), LinkError> {
        // Placeholder: write frame bytes to TCP socket.
        Err(LinkError::Io(String::from("wifi transport not yet active")))
    }
}
