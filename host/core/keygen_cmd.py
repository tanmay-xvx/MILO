"""`milo keygen` — generate an Ed25519 signing keypair for signed bytecode.

The private seed is written to ~/.milo/signing.key (0600); the public key is
printed for provisioning receivers via MILO_TRUSTED_KEY.
"""

import argparse
import os
import stat


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("keygen", help="Generate an Ed25519 code-signing keypair")
    p.add_argument(
        "--out",
        default=os.path.expanduser("~/.milo/signing.key"),
        help="Where to write the private key (default: ~/.milo/signing.key)",
    )
    p.add_argument("--force", action="store_true", help="Overwrite an existing key")
    p.add_argument(
        "--print-public",
        metavar="KEYFILE",
        help="Print the public key for an existing private key and exit",
    )
    p.set_defaults(func=run)
    return p


def run(args: argparse.Namespace) -> int:
    from core.signing import generate_keypair, public_key_hex

    if args.print_public:
        with open(args.print_public) as f:
            priv = f.read().strip()
        print(public_key_hex(priv))
        return 0

    if os.path.exists(args.out) and not args.force:
        print(f"Refusing to overwrite existing key at {args.out} (use --force).")
        return 1

    priv, pub = generate_keypair()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(priv + "\n")
    os.chmod(args.out, stat.S_IRUSR | stat.S_IWUSR)  # 0600

    print(f"  Private key written to {args.out} (mode 0600)")
    print(f"  Public key (provision receivers with this):\n\n    {pub}\n")
    print("  Provision a device:")
    print(f"    export MILO_TRUSTED_KEY={pub}")
    print("    export MILO_REQUIRE_SIGNED=1   # refuse unsigned modules")
    print("  Sign from the host by setting MILO_SIGNING_KEY or ~/.milo/signing.key.")
    return 0
