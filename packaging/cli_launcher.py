"""PyInstaller entry point for the console tool."""

import sys

from openftir.cli import main

if __name__ == "__main__":
    sys.exit(main())
