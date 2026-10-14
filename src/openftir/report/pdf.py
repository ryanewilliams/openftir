"""Renders the "classic" infrared-spectrum PDF report.

Everything here uses ``matplotlib.figure.Figure`` directly together with
``matplotlib.backends.backend_pdf`` (``FigureCanvasPdf`` / ``PdfPages``) --
never ``matplotlib.pyplot`` (which keeps hidden global state) and never a Qt
backend. That makes this module safe to call from the GUI thread: it never
touches the app's own Qt/pyqtgraph plot, and never leaks state between
calls.

Layout is specified entirely in figure-fraction coordinates (0..1), so the
same drawing code produces a correctly scaled report on both Letter and A4
paper -- only the figure's physical size (inches) changes.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import matplotlib
import numpy as np
from matplotlib import font_manager
from matplotlib.backends.backend_pdf import FigureCanvasPdf, PdfPages
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

# Embed fonts as TrueType (42) rather than the default Type 3, so the
# Noto Serif text stays crisp and extractable (pdftotext etc.) in the PDF.
# This touches matplotlib's global rcParams, not pyplot's figure-management
# state -- no current-figure/axes global is used anywhere in this module.
matplotlib.rcParams["pdf.fonttype"] = 42

from openftir import __version__
from openftir.label_layout import LabelRequest, place_labels
from openftir.processing import auto_label_peaks
from openftir.spectrum import local_naive, Spectrum, YUnit

__all__ = ["export_report_pdf"]

# -- fonts --------------------------------------------------------------------------

_FONT_DIR = Path(__file__).parent / "fonts"
SERIF = "Noto Serif"

_FONT_FILES = (
    "NotoSerif-Regular.ttf",
    "NotoSerif-Italic.ttf",
    "NotoSerif-Bold.ttf",
    "NotoSerif-BoldItalic.ttf",
)

_fonts_registered = False


def _register_fonts() -> None:
    """Register the bundled Noto Serif faces with matplotlib's font manager.

    Idempotent -- safe to call more than once (e.g. if this module is
    reloaded) since ``addfont`` just re-adds the same file paths.
    """
    global _fonts_registered
    if _fonts_registered:
        return
    for name in _FONT_FILES:
        path = _FONT_DIR / name
        if path.is_file():
            font_manager.fontManager.addfont(str(path))
    _fonts_registered = True


_register_fonts()

# -- paper sizes ----------------------------------------------------------------------

_PAPER_SIZES_IN = {
    "letter": (8.5, 11.0),
    "a4": (8.27, 11.69),
}

# -- layout constants (figure-fraction units) ------------------------------------------

_LEFT = 0.08
_RIGHT = 0.92
_TITLE_Y = 0.955
_RULE1_Y = 0.935

_HEADER_ROW0_Y = 0.908
_HEADER_ROW_DY = 0.026

_PLOT_LEFT = 0.12
_PLOT_WIDTH = 0.80
_PLOT_GAP_BELOW_RULE = 0.028
_PLOT_HEIGHT = 0.29

_TABLE_LEFT = 0.08
_TABLE_RIGHT = 0.66
_TABLE_COLS = (0.08, 0.30, 0.48)

_TABLE_TITLE_GAP = 0.066
_TABLE_RULE_GAP = 0.018
_TABLE_HEADER_GAP = 0.018
_TABLE_RULE2_GAP = 0.013
_TABLE_FIRST_ROW_GAP = 0.017
_TABLE_ROW_DY = 0.021
_TABLE_MIN_ROW_Y = 0.11  # leave room for the closing rule + footer below it

_FOOTER_Y = 0.035

#: %T/%R-like units: auto-label/plot features point down from ~100.
_PERCENT_DOWN_UNITS = (YUnit.TRANSMITTANCE, YUnit.REFLECTANCE)

_VALUE_COLUMN_LABEL = {
    YUnit.TRANSMITTANCE: "Transmittance (%)",
    YUnit.REFLECTANCE: "Reflectance (%)",
    YUnit.ABSORBANCE: "Absorbance",
}
_AXIS_Y_LABEL = {
    YUnit.TRANSMITTANCE: "Transmittance (%)",
    YUnit.REFLECTANCE: "Reflectance (%)",
    YUnit.ABSORBANCE: "Absorbance",
}

#: an auto peak within this many cm-1 of a manual one is dropped (the manual
#: one wins) -- mirrors MainWindow._refresh_peaks_panel's own de-dup rule, so
#: the PDF table always agrees with the GUI's Peaks panel.
_MANUAL_DEDUP_CM = 4.0

_WEEKDAY_RE = re.compile(r"^[A-Za-z]+,\s*")
_TZ_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _fmt_value(value: float) -> str:
    """Values below 0.05 (in whatever the column's units are) print as
    ``0.0`` rather than a near-zero or slightly negative noise value."""
    return "0.0" if value < 0.05 else f"{value:.1f}"


def _format_dt(dt: datetime) -> str:
    dt = local_naive(dt)  # .spa times are UTC; show the reader's local time
    return f"{dt.day} {dt.strftime('%B %Y, %H:%M')}"


def _format_background_text(raw: Any) -> str:
    if isinstance(raw, datetime):
        return _format_dt(raw)
    text = str(raw).strip()
    cleaned = _TZ_RE.sub("", _WEEKDAY_RE.sub("", text))
    for fmt in ("%B %d, %Y %I:%M:%S %p", "%B %d, %Y %H:%M:%S", "%B %d, %Y"):
        try:
            return _format_dt(datetime.strptime(cleaned, fmt))
        except ValueError:
            continue
    return text  # unparsed, but still better than nothing


def _resolve_fields(spectrum: Spectrum, title: str | None, info: dict | None) -> dict:
    """Header field text, with "--" fallbacks -- see module docstring in
    ``openftir.report`` and the field-by-field rules in the export task."""
    meta = spectrum.meta or {}
    info = info or {}

    sample = (title or spectrum.title or "").strip() or "—"
    date = _format_dt(spectrum.acquired) if spectrum.acquired is not None else "—"

    driver = meta.get("driver")
    serial = str(meta.get("serial") or meta.get("instrument_serial_number") or "").strip()
    if driver == "summit" or serial:
        instrument = "Nicolet Summit X (ATR)"
    elif info.get("driver_display_name"):
        instrument = str(info["driver_display_name"])
    elif meta.get("instrument"):
        instrument = str(meta["instrument"])
    elif driver:
        instrument = str(driver).capitalize()
    else:
        instrument = "—"
    serial_text = serial or "—"

    resolution_cm = meta.get("resolution")
    if resolution_cm is None:
        raw_res = meta.get("resolution_cm-1")
        if raw_res is not None:
            try:
                resolution_cm = float(raw_res)
            except (TypeError, ValueError):
                resolution_cm = None
    scans = meta.get("scans") or info.get("scans")
    if resolution_cm is not None:
        res_text = f"{resolution_cm:g} cm⁻¹"
        if scans:
            res_text += f", {int(scans)} scan{'' if int(scans) == 1 else 's'}"
    else:
        res_text = "—"

    background_raw = meta.get("background_collected")
    if background_raw:
        background_text = _format_background_text(background_raw)
    else:
        bg_time = info.get("background_time")
        background_text = _format_dt(bg_time) if isinstance(bg_time, datetime) else "—"

    return {
        "sample": sample,
        "date": date,
        "instrument": instrument,
        "serial": serial_text,
        "resolution": res_text,
        "background": background_text,
    }


def _merged_peak_rows(
    spectrum: Spectrum, manual_peaks: Sequence[float], auto_peak_params: dict | None
) -> list[dict]:
    """Auto + manual peaks, merged and sorted by descending wavenumber.

    An auto peak within ``_MANUAL_DEDUP_CM`` of a manual one is dropped (the
    manual one wins) -- the same rule the GUI's Peaks panel uses, so the
    table always matches what the app itself shows for this spectrum.
    """
    manual_positions = sorted({float(p) for p in manual_peaks}, reverse=True)
    rows: list[dict] = []
    for pos in manual_positions:
        idx = int(np.argmin(np.abs(spectrum.x - pos))) if len(spectrum.x) else None
        value = float(spectrum.y[idx]) if idx is not None else 0.0
        rows.append({"position": pos, "value": value, "manual": True})

    try:
        auto_peaks = auto_label_peaks(spectrum, **(auto_peak_params or {}))
    except ValueError:
        auto_peaks = []
    for p in auto_peaks:
        if any(abs(p.position - m) <= _MANUAL_DEDUP_CM for m in manual_positions):
            continue
        rows.append({"position": p.position, "value": p.height, "manual": False})

    rows.sort(key=lambda r: r["position"], reverse=True)
    return rows


# -- drawing ----------------------------------------------------------------------------


def _draw_page_header(fig: Figure) -> None:
    fig.text(_LEFT, _TITLE_Y, "Infrared spectrum report", fontsize=19, family=SERIF, va="center")
    fig.text(_RIGHT, _TITLE_Y, "OpenFTIR", fontsize=9, color="#555555", ha="right", va="center", family=SERIF)
    fig.add_artist(Line2D([_LEFT, _RIGHT], [_RULE1_Y, _RULE1_Y], color="black", lw=1.2, transform=fig.transFigure))


def _draw_field_block(fig: Figure, fields: dict) -> float:
    """The 2x3 Sample/Date/Instrument/Serial/Resolution/Background block
    and its closing thin rule. Returns the rule's y (figure fraction), i.e.
    where the next section may start below it."""
    rows = [
        ("Sample", fields["sample"], "Date", fields["date"]),
        ("Instrument", fields["instrument"], "Serial", fields["serial"]),
        ("Resolution", fields["resolution"], "Background", fields["background"]),
    ]
    last_y = _HEADER_ROW0_Y
    for i, (a, b, c, d) in enumerate(rows):
        y = _HEADER_ROW0_Y - i * _HEADER_ROW_DY
        last_y = y
        fig.text(0.08, y, a, fontsize=8.5, color="#555555", family=SERIF, va="center")
        fig.text(0.21, y, b, fontsize=9.5, family=SERIF, va="center")
        fig.text(0.52, y, c, fontsize=8.5, color="#555555", family=SERIF, va="center")
        fig.text(0.65, y, d, fontsize=9.5, family=SERIF, va="center")
    rule_y = last_y - 0.017
    fig.add_artist(Line2D([_LEFT, _RIGHT], [rule_y, rule_y], color="black", lw=0.5, transform=fig.transFigure))
    return rule_y


def _draw_spectrum(fig: Figure, spectrum: Spectrum, rows: list[dict], plot_top: float):
    ax = fig.add_axes([_PLOT_LEFT, plot_top - _PLOT_HEIGHT, _PLOT_WIDTH, _PLOT_HEIGHT])

    x, y = spectrum.x, spectrum.y
    ax.plot(x, y, color="black", lw=0.8, solid_joinstyle="round")

    x_max, x_min = float(np.max(x)), float(np.min(x))
    pad_x = 0.01 * (x_max - x_min) if x_max > x_min else 1.0
    ax.set_xlim(x_max + pad_x, x_min - pad_x)

    down = spectrum.y_unit in _PERCENT_DOWN_UNITS
    if down:
        ax.set_ylim(-9, max(105.0, float(np.max(y)) + 3))
    else:
        y_span = float(np.ptp(y)) if len(y) else 1.0
        pad_y = 0.08 * y_span if y_span > 0 else 1.0
        ax.set_ylim(float(np.min(y)) - pad_y, float(np.max(y)) + pad_y)

    ax.set_xlabel("Wavenumber (cm⁻¹)", color="black", family=SERIF)
    ax.set_ylabel(_AXIS_Y_LABEL.get(spectrum.y_unit, "Value"), color="black", family=SERIF)
    ax.tick_params(colors="black", direction="out", length=3, width=0.6)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_linewidth(0.6)
        ax.spines[side].set_color("black")
    ax.grid(True, lw=0.5, color="#ececec")
    ax.set_axisbelow(True)

    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_family(SERIF)
        label.set_fontsize(8.5)

    _draw_peak_labels(fig, ax, spectrum, rows, down)
    return ax


def _draw_peak_labels(fig: Figure, ax, spectrum: Spectrum, rows: list[dict], down: bool) -> None:
    """Marker + wavenumber label for every peak in the table, placed clear of
    the trace and each other (see openftir.label_layout). Manual peaks go
    first, then auto peaks deepest dip / tallest band first."""
    fig.canvas.draw()
    fig_h = fig.bbox.height
    to_px = ax.transData.transform
    from_px = ax.transData.inverted().transform
    x_lo, x_hi = sorted(ax.get_xlim())

    order = sorted(
        (r for r in rows if x_lo <= r["position"] <= x_hi),
        key=lambda r: (0 if r["manual"] else 1, r["value"] if down else -r["value"]),
    )
    texts = []
    requests = []
    for r in order:
        text = ax.text(
            0, 0, f"{r['position']:.0f}", fontsize=7.5, color="black", family=SERIF,
            fontweight="bold" if r["manual"] else "normal", ha="center", va="top" if down else "bottom",
        )
        extent = text.get_window_extent()
        X, Y = to_px((r["position"], r["value"]))
        requests.append(LabelRequest(X, fig_h - Y, extent.width, extent.height, below=down, must_show=r["manual"]))
        texts.append(text)

    curve_px = to_px(np.column_stack([spectrum.x, spectrum.y]))
    curves = [(curve_px[:, 0], fig_h - curve_px[:, 1])]
    box = ax.bbox
    bounds = (box.x0 + 2, fig_h - box.y1 + 2, box.x1 - 2, fig_h - box.y0 - 2)
    placements = place_labels(requests, curves, bounds, marker_radius=3.0 * fig.dpi / 72)

    for r, req, text, placement in zip(order, requests, texts, placements):
        if placement is None:
            text.remove()
            continue
        pos, val = r["position"], r["value"]
        if r["manual"]:
            ax.plot(pos, val, "v" if down else "^", ms=5.0, color="black", mfc="black")
        else:
            ax.plot(pos, val, "o", ms=2.6, color="black")
        ax_, ay_ = placement.anchor_point(req.below)
        tx, ty = from_px((ax_, fig_h - ay_))
        text.set_position((tx, ty))
        if placement.leader:
            ax.plot([pos, tx], [val, ty], color="#666666", lw=0.4, zorder=1.5)


def _table_header_label(spectrum: Spectrum) -> str:
    return _VALUE_COLUMN_LABEL.get(spectrum.y_unit, "Value")


def _draw_table_header(fig: Figure, title_y: float, value_label: str, continued: bool) -> float:
    """Draws 'Table 1. Principal absorption bands[ (continued)]', the
    column headers and their rules. Returns the y of the first data row."""
    caption = "Table 1. Principal absorption bands"
    if continued:
        caption += " (continued)"
    fig.text(_TABLE_LEFT, title_y, caption, fontsize=10, family=SERIF, style="italic")

    rule1_y = title_y - _TABLE_RULE_GAP
    fig.add_artist(Line2D([_TABLE_LEFT, _TABLE_RIGHT], [rule1_y, rule1_y], color="black", lw=0.8, transform=fig.transFigure))

    header_y = rule1_y - _TABLE_HEADER_GAP
    headers = ["Wavenumber (cm⁻¹)", value_label, "Assignment"]
    for x, h in zip(_TABLE_COLS, headers):
        fig.text(x, header_y, h, fontsize=9, family=SERIF, va="center")

    rule2_y = header_y - _TABLE_RULE2_GAP
    fig.add_artist(Line2D([_TABLE_LEFT, _TABLE_RIGHT], [rule2_y, rule2_y], color="black", lw=0.5, transform=fig.transFigure))

    return rule2_y - _TABLE_FIRST_ROW_GAP


def _draw_table_rows(fig: Figure, rows: list[dict], first_row_y: float) -> float:
    """Draws ``rows`` starting at ``first_row_y``, then the closing rule.
    Returns the closing rule's y."""
    last_y = first_row_y
    for i, r in enumerate(rows):
        y = first_row_y - i * _TABLE_ROW_DY
        last_y = y
        # Picked and auto bands are listed alike; Assignment stays blank.
        values = [f"{r['position']:.0f}", _fmt_value(r["value"]), ""]
        for x, v in zip(_TABLE_COLS, values):
            fig.text(x, y, v, fontsize=9.5, family=SERIF, va="center")
    closing_y = last_y - _TABLE_ROW_DY * 0.55
    fig.add_artist(Line2D([_TABLE_LEFT, _TABLE_RIGHT], [closing_y, closing_y], color="black", lw=0.8, transform=fig.transFigure))
    return closing_y


def _draw_footer(fig: Figure) -> None:
    fig.text(
        _RIGHT,
        _FOOTER_Y,
        f"Generated with OpenFTIR {__version__}",
        fontsize=7.5,
        color="#666666",
        ha="right",
        family=SERIF,
    )


def _max_rows(first_row_y: float) -> int:
    if first_row_y <= _TABLE_MIN_ROW_Y:
        return 0
    return int((first_row_y - _TABLE_MIN_ROW_Y) / _TABLE_ROW_DY) + 1


def export_report_pdf(
    path: str,
    spectrum: Spectrum,
    *,
    title: str | None = None,
    manual_peaks: Sequence[float] = (),
    auto_peak_params: dict | None = None,
    paper: str = "letter",
    info: dict | None = None,
) -> None:
    """Export a "classic" infrared-spectrum PDF report to ``path``.

    ``spectrum`` must be %Transmittance, %Reflectance or Absorbance (the
    only units a "principal absorption bands" table makes sense for);
    anything else raises ``ValueError`` with a message fit to show the
    user directly.

    ``title`` overrides the Sample field (falls back to ``spectrum.title``,
    then "--"). ``manual_peaks`` are wavenumber positions (merged with
    ``auto_label_peaks(spectrum, **auto_peak_params)``, manual peaks always
    winning over a nearby auto one -- see ``_merged_peak_rows``).
    ``auto_peak_params`` should mirror whatever auto-labelling thresholds
    the app's own View menu currently has selected, so the table matches
    the plot. ``info`` optionally supplies header fields the spectrum's own
    ``meta`` cannot ("driver_display_name", "background_time" -- a
    ``datetime``) -- see ``_resolve_fields``.

    A peak list too long to fit on one page continues onto further pages
    with the same title/rule/table-header styling (no page numbers, per
    the approved design -- the footer is always just "Generated with
    OpenFTIR <version>").
    """
    if spectrum.y_unit not in (YUnit.TRANSMITTANCE, YUnit.REFLECTANCE, YUnit.ABSORBANCE):
        raise ValueError(
            "A PDF report needs a %Transmittance, %Reflectance or Absorbance spectrum "
            f"(got {spectrum.y_unit.value!r}). Convert the spectrum first."
        )
    if len(spectrum.x) == 0:
        raise ValueError("Cannot export a report for an empty spectrum.")

    paper_key = (paper or "letter").strip().lower()
    if paper_key not in _PAPER_SIZES_IN:
        raise ValueError(f"Unknown paper size {paper!r}; expected 'letter' or 'a4'.")
    width_in, height_in = _PAPER_SIZES_IN[paper_key]

    fields = _resolve_fields(spectrum, title, info)
    rows = _merged_peak_rows(spectrum, manual_peaks, auto_peak_params)
    value_label = _table_header_label(spectrum)

    figures: list[Figure] = []

    # -- page 1: title, field block, spectrum, as many table rows as fit ------------
    fig1 = Figure(figsize=(width_in, height_in))
    FigureCanvasPdf(fig1)
    figures.append(fig1)

    _draw_page_header(fig1)
    rule2_y = _draw_field_block(fig1, fields)
    plot_top = rule2_y - _PLOT_GAP_BELOW_RULE
    _draw_spectrum(fig1, spectrum, rows, plot_top)
    plot_bottom = plot_top - _PLOT_HEIGHT

    table_title_y = plot_bottom - _TABLE_TITLE_GAP
    first_row_y = _draw_table_header(fig1, table_title_y, value_label, continued=False)
    max_rows_p1 = max(_max_rows(first_row_y), 0)

    page1_rows = rows[:max_rows_p1]
    remaining_rows = rows[max_rows_p1:]
    if page1_rows:
        _draw_table_rows(fig1, page1_rows, first_row_y)
    elif rows:
        # Nothing at all fits (pathological case, e.g. a tiny custom paper
        # size) -- still draw the closing rule directly under the header so
        # the page doesn't look broken.
        _draw_table_rows(fig1, [], first_row_y)
    _draw_footer(fig1)

    # -- continuation pages: repeat the title header, continue the table -------------
    while remaining_rows:
        fig = Figure(figsize=(width_in, height_in))
        FigureCanvasPdf(fig)
        figures.append(fig)

        _draw_page_header(fig)
        table_title_y = _HEADER_ROW0_Y
        first_row_y = _draw_table_header(fig, table_title_y, value_label, continued=True)
        max_rows = max(_max_rows(first_row_y), 1)  # always make progress
        page_rows = remaining_rows[:max_rows]
        remaining_rows = remaining_rows[max_rows:]
        _draw_table_rows(fig, page_rows, first_row_y)
        _draw_footer(fig)

    with PdfPages(path) as pdf:
        for fig in figures:
            pdf.savefig(fig)
