# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OpenFTIR.

Builds a one-folder bundle containing two executables that share one set of
libraries:

* ``OpenFTIR``      - the windowed desktop app
* ``openftir-cli``  - the console command-line tool (``openftir-cli --help``)

On macOS the GUI is additionally wrapped in ``OpenFTIR.app``.

Build from the repository root with ``python packaging/build.py`` (preferred)
or ``pyinstaller packaging/openftir.spec``.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files, collect_submodules

HERE = SPECPATH  # noqa: F821 - injected by PyInstaller
# Drivers are imported lazily by name from the registry, so PyInstaller's
# import scanner cannot see them; bundle every openftir submodule explicitly.
hidden = collect_submodules("openftir")
# usb.backend.libusb1 is loaded dynamically by pyusb; libusb_package ships the
# prebuilt libusb-1.0 shared library the probe's USB enumeration needs, since a
# frozen app can't rely on the target machine having a system libusb installed.
hidden += ["usb.backend.libusb1", "libusb_package"]

libusb_binaries = collect_dynamic_libs("libusb_package")
libusb_datas = collect_data_files("libusb_package")
# Package data such as the window icon (src/openftir/gui/resources/).
openftir_datas = collect_data_files("openftir")
# PyInstaller converts this PNG to .ico (Windows) / .icns (macOS); needs Pillow.
ICON = os.path.join(HERE, "icons", "openftir.png")

common = dict(
    pathex=[os.path.join(HERE, "..", "src")],
    hiddenimports=hidden,
    binaries=libusb_binaries,
    datas=libusb_datas + openftir_datas,
    # matplotlib itself is needed now (openftir.report's PDF export, backend
    # "pdf" only), but not its GUI backends: the app embeds matplotlib's
    # Figure/PdfPages directly and never shows an interactive matplotlib
    # window, so every backend other than "pdf"/"agg" (and the heavy
    # notebook/IPython stack pulled in by lazy-imports) is dead weight.
    excludes=[
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

gui_a = Analysis([os.path.join(HERE, "launcher.py")], **common)  # noqa: F821
cli_a = Analysis([os.path.join(HERE, "cli_launcher.py")], **common)  # noqa: F821

gui_pyz = PYZ(gui_a.pure)  # noqa: F821
cli_pyz = PYZ(cli_a.pure)  # noqa: F821

gui_exe = EXE(  # noqa: F821
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="OpenFTIR",
    console=False,
    icon=ICON,
    upx=False,
    argv_emulation=False,
)

cli_exe = EXE(  # noqa: F821
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="openftir-cli",
    console=True,
    icon=ICON,
    upx=False,
)

coll = COLLECT(  # noqa: F821
    gui_exe,
    cli_exe,
    gui_a.binaries + cli_a.binaries,
    gui_a.datas + cli_a.datas,
    upx=False,
    name="OpenFTIR",
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="OpenFTIR.app",
        icon=ICON,
        bundle_identifier="org.openftir.OpenFTIR",
        info_plist={
            "CFBundleName": "OpenFTIR",
            "CFBundleDisplayName": "OpenFTIR",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            # PyInstaller marks the bundle background-only when its last
            # executable is a console one (openftir-cli). A background-only
            # app never becomes the active app, so the keyboard never
            # reaches it: typing anywhere just beeps.
            "LSBackgroundOnly": False,
        },
    )
