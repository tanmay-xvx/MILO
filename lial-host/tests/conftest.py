"""Shared pytest configuration.

Adds the lial-host directory to sys.path so tests can `import board_registry`
etc. without having to install the package.
"""

import os
import sys

HOST_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOST_DIR not in sys.path:
    sys.path.insert(0, HOST_DIR)
