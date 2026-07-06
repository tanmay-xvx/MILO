//! Ed25519 bytecode signature verification (feature `signing`).
//!
//! Wire format for `OP_SIGNED_PUSH` / `OP_SIGNED_SWAP` payloads:
//!
//! ```text
//! [64-byte Ed25519 signature over the wasm bytes][wasm bytes]
//! ```
//!
//! The receiver holds a single trusted verifying key (32 bytes). With
//! `SecurityPolicy::require_signed`, unsigned pushes are rejected outright —
//! the device only ever instantiates modules signed by the fleet operator.
//! Verification happens *before* import validation, which happens before
//! instantiation: three gates between the wire and the first instruction.

pub const SIG_LEN: usize = 64;
pub const PUBKEY_LEN: usize = 32;

/// Split a signed payload into (signature, wasm). None if too short.
pub fn split_signed(payload: &[u8]) -> Option<(&[u8], &[u8])> {
    if payload.len() <= SIG_LEN {
        return None;
    }
    Some(payload.split_at(SIG_LEN))
}

/// Verify a signed payload against the trusted key and return the wasm bytes.
#[cfg(feature = "signing")]
pub fn verify_signed<'a>(
    payload: &'a [u8],
    pubkey: &[u8; PUBKEY_LEN],
) -> Result<&'a [u8], &'static str> {
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};

    let (sig_bytes, wasm) = split_signed(payload).ok_or("signed payload too short")?;
    let key = VerifyingKey::from_bytes(pubkey).map_err(|_| "malformed trusted key")?;
    let sig = Signature::from_slice(sig_bytes).map_err(|_| "malformed signature")?;
    key.verify(wasm, &sig)
        .map_err(|_| "signature verification failed")?;
    Ok(wasm)
}

#[cfg(not(feature = "signing"))]
pub fn verify_signed<'a>(
    _payload: &'a [u8],
    _pubkey: &[u8; PUBKEY_LEN],
) -> Result<&'a [u8], &'static str> {
    Err("signing not supported in this build")
}

#[cfg(all(test, feature = "signing", feature = "std"))]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    fn keypair() -> (SigningKey, [u8; 32]) {
        let sk = SigningKey::from_bytes(&[7u8; 32]);
        let pk = sk.verifying_key().to_bytes();
        (sk, pk)
    }

    #[test]
    fn accepts_valid_signature() {
        let (sk, pk) = keypair();
        let wasm = b"\x00asm fake module bytes";
        let sig = sk.sign(wasm);
        let mut payload = sig.to_bytes().to_vec();
        payload.extend_from_slice(wasm);
        assert_eq!(verify_signed(&payload, &pk).unwrap(), wasm);
    }

    #[test]
    fn rejects_tampered_module() {
        let (sk, pk) = keypair();
        let wasm = b"\x00asm fake module bytes";
        let sig = sk.sign(wasm);
        let mut payload = sig.to_bytes().to_vec();
        payload.extend_from_slice(wasm);
        let last = payload.len() - 1;
        payload[last] ^= 0xFF; // flip one bit of the module
        assert!(verify_signed(&payload, &pk).is_err());
    }

    #[test]
    fn rejects_wrong_key() {
        let (sk, _) = keypair();
        let other_pk = SigningKey::from_bytes(&[9u8; 32]).verifying_key().to_bytes();
        let wasm = b"module";
        let sig = sk.sign(wasm);
        let mut payload = sig.to_bytes().to_vec();
        payload.extend_from_slice(wasm);
        assert!(verify_signed(&payload, &other_pk).is_err());
    }

    #[test]
    fn rejects_short_payload() {
        let (_, pk) = keypair();
        assert!(verify_signed(&[0u8; 10], &pk).is_err());
    }
}
