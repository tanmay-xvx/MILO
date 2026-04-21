"""LIAL CLI subcommands.

Each file in this package is a subcommand registered by lial_host.py's
argparse subparser dispatcher. Keeping them as separate modules keeps
lial_host.py slim and makes each subcommand trivially unit-testable.
"""
