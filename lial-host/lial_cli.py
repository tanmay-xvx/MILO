#!/usr/bin/env python3
"""LIAL CLI — unified command-line interface.

Usage:
    lial init [--port PORT] [-y] [--dry-run]
    lial download [--board BOARD]
    lial run [--port PORT]
"""

import argparse
import sys

from lial_commands import init, download


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lial",
        description="LIAL — LLM IoT Abstraction Layer",
    )
    subparsers = parser.add_subparsers(dest="command")
    init.add_parser(subparsers)
    download.add_parser(subparsers)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
