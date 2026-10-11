"""Startup splash screen (the user-approved "classic" design).

Kept free of heavy imports (no numpy/scipy/pyqtgraph/openftir.processing) so
it can be shown before the rest of the app loads; ``app.main`` then advances
the progress bar as each stage finishes.
"""

from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QSplashScreen

from openftir import __version__

WIDTH, HEIGHT = 600, 340

_RESOURCES = files("openftir.gui") / "resources"
_FONT_FAMILY = "Noto Sans"
_fonts_loaded = False

# Absorption bands for the faint decorative spectrum: (position 0-1, half
# width, depth), the same shape as the app icon's trace.
_BANDS = (
    (0.14, 0.010, 0.50), (0.18, 0.008, 0.35), (0.33, 0.050, 0.25),
    (0.52, 0.007, 0.70), (0.56, 0.008, 0.45), (0.68, 0.015, 0.40),
    (0.74, 0.007, 0.60), (0.83, 0.010, 0.50), (0.88, 0.006, 0.30),
)


def _load_fonts() -> None:
    global _fonts_loaded
    if _fonts_loaded:
        return
    for name in ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"):
        QFontDatabase.addApplicationFont(str(_RESOURCES / "fonts" / name))
    _fonts_loaded = True


def _font(pixel_size: int, bold: bool = False) -> QFont:
    font = QFont(_FONT_FAMILY)
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    return font


def _spectrum_path(rect: QRectF, samples: int = 500) -> QPainterPath:
    path = QPainterPath()
    for i in range(samples):
        x = i / (samples - 1)
        t = 1.0
        for centre, half_width, depth in _BANDS:
            t -= depth * half_width**2 / ((x - centre) ** 2 + half_width**2)
        point = QPointF(rect.left() + x * rect.width(), rect.top() + (1 - t) * rect.height())
        if i == 0:
            path.moveTo(point)
        else:
            path.lineTo(point)
    return path


def render_splash(message: str, progress: float, device_pixel_ratio: float = 1.0) -> QPixmap:
    """Draw the splash at ``WIDTH`` x ``HEIGHT`` logical pixels."""
    _load_fonts()
    pixmap = QPixmap(int(WIDTH * device_pixel_ratio), int(HEIGHT * device_pixel_ratio))
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    pixmap.fill(Qt.transparent)

    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    gradient = QLinearGradient(0, 0, WIDTH, HEIGHT)
    gradient.setColorAt(0, QColor("#163f78"))
    gradient.setColorAt(1, QColor("#13917f"))
    p.setPen(Qt.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(QRectF(0, 0, WIDTH, HEIGHT), 10, 10)

    p.setPen(QPen(QColor(255, 255, 255, 38), 3))
    p.setBrush(Qt.NoBrush)
    p.drawPath(_spectrum_path(QRectF(-20, 150, WIDTH + 40, 120)))

    QSvgRenderer(str(_RESOURCES / "openftir.svg")).render(p, QRectF(36, 40, 92, 92))

    p.setPen(QColor("white"))
    p.setFont(_font(40, bold=True))
    p.drawText(QRectF(146, 46, 420, 52), Qt.AlignLeft | Qt.AlignVCenter, "OpenFTIR")
    p.setPen(QColor("#cfe0f2"))
    p.setFont(_font(15))
    p.drawText(QRectF(148, 96, 420, 26), Qt.AlignLeft | Qt.AlignVCenter, "Open-source FTIR spectroscopy")

    p.setFont(_font(12))
    p.setPen(QColor("#e8f1fb"))
    p.drawText(QRectF(36, 278, 400, 20), Qt.AlignLeft | Qt.AlignVCenter, message)
    p.setPen(QColor("#b8cde3"))
    p.drawText(QRectF(WIDTH - 236, 278, 200, 20), Qt.AlignRight | Qt.AlignVCenter, f"Version {__version__}")

    track = QRectF(36, 304, WIDTH - 72, 4)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 64))
    p.drawRoundedRect(track, 2, 2)
    done = QRectF(track)
    done.setWidth(track.width() * max(0.0, min(progress, 1.0)))
    p.setBrush(QColor("white"))
    p.drawRoundedRect(done, 2, 2)

    p.end()
    return pixmap


class SplashScreen(QSplashScreen):
    """The splash window; call ``advance()`` as startup stages complete."""

    def __init__(self) -> None:
        self._dpr = 1.0
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self._dpr = screen.devicePixelRatio()
        super().__init__(render_splash("Starting…", 0.05, self._dpr))
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)  # rounded corners
        self.message = "Starting…"
        self.progress = 0.05

    def advance(self, message: str, progress: float) -> None:
        self.message, self.progress = message, progress
        self.setPixmap(render_splash(message, progress, self._dpr))
        QApplication.processEvents()
