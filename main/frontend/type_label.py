"""One borderless type/context label style for widgets and painted result rows."""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont
from .widgets import ElidedLabel, pixel_font


def _font(config):
    return pixel_font(config['results']['font_family'], 12, QFont.Weight.Medium)


class TypeLabel(ElidedLabel):
    def __init__(self, text, config, parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setFont(_font(config))
        self.setStyleSheet(f"color: {config['colors']['muted']}; background: transparent; border: none; padding: 0;")
        self.setAccessibleName(text)
        self.setToolTip(text)

    @staticmethod
    def paint(painter, bounds, text, config):
        painter.setFont(_font(config))
        text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, min(160, bounds.width()))
        width = painter.fontMetrics().horizontalAdvance(text)
        painter.setPen(QColor(config['colors']['muted']))
        painter.drawText(QRect(bounds.right() - width, bounds.y(), width + 1, bounds.height()),
                         Qt.AlignmentFlag.AlignVCenter, text)
        return width
