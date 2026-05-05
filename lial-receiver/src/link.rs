/// LIAL-Link v0.1 -- Length-prefixed binary frames over a byte stream.
///
/// Frame format: [opcode: u8][payload_len: u32 big-endian][payload: bytes]
///
/// OpCodes:
///   0x01 -- Discovery (Receiver -> Host): JSON hardware manifest
///   0x02 -- Bytecode Push (Host -> Receiver): raw wasm bytes
///   0x03 -- Execution Log/Feedback (Receiver -> Host): JSON result
use alloc::string::String;
use alloc::vec::Vec;

pub const OP_DISCOVERY: u8 = 0x01;
pub const OP_BYTECODE_PUSH: u8 = 0x02;
pub const OP_EXEC_RESULT: u8 = 0x03;
pub const OP_STREAM_DATA: u8 = 0x04;
pub const OP_STOP: u8 = 0x05;
pub const OP_QUERY_STATUS: u8 = 0x06;
pub const OP_STATUS_RESPONSE: u8 = 0x07;
pub const OP_SET_PARAM: u8 = 0x08;
pub const OP_HOT_SWAP: u8 = 0x09;

#[derive(Debug)]
pub struct Frame {
    pub opcode: u8,
    pub payload: Vec<u8>,
}

impl Frame {
    pub fn new(opcode: u8, payload: Vec<u8>) -> Self {
        Self { opcode, payload }
    }

    pub fn serialize(&self) -> Vec<u8> {
        let len = self.payload.len() as u32;
        let mut buf = Vec::with_capacity(5 + self.payload.len());
        buf.push(self.opcode);
        buf.extend_from_slice(&len.to_be_bytes());
        buf.extend_from_slice(&self.payload);
        buf
    }
}

#[derive(Debug)]
pub enum LinkError {
    Io(String),
    UnexpectedOpcode { expected: u8, got: u8 },
    ConnectionClosed,
}

impl core::fmt::Display for LinkError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::Io(e) => write!(f, "link I/O error: {e}"),
            Self::UnexpectedOpcode { expected, got } => {
                write!(f, "expected opcode 0x{expected:02x}, got 0x{got:02x}")
            }
            Self::ConnectionClosed => write!(f, "connection closed"),
        }
    }
}

#[cfg(feature = "std")]
impl std::error::Error for LinkError {}

/// Read exactly `n` bytes from a reader. Returns LinkError on short read / EOF.
#[cfg(feature = "std")]
fn read_exact(reader: &mut dyn std::io::Read, buf: &mut [u8]) -> Result<(), LinkError> {
    reader
        .read_exact(buf)
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::UnexpectedEof {
                LinkError::ConnectionClosed
            } else {
                LinkError::Io(alloc::format!("{e}"))
            }
        })
}

/// Read one LIAL-Link frame from a byte stream.
#[cfg(feature = "std")]
pub fn read_frame(reader: &mut dyn std::io::Read) -> Result<Frame, LinkError> {
    let mut header = [0u8; 5];
    read_exact(reader, &mut header)?;
    let opcode = header[0];
    let len = u32::from_be_bytes([header[1], header[2], header[3], header[4]]) as usize;
    let mut payload = vec![0u8; len];
    if len > 0 {
        read_exact(reader, &mut payload)?;
    }
    Ok(Frame { opcode, payload })
}

/// Write one LIAL-Link frame to a byte stream.
#[cfg(feature = "std")]
pub fn write_frame(writer: &mut dyn std::io::Write, frame: &Frame) -> Result<(), LinkError> {
    let bytes = frame.serialize();
    writer
        .write_all(&bytes)
        .map_err(|e| LinkError::Io(alloc::format!("{e}")))?;
    writer
        .flush()
        .map_err(|e| LinkError::Io(alloc::format!("{e}")))?;
    Ok(())
}
