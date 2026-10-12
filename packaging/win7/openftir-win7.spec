# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows 7 build of OpenFTIR.

Same app as packaging/openftir.spec, frozen with Python 3.8 and Qt 5
(PySide2): the last versions that run on Windows 7. Three executables share
one set of libraries:

* ``OpenFTIR``           - the desktop app
* ``openftir-cli``       - the command-line tool
* ``openftir-selftest``  - checks each part of the app works on this PC

Build with ``python packaging/win7/build.py`` on Windows.
"""

import glob
import os
import struct

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

HERE = SPECPATH  # noqa: F821 - injected by PyInstaller
PACKAGING = os.path.join(HERE, "..")

hidden = collect_submodules("openftir")
hidden += ["usb.backend.libusb1", "libusb_package"]
# The app imports Qt as PySide6, which resolves to shim/ at run time; tell
# PyInstaller which real Qt 5 modules that needs.
hidden += ["PySide2.QtCore", "PySide2.QtGui", "PySide2.QtWidgets", "PySide2.QtSvg"]


def _ucrt_dlls():
    """The Universal C Runtime, bundled next to the app.

    Python 3.8 needs it and Windows 7 only has it if update KB2999226 was
    installed. Microsoft supports shipping these DLLs with the app.
    """
    arch = "x64" if struct.calcsize("P") == 8 else "x86"
    kits = os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Windows Kits", "10", "Redist")
    for pattern in (os.path.join(kits, "10.*", "ucrt", "DLLs", arch), os.path.join(kits, "ucrt", "DLLs", arch)):
        for folder in sorted(glob.glob(pattern), reverse=True):
            dlls = glob.glob(os.path.join(folder, "*.dll"))
            if dlls:
                return [(dll, ".") for dll in dlls]
    raise SystemExit(f"Universal C Runtime redistributable ({arch}) not found under {kits}; install the Windows 10 SDK")


shim_files = [(path, os.path.join("win7shim", "PySide6")) for path in glob.glob(os.path.join(HERE, "shim", "PySide6", "*.py"))]

common = dict(
    pathex=[os.path.join(PACKAGING, "..", "src")],
    hiddenimports=hidden,
    binaries=collect_dynamic_libs("libusb_package") + _ucrt_dlls(),
    datas=collect_data_files("libusb_package") + collect_data_files("openftir") + shim_files,
    runtime_hooks=[os.path.join(HERE, "rthook_shim.py"), os.path.join(HERE, "py38_compat.py")],
    excludes=[
        "PySide6",  # the stand-in in win7shim/ is loaded from disk, not frozen
        "PyQt5",
        "PyQt6",
        "tkinter",
        "matplotlib.backends.backend_tkagg",
        "matplotlib.backends.backend_qtagg",
        "matplotlib.backends.backend_qt5agg",
        "matplotlib.backends.backend_qtcairo",
        "matplotlib.backends.backend_webagg",
        "matplotlib.backends.backend_nbagg",
        "IPython",
        "pytest",
    ],
    noarchive=False,
)

ICON = os.path.join(PACKAGING, "icons", "openftir.png")

analyses = {
    "OpenFTIR": (Analysis([os.path.join(PACKAGING, "launcher.py")], **common), False),  # noqa: F821
    "openftir-cli": (Analysis([os.path.join(PACKAGING, "cli_launcher.py")], **common), True),  # noqa: F821
    "openftir-selftest": (Analysis([os.path.join(HERE, "selftest.py")], **common), True),  # noqa: F821
}

exes = [
    EXE(  # noqa: F821
        PYZ(a.pure),  # noqa: F821
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        icon=ICON,
        upx=False,
    )
    for name, (a, console) in analyses.items()
]

coll = COLLECT(  # noqa: F821
    *exes,
    *[a.binaries for a, _ in analyses.values()],
    *[a.datas for a, _ in analyses.values()],
    upx=False,
    name="OpenFTIR",
)
