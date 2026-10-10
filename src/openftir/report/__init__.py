"""PDF spectrum report export.

:func:`export_report_pdf` renders a one- (or more-) page PDF report for a
single processed spectrum: a title block, a 2x3 header field grid (Sample /
Date / Instrument / Serial / Resolution / Background), the spectrum plot
with auto and manual peak labels, and a "principal absorption bands" table.

Built on ``matplotlib``'s non-interactive PDF backend only -- never
``pyplot`` global state, never a Qt backend -- so it is safe to call from
the GUI thread (or anywhere else) without interfering with the app's own
Qt/pyqtgraph plotting.
"""

from .pdf import export_report_pdf

__all__ = ["export_report_pdf"]
