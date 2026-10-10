"""openftir.report.export_report_pdf: PDF structure, pagination and content."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime

import numpy as np
import pytest

from openftir.report import export_report_pdf
from openftir.spectrum import Spectrum, XUnit, YUnit

_PDFTOTEXT = shutil.which("pdftotext")


def _transmittance_spectrum(title: str = "Demo Sample") -> Spectrum:
    x = np.linspace(4000, 600, 3000)
    y = (
        100.0
        - 55.0 * np.exp(-((x - 3300.0) ** 2) / (2 * 60.0**2))
        - 70.0 * np.exp(-((x - 1700.0) ** 2) / (2 * 25.0**2))
        - 15.0 * np.exp(-((x - 1100.0) ** 2) / (2 * 40.0**2))
    )
    return Spectrum(
        x=x,
        y=y,
        x_unit=XUnit.WAVENUMBER,
        y_unit=YUnit.TRANSMITTANCE,
        title=title,
        meta={
            "driver": "summit",
            "scans": 16,
            "resolution": 4.0,
            "serial": "BGA1234567",
            "background_collected": "Thursday, October 1, 2026 3:40:19 PM (GMT-07:00)",
        },
        acquired=datetime(2026, 10, 1, 22, 48, 46),
    )


def _count_page_objects(pdf_bytes: bytes) -> int:
    # "/Type /Pages" (the page tree root) is a superset match of
    # "/Type /Page" -- exclude it so only actual leaf page objects count.
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes))


def test_export_creates_a_valid_single_page_pdf(tmp_path):
    path = tmp_path / "report.pdf"
    export_report_pdf(str(path), _transmittance_spectrum(), manual_peaks=[1050.0])

    data = path.read_bytes()
    assert data.startswith(b"%PDF")
    assert _count_page_objects(data) == 1


def test_report_contains_sample_name_and_peak_numbers_as_text(tmp_path):
    if _PDFTOTEXT is None:
        pytest.skip("pdftotext not on PATH")

    path = tmp_path / "report.pdf"
    export_report_pdf(str(path), _transmittance_spectrum("Polystyrene film A"), manual_peaks=[1050.0])

    text = subprocess.run(
        [_PDFTOTEXT, str(path), "-"], check=True, capture_output=True, text=True
    ).stdout
    assert "Polystyrene film A" in text
    assert "Infrared spectrum report" in text
    assert "Table 1. Principal absorption bands" in text
    # The manually-picked peak and at least one auto-labelled band should
    # both show up as plain extractable numbers (fonttype 42 embedding).
    assert "1050" in text
    assert "1700" in text
    assert "(picked)" in text


def test_a4_paper_produces_a_valid_pdf(tmp_path):
    path = tmp_path / "report_a4.pdf"
    export_report_pdf(str(path), _transmittance_spectrum(), paper="a4")
    data = path.read_bytes()
    assert data.startswith(b"%PDF")
    assert _count_page_objects(data) == 1


def test_unknown_paper_size_raises():
    with pytest.raises(ValueError):
        export_report_pdf("/dev/null", _transmittance_spectrum(), paper="legal")


def test_long_manual_peak_list_overflows_to_a_second_page(tmp_path):
    # A flat trace with nothing for auto_label_peaks to find: the row count
    # is then exactly the 40 manual peaks, which is known to need a second
    # (but not third) page at this layout's row capacity.
    x = np.linspace(4000, 600, 3000)
    y = np.full_like(x, 100.0)
    spectrum = Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.TRANSMITTANCE, title="Flat")
    manual_peaks = list(np.linspace(3900.0, 650.0, 40))

    path = tmp_path / "report_overflow.pdf"
    export_report_pdf(str(path), spectrum, manual_peaks=manual_peaks)

    data = path.read_bytes()
    assert _count_page_objects(data) == 2
    # No page numbers anywhere in the generated pages, per the approved design.
    if _PDFTOTEXT is not None:
        text = subprocess.run(
            [_PDFTOTEXT, str(path), "-"], check=True, capture_output=True, text=True
        ).stdout
        assert "Page 1 of" not in text
        assert "(continued)" in text


def test_absorbance_spectrum_uses_absorbance_column_header(tmp_path):
    x = np.linspace(4000, 600, 2000)
    y = 0.05 + 1.2 * np.exp(-((x - 1700.0) ** 2) / (2 * 25.0**2))
    spectrum = Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.ABSORBANCE, title="Abs Sample")

    path = tmp_path / "report_abs.pdf"
    export_report_pdf(str(path), spectrum)

    if _PDFTOTEXT is None:
        pytest.skip("pdftotext not on PATH")
    text = subprocess.run(
        [_PDFTOTEXT, str(path), "-"], check=True, capture_output=True, text=True
    ).stdout
    assert "Absorbance" in text
    assert "Transmittance" not in text


def test_single_beam_spectrum_is_rejected():
    x = np.linspace(4000, 600, 50)
    y = np.ones_like(x)
    spectrum = Spectrum(x=x, y=y, x_unit=XUnit.WAVENUMBER, y_unit=YUnit.SINGLE_BEAM, title="raw")
    with pytest.raises(ValueError):
        export_report_pdf("/dev/null", spectrum)


def test_manual_peak_wins_over_a_nearby_auto_peak(tmp_path):
    """A manual peak within 4 cm-1 of an auto-labelled one de-dups to a
    single (manual) row -- same rule as MainWindow._refresh_peaks_panel."""
    from openftir.report.pdf import _merged_peak_rows

    spectrum = _transmittance_spectrum()
    rows = _merged_peak_rows(spectrum, [1699.0], auto_peak_params=None)
    near_1700 = [r for r in rows if abs(r["position"] - 1700.0) <= 4.0]
    assert len(near_1700) == 1
    assert near_1700[0]["manual"] is True


def test_values_below_0_05_print_as_zero():
    from openftir.report.pdf import _fmt_value

    assert _fmt_value(0.049) == "0.0"
    assert _fmt_value(-0.2) == "0.0"
    assert _fmt_value(0.06) == "0.1"
    assert _fmt_value(4.2) == "4.2"
