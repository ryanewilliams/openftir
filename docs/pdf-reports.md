# PDF spectrum reports

`openftir.report.export_report_pdf(path, spectrum, ...)` renders a one-page
(or more, for a long peak list) PDF report for a single %Transmittance,
%Reflectance or Absorbance spectrum: a serif title block, a Sample / Date /
Instrument / Serial / Resolution / Background field grid, the spectrum plot
(auto + manual peak labels with collision avoidance, manual peaks drawn as
▼ with bold labels), and a "Table 1. Principal absorption bands" listing
every labelled peak (sorted by descending wavenumber, manual peaks marked
`(picked)`).

It is built entirely on `matplotlib.figure.Figure` + the non-interactive PDF
backend (`FigureCanvasPdf` / `PdfPages`) -- never `pyplot` and never a Qt
backend -- so it is safe to call from the GUI thread. Fonts (Noto Serif,
`src/openftir/report/fonts/`, SIL OFL 1.1 licensed) are embedded as
TrueType so the exported text stays selectable/searchable.

## GUI

**File -> Export PDF Report...** (Ctrl+Shift+E, both Simple and Advanced
mode), or Simple mode's bottom-bar **Export** dropdown ("PDF report..."
listed first, "Data file..." for the original save-to-disk flow). A small
dialog asks for the sample name and paper size (Letter/A4), then the app's
usual non-native save dialog.

## Paper size and pagination

`paper="letter"` (8.5x11 in, default) or `paper="a4"` -- the whole layout is
specified in figure-fraction coordinates, so it scales correctly to either
page size. If the merged peak table has more rows than fit on one page, the
report continues onto further pages with the same title/header styling (no
page numbers anywhere, per the approved design -- the footer is always just
"Generated with OpenFTIR &lt;version&gt;").
