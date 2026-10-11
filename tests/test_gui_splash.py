import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openftir.gui.splash import HEIGHT, WIDTH, SplashScreen, render_splash


def test_splash_renders_at_logical_size_and_hidpi(qtbot):
    pixmap = render_splash("Loading instrument drivers…", 0.5)
    assert (pixmap.width(), pixmap.height()) == (WIDTH, HEIGHT)
    hidpi = render_splash("Loading…", 0.5, device_pixel_ratio=2.0)
    assert (hidpi.width(), hidpi.height()) == (2 * WIDTH, 2 * HEIGHT)


def test_splash_advances(qtbot):
    splash = SplashScreen()
    qtbot.addWidget(splash)
    splash.show()
    splash.advance("Preparing workspace…", 0.8)
    assert splash.message == "Preparing workspace…"
    assert splash.progress == 0.8
    assert not splash.pixmap().isNull()


def test_splash_module_does_not_pull_in_heavy_libraries():
    # The splash must be quick to show: importing it may not import the
    # plotting/processing stack (that's loaded while it is on screen).
    import subprocess
    import sys

    code = (
        "import sys; import openftir.gui.splash; "
        "heavy = [m for m in ('scipy', 'pyqtgraph', 'matplotlib', 'openftir.processing') if m in sys.modules]; "
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
