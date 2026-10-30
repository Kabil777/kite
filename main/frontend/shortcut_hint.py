"""Shared shortcut keycaps for widgets, declarative nodes and painted Actions rows."""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

KEY_SYMBOLS = {'Enter': '↩', 'Return': '↩', 'Alt': '⌥', 'Ctrl': '⌃', 'Shift': '⇧', 'Esc': '⎋'}


def _keys(keys):
    return [KEY_SYMBOLS.get(key, key) for key in keys.replace('+', ' ').split()]


def _font(family):
    font = QFont(family)
    font.setPixelSize(11)
    font.setWeight(QFont.Weight.DemiBold)
    return font


class ShortcutHint(QWidget):
    def __init__(self, keys, family='Adwaita Sans', colors=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(keys)
        self.setToolTip(keys)
        foreground = QColor(colors['text']) if colors else self.palette().color(self.foregroundRole())
        rgb = f'{foreground.red()}, {foreground.green()}, {foreground.blue()}'
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        for raw, text in zip(keys.replace('+', ' ').split(), _keys(keys)):
            label = QLabel(text)
            label.setAccessibleName(raw)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setObjectName('keycap')
            label.setFont(_font(family))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            label.setStyleSheet(f'background: rgba({rgb}, 25); color: rgba({rgb}, 220); '
                                'border: none; border-radius: 4px; padding: 3px 6px; min-width: 10px; '
                                'font-size: 11px; font-weight: 600;')
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def paint(painter, right, center_y, keys, family, colors):
        painter.setFont(_font(family))
        background, foreground = QColor(colors['text']), QColor(colors['text'])
        background.setAlpha(25)
        foreground.setAlpha(220)
        parts = _keys(keys)
        for key in reversed(parts):
            height = painter.fontMetrics().height() + 6
            width = max(22, painter.fontMetrics().horizontalAdvance(key) + 12)
            box = QRect(right - width, center_y - height // 2, width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(foreground)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, key)
            right -= width + 3
        return right + (3 if parts else 0)
