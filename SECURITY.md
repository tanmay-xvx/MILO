# MILO Security Model

MILO lets a language model generate and load firmware onto physical devices.
That is only safe because the trust boundary is drawn *below* the LLM: nothing
the model produces is trusted, and every module crosses four independent gates
before a single instruction executes on hardware.

## Threat model

**Trusted:** the fleet operator and their signing key; the receiver firmware
image; the physical link (USB) or an authenticated network path.

**Untrusted:** the LLM and everything it emits; any wasm module arriving over
the wire; the *content* of a MILO-Link frame until it has been validated.

**In scope:** a malicious or buggy module attempting to (a) exhaust device
resources, (b) reach peripherals it was not granted, (c) escape the sandbox
into firmware memory, (d) run code the operator never authorized, or (e) wedge
the receiver with a malformed frame.

**Out of scope (documented, not yet solved):** confidentiality of the link
(frames are not encrypted — run over USB or a trusted network/VPN; TLS for the
TCP transport is future work); rollback protection for signed modules; physical
attacks (JTAG, glash-glitch, chip decap).

## The four gates

Every code-loading opcode (`OP_BYTECODE_PUSH`, `OP_HOT_SWAP`, and their signed
variants `OP_SIGNED_PUSH` 0x0A / `OP_SIGNED_SWAP` 0x0B) passes through:

1. **Frame bound.** A payload length is checked against `MAX_FRAME_LEN`
   (512 KB) *before* any buffer is allocated from it. A hostile 4 GB length
   prefix is rejected instead of triggering a giant allocation. Enforced in all
   three readers (blocking stdio, TCP server, Pico USB).

2. **Signature (optional, operator-controlled).** With a trusted Ed25519 key
   provisioned, a signed payload is `signature(64) || wasm`; the receiver
   verifies it against the key. Under `require_signed`, unsigned pushes are
   refused outright. Verification is `ed25519-dalek`, constant-time, no_std,
   and runs on the RISC-V/ARM targets as well as the host.

3. **Import whitelist.** The module's import section is parsed and every
   imported function checked against the 12-syscall Alphabet. A module asking
   for anything else (an unknown `env` import, a WASI call) is rejected with a
   named error and never instantiated. Enforced on every entry point.

4. **Sandbox + fuel.** The module runs in wasmi with a hard 64 KB memory / 8 KB
   stack cap and per-instruction fuel metering. It cannot form a pointer into
   firmware memory; an infinite loop exhausts fuel and returns a clean error.
   The worst outcome is a failed episode on that one device.

## Capability enforcement at the peripheral

The hardware manifest advertises exactly which pins, channels and buses exist.
A syscall targeting an id the manifest never advertised is a **safe no-op that
is logged** — `denied: pwm_set 7 not in manifest` appears in the device logs
and therefore in the host's result frame. The model gets explicit feedback
instead of silent nothing, and an operator can audit every out-of-spec attempt.

## Provisioning signed firmware

```bash
# 1. Generate an operator keypair (private key stored 0600).
python cli.py keygen
#   → prints the public key

# 2. Provision an emulated device (or bake into a hardware image at build time).
export MILO_TRUSTED_KEY=<public-hex>
export MILO_REQUIRE_SIGNED=1
./milo-receiver --listen 9400 --profile drone     # signed-only

#    Hardware: bake the key in at build time
MILO_TRUSTED_KEY=<public-hex> cargo build --release \
  --target riscv32imc-unknown-none-elf --features esp32c3 --no-default-features

# 3. Host signs automatically when MILO_SIGNING_KEY or ~/.milo/signing.key is set.
```

Verified end-to-end in `host/tests/test_signing.py`: an unsigned push is
rejected under `require_signed`, a validly signed module runs, and a
single-bit-tampered module is rejected.

## What is enforced where

| Guarantee | Enforced in | Test |
|---|---|---|
| Frame length bound | `engine/link.rs`, `transport/mod.rs`, Pico loop | `test_frame_cap` (host) |
| Signature verification | `engine/signing.rs` + `authorize_module` | `signing::tests`, `test_signing.py` |
| Signed-only policy | `SecurityPolicy` in `lib.rs` | `test_signing.py` |
| Import whitelist | `engine/validation.rs` | `test_sim_fleet.py`, `integration.rs` |
| Fuel metering | wasmi config in `MiloRuntime` | `test_fuel_exhaustion` |
| Capability denial logging | `hal/adapter.rs` | `unregistered_syscall_is_denied_and_logged` |

## Reporting

This is a research project; there is no formal disclosure process yet. Open a
GitHub issue for security-relevant findings, or mark it privately if the
repository has private reporting enabled.
