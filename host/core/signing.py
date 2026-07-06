"""Ed25519 signing for MILO bytecode (host side).

Mirrors `receiver/src/engine/signing.rs`: an `OP_SIGNED_PUSH` /
`OP_SIGNED_SWAP` payload is `[64-byte signature over the wasm][wasm bytes]`.
The signing key is a 32-byte Ed25519 seed stored as 64 hex chars; the public
key (also 64 hex chars) is what a receiver is provisioned with via
`MILO_TRUSTED_KEY`.

Uses the `cryptography` package if present; raises a clear error otherwise.
"""

from __future__ import annotations

import os


def _require_crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )

        return Ed25519PrivateKey, Ed25519PublicKey
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "signing requires the 'cryptography' package: pip install cryptography"
        ) from e


def generate_keypair() -> tuple[str, str]:
    """Return (private_hex, public_hex). Private is the 32-byte seed."""
    from cryptography.hazmat.primitives import serialization

    Ed25519PrivateKey, _ = _require_crypto()
    sk = Ed25519PrivateKey.generate()
    seed = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return seed.hex(), pub.hex()


def public_key_hex(private_hex: str) -> str:
    """Derive the public key (hex) from a private seed (hex)."""
    from cryptography.hazmat.primitives import serialization

    Ed25519PrivateKey, _ = _require_crypto()
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex.strip()))
    return sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def sign_wasm(wasm: bytes, private_hex: str) -> bytes:
    """Return the signed-push payload: signature(64) || wasm."""
    Ed25519PrivateKey, _ = _require_crypto()
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex.strip()))
    signature = sk.sign(wasm)
    return signature + wasm


def load_signing_key(explicit: str | None = None) -> str | None:
    """Resolve a signing key: explicit arg, then MILO_SIGNING_KEY env, then
    ~/.milo/signing.key. Returns the private hex or None if unavailable."""
    if explicit:
        return explicit.strip()
    env = os.environ.get("MILO_SIGNING_KEY")
    if env:
        return env.strip()
    path = os.path.expanduser("~/.milo/signing.key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return None
