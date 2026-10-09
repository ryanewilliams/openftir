import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from openftir.gui.app import app_icon

REPO = Path(__file__).resolve().parent.parent


def test_icon_assets_exist():
    assert (REPO / "packaging" / "icons" / "openftir.svg").is_file()
    assert (REPO / "packaging" / "icons" / "openftir.png").is_file()


def test_app_icon_loads(qtbot):
    icon = app_icon()
    assert not icon.isNull()
    assert icon.availableSizes()
