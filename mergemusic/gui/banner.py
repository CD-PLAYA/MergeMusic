"""The MergeMusic banner shown across the top of the Welcome page."""

import os
import sys

from PySide6.QtCore import QRect, QRectF, QSize
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

# The banner file is 2:1 with empty space above and below the artwork. The window only
# shows the band with the artwork (from 17% to 81% of the height), so the text still fits.
CROP_TOP = 0.17
CROP_HEIGHT = 0.64
MAX_HEIGHT = 260
CORNER_RADIUS = 10


def resource_path(name):
    """Path of a file in mergemusic/resources, in the source tree or in the built app."""
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for p in (os.path.join(base, 'mergemusic', 'resources', name),
              os.path.join(base, 'resources', name)):
        if os.path.exists(p):
            return p
    return None


class Banner(QWidget):
    """Draws the banner scaled to the page width, keeping its shape, with rounded corners."""

    def __init__(self, path=None, parent=None):
        super().__init__(parent)
        path = path or resource_path('banner.jpg')
        pix = QPixmap(path) if path else QPixmap()
        if not pix.isNull():
            top = round(pix.height() * CROP_TOP)
            pix = pix.copy(QRect(0, top, pix.width(), round(pix.height() * CROP_HEIGHT)))
        self.pixmap = pix
        self.setAccessibleName('MergeMusic banner')
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setVisible(self.loaded())

    def loaded(self):
        return not self.pixmap.isNull()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        if not self.loaded():
            return 0
        return min(MAX_HEIGHT, round(width * self.pixmap.height() / self.pixmap.width()))

    def sizeHint(self):
        return QSize(800, self.heightForWidth(800))

    def minimumSizeHint(self):
        return QSize(300, self.heightForWidth(300))

    def paintEvent(self, event):
        if not self.loaded():
            return
        # Largest rectangle with the banner's shape that fits, centred.
        r = QRectF(self.rect())
        ratio = self.pixmap.width() / self.pixmap.height()
        w = min(r.width(), r.height() * ratio)
        h = w / ratio
        target = QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h)
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(target, CORNER_RADIUS, CORNER_RADIUS)
        painter.setClipPath(clip)
        painter.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        painter.end()
