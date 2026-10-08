#!/usr/bin/env python3
"""Thin CLI wrapper around :mod:`openftir.probe`.

The actual probe logic (read-only USB/network discovery for the Thermo Nicolet
Summit / Summit X, see the module docstring in ``openftir.probe`` for full
details) now lives in the importable ``openftir.probe`` module so the GUI app
and the ``openftir probe`` CLI subcommand can call it directly. This script is
kept only as a convenient standalone entry point, e.g. for running against a
dev checkout without installing the package.

Usage:
    python probe_instrument.py
    python probe_instrument.py --scan-host 169.254.1.1
    python probe_instrument.py --output-dir /tmp/reports
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this script directly from a source checkout without having
# installed the package first (e.g. `python tools/probe_instrument.py`).
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from openftir.probe import main

if __name__ == "__main__":
    sys.exit(main())
