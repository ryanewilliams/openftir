"""Non-native file dialogs: on an unsigned macOS build the native save/open
panel never gets keyboard focus, so every dialog in the GUI must go through
``openftir.gui.file_dialogs`` and use ``QFileDialog.DontUseNativeDialog``.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from openftir.gui import file_dialogs


def test_sanitize_filename_strips_illegal_characters():
    assert file_dialogs.sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_filename_empty_or_dots_falls_back():
    assert file_dialogs.sanitize_filename("") == "Untitled"
    assert file_dialogs.sanitize_filename("...") == "Untitled"


def test_sanitize_filename_keeps_normal_names():
    assert file_dialogs.sanitize_filename("Sample 1") == "Sample 1"


def test_filter_extension_extracts_first_glob():
    assert file_dialogs.filter_extension("JCAMP-DX spectrum (*.jdx *.dx)") == ".jdx"
    assert file_dialogs.filter_extension("All Files (*)") == ""


def test_build_filter_string_without_all_supported():
    filters = {".jdx": "JCAMP-DX spectrum", ".csv": "Comma separated values"}
    result = file_dialogs.build_filter_string(filters, include_all_supported=False)
    assert "All Supported" not in result
    assert "JCAMP-DX spectrum (*.jdx)" in result
    assert result.endswith("All Files (*)")


def test_build_filter_string_with_all_supported():
    filters = {".jdx": "JCAMP-DX spectrum", ".csv": "Comma separated values"}
    result = file_dialogs.build_filter_string(filters, include_all_supported=True)
    assert result.startswith("All Supported (*.jdx *.csv)")


def test_finalize_save_path_appends_missing_extension():
    path = file_dialogs.finalize_save_path("/tmp/myfile", "JCAMP-DX spectrum (*.jdx)")
    assert path == "/tmp/myfile.jdx"


def test_finalize_save_path_leaves_existing_extension_alone():
    path = file_dialogs.finalize_save_path("/tmp/myfile.csv", "JCAMP-DX spectrum (*.jdx)")
    assert path == "/tmp/myfile.csv"


def test_build_save_dialog_uses_non_native_widget(qtbot):
    dialog = file_dialogs.build_save_dialog(None, "Save Thing", "Sample 1.jdx", "JCAMP (*.jdx);;CSV (*.csv)")
    qtbot.addWidget(dialog)
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog) is True
    assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave
    assert Path(dialog.selectedFiles()[0]).name == "Sample 1.jdx"


def test_build_open_dialog_uses_non_native_widget(qtbot):
    dialog = file_dialogs.build_open_dialog(None, "Open Thing", "All Files (*)")
    qtbot.addWidget(dialog)
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog) is True
    assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptOpen


def test_build_directory_dialog_uses_non_native_widget(qtbot):
    dialog = file_dialogs.build_directory_dialog(None, "Pick a folder")
    qtbot.addWidget(dialog)
    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog) is True


def test_save_dialog_filename_extension_tracks_selected_filter(qtbot):
    dialog = file_dialogs.build_save_dialog(None, "Save Thing", "Sample 1.jdx", "JCAMP (*.jdx);;CSV (*.csv)")
    qtbot.addWidget(dialog)

    dialog.selectNameFilter("CSV (*.csv)")

    selected = Path(dialog.selectedFiles()[0])
    assert selected.suffix == ".csv"
    assert selected.stem == "Sample 1"


def test_default_directory_prefers_last_used(monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        file_dialogs, "_settings", lambda: QSettings("OpenFTIR-Test", "OpenFTIR-Test")
    )
    settings = QSettings("OpenFTIR-Test", "OpenFTIR-Test")
    settings.setValue(file_dialogs._LAST_DIR_KEY, str(tmp_path))

    assert file_dialogs.default_directory() == str(tmp_path)

    settings.remove(file_dialogs._LAST_DIR_KEY)


def test_default_directory_falls_back_to_documents_or_home(monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        file_dialogs, "_settings", lambda: QSettings("OpenFTIR-Test", "OpenFTIR-Test")
    )
    settings = QSettings("OpenFTIR-Test", "OpenFTIR-Test")
    settings.remove(file_dialogs._LAST_DIR_KEY)

    result = file_dialogs.default_directory()
    documents = Path.home() / "Documents"
    expected = str(documents) if documents.is_dir() else str(Path.home())
    assert result == expected
