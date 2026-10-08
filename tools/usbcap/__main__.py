"""Entry point for ``python -m tools.usbcap ...`` run from the repo root."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
