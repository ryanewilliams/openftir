"""Build the Windows 7 version of OpenFTIR.

Usage, on Windows, with 32- or 64-bit Python 3.8 (the bitness of the
Python decides the bitness of the app):

    py -3.8 packaging/win7/build.py

Writes dist/OpenFTIR-win7-64bit.zip or dist/OpenFTIR-win7-32bit.zip. The
regular build (packaging/build.py) is unaffected.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build" / "win7"


def run(*cmd: object) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def main() -> int:
    if sys.platform != "win32" or sys.version_info[:2] != (3, 8):
        sys.exit("Run this on Windows with Python 3.8 (the last Python that supports Windows 7).")

    run(sys.executable, "-m", "pip", "install", "-r", HERE / "requirements-build.txt")
    # OpenFTIR itself asks for Python 3.10+; the Windows 7 build covers the
    # difference with shim/ and py38_compat.py.
    run(sys.executable, "-m", "pip", "install", "--no-deps", "--ignore-requires-python", ".")

    shutil.rmtree(DIST / "OpenFTIR", ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", WORK, HERE / "openftir-win7.spec")

    bits = struct.calcsize("P") * 8
    archive = shutil.make_archive(str(DIST / f"OpenFTIR-win7-{bits}bit"), "zip", DIST, "OpenFTIR")
    print(f"\nBuilt {archive}\n  unzip, then run OpenFTIR\\OpenFTIR.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
