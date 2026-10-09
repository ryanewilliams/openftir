"""Build a standalone OpenFTIR app for the current OS.

Usage, from the repository root (Windows, macOS or Linux):

    python packaging/build.py

Creates ``.build-venv/`` (unless ``--no-venv``), installs OpenFTIR and
PyInstaller into it, runs PyInstaller and writes a zip/tarball to ``dist/``:

* Windows: dist/OpenFTIR-windows.zip   (run OpenFTIR\\OpenFTIR.exe)
* macOS:   dist/OpenFTIR-macos.zip     (contains OpenFTIR.app)
* Linux:   dist/OpenFTIR-linux.tar.gz  (run OpenFTIR/OpenFTIR)

PyInstaller cannot cross-compile, so each OS's build must run on that OS.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import tarfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WORK = ROOT / "build"


def run(*cmd: object) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-venv", action="store_true", help="use the current Python instead of .build-venv")
    args = parser.parse_args()

    if sys.version_info < (3, 10):
        sys.exit("Python 3.10 or newer is required (python.org installer recommended).")

    if args.no_venv:
        python = Path(sys.executable)
    else:
        venv_dir = ROOT / ".build-venv"
        if not venv_python(venv_dir).exists():
            print(f"creating {venv_dir}")
            venv.create(venv_dir, with_pip=True)
        python = venv_python(venv_dir)

    run(python, "-m", "pip", "install", "--upgrade", "pip")
    run(python, "-m", "pip", "install", ".[gui]", "pyinstaller>=6.3", "pillow")  # pillow: icon conversion

    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(WORK, ignore_errors=True)
    run(python, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", WORK, ROOT / "packaging" / "openftir.spec")

    system = platform.system()
    if system == "Windows":
        archive = shutil.make_archive(str(DIST / "OpenFTIR-windows"), "zip", DIST, "OpenFTIR")
        hint = r"unzip, then run OpenFTIR\OpenFTIR.exe"
    elif system == "Darwin":
        # ditto preserves the symlinks and metadata inside the .app bundle.
        archive = str(DIST / "OpenFTIR-macos.zip")
        run("ditto", "-c", "-k", "--keepParent", DIST / "OpenFTIR.app", archive)
        hint = "unzip, then right-click OpenFTIR.app > Open the first time (unsigned app)"
    else:
        archive = str(DIST / "OpenFTIR-linux.tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(DIST / "OpenFTIR", arcname="OpenFTIR")
        hint = "extract, then run OpenFTIR/OpenFTIR"

    print(f"\nBuilt {archive}\n  {hint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
