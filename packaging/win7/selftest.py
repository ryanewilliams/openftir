"""openftir-selftest.exe: checks the Windows 7 build works on this PC.

Double-click it if OpenFTIR won't open. It runs each part of the app in turn
and prints what failed, then waits for Enter so the window stays open.
"""

import os
import platform
import sys
import tempfile
import traceback

results = []


def step(name):
    def run(fn):
        try:
            detail = fn()
        except Exception:  # noqa: BLE001 - report every failure, keep going
            results.append(False)
            print(f"FAIL  {name}")
            traceback.print_exc(file=sys.stdout)
        else:
            results.append(True)
            print(f"ok    {name}" + (f": {detail}" if detail else ""))
        return fn
    return run


def main():
    print(f"{platform.platform()} ({platform.architecture()[0]}), Python {platform.python_version()}")
    out_dir = tempfile.mkdtemp(prefix="openftir-selftest-")
    state = {}

    @step("Qt")
    def _():
        import PySide2
        from PySide6.QtWidgets import QApplication

        from openftir.gui.app import apply_light_theme, app_icon

        app = QApplication.instance() or QApplication(sys.argv)
        apply_light_theme(app)
        app.setWindowIcon(app_icon())
        state["app"] = app
        return f"Qt {PySide2.__version__}, platform {app.platformName()}"

    @step("Splash screen")
    def _():
        from openftir.gui.splash import render_splash

        assert not render_splash("Self-test", 0.5).isNull()

    @step("Instrument drivers")
    def _():
        from openftir.instruments import registry

        drivers = registry.available_drivers()
        assert {"simulated", "summit"} <= set(drivers), sorted(drivers)
        return ", ".join(sorted(drivers))

    @step("USB library")
    def _():
        import libusb_package
        import usb.backend.libusb1
        import usb.core

        backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
        assert backend is not None, "libusb-1.0.dll did not load"
        return f"{len(list(usb.core.find(find_all=True, backend=backend)))} USB devices"

    @step("Simulated collection")
    def _():
        from openftir.instruments import registry
        from openftir.instruments.base import CollectParams
        from openftir.processing import ratio
        from openftir.spectrum import YUnit

        with registry.create("simulated") as instrument:
            background_params = CollectParams(scans=2, resolution=4.0)
            background_params.extra["purpose"] = "background"
            background = instrument.collect_single_beam(background_params)
            sample = instrument.collect_single_beam(CollectParams(scans=2, resolution=4.0))
        state["spectrum"] = ratio(sample, background, to=YUnit.TRANSMITTANCE)
        return f"{len(state['spectrum'])} points"

    @step("Main window")
    def _():
        from openftir.gui.main_window import MainWindow

        window = MainWindow()
        window.show()
        for mode in ("advanced", "simple"):
            window._apply_mode(mode)
            state["app"].processEvents()
        state["window"] = window
        return f"{window.width()}x{window.height()}"

    @step("Image export")
    def _():
        path = os.path.join(out_dir, "plot.png")
        state["window"].plot.render_export(path)
        assert os.path.getsize(path) > 0

    @step("PDF report")
    def _():
        from openftir.report.pdf import export_report_pdf

        path = os.path.join(out_dir, "report.pdf")
        export_report_pdf(path, state["spectrum"], title="Self-test")
        assert os.path.getsize(path) > 0

    @step("Save and open files")
    def _():
        from openftir import io as ftio

        for ext in (".jdx", ".csv"):
            path = os.path.join(out_dir, "spectrum" + ext)
            ftio.save([state["spectrum"]], path)
            assert len(ftio.load(path)) == 1

    if "window" in state:
        state["window"].close()

    failed = results.count(False)
    print()
    print("All checks passed." if not failed else f"{failed} check(s) failed. Send a photo of this window.")
    if not os.environ.get("CI") and sys.stdin is not None and sys.stdin.isatty():
        input("Press Enter to close.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
