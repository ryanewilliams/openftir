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

from PyInstaller.utils.hooks import collect_submodules

HERE = SPECPATH  # noqa: F821 - injected by PyInstaller
# Drivers are imported lazily by name from the registry, so PyInstaller's
# import scanner cannot see them; bundle every openftir submodule explicitly.
hidden = collect_submodules("openftir")

common = dict(
    pathex=[os.path.join(HERE, "..", "src")],
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
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
        icon=None,
        bundle_identifier="org.openftir.OpenFTIR",
        info_plist={
            "CFBundleName": "OpenFTIR",
            "CFBundleDisplayName": "OpenFTIR",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
        },
    )
