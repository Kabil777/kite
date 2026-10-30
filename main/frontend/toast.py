"""Nonmodal, closeable notification banner; no separate native popup window."""
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QAccessible, QAccessibleEvent, QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
from .widgets import glyph_font, pixel_font


class ToastBanner(QFrame):
    def __init__(self, config, parent):
        super().__init__(parent)
        self.cfg = config
        self.setObjectName('toastBanner')
        colors, family = config['colors'], config['search']['font_family']
        self.setStyleSheet(f"QFrame#toastBanner {{ background: {colors['search']}; "
                          f"border: 1px solid {colors['border']}; border-radius: 8px; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(28, 28)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setFont(pixel_font(family, 19))
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(3)
        self.title, self.message = QLabel(), QLabel()
        for label, size, weight, color in ((self.title, 14, QFont.Weight.DemiBold, colors['text']),
                                           (self.message, 13, QFont.Weight.Normal, colors['muted'])):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setFont(pixel_font(family, size, weight))
            label.setStyleSheet(f'color: {color}; background: transparent; border: none;')
            text.addWidget(label)
        layout.addLayout(text, 1)
        self.close_button = QPushButton('×')
        self.close_button.setFixedSize(28, 28)
        self.close_button.setFont(pixel_font(family, 18))
        self.close_button.setAccessibleName('Dismiss notification')
        self.close_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.close_button.setStyleSheet(f"QPushButton {{ color: {colors['muted']}; background: transparent; "
                                       'border: none; border-radius: 4px; padding: 0; }'
                                       f"QPushButton:hover {{ background: {colors['selected']}; }} "
                                       f"QPushButton:focus {{ border: 1px solid {colors['accent']}; }}")
        self.close_button.clicked.connect(self.dismiss)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        parent.installEventFilter(self)
        self.hide()

    def show_toast(self, toast):
        self.timer.stop()
        glyph, color = {'error': ('\uf057', '#e89b9b'), 'success': ('\uf058', '#a9d2a5'),
                        'info': ('\uf05a', self.cfg['colors']['accent'])}[toast.tone]
        font = glyph_font(glyph, 19)
        self.icon.setFont(font or pixel_font(self.cfg['search']['font_family'], 19))
        self.icon.setText(glyph if font is not None else {'error': '!', 'success': '✓', 'info': 'i'}[toast.tone])
        self.icon.setAccessibleName(toast.tone)
        self.icon.setStyleSheet(f'color: {color}; background: transparent; border: none;')
        self.title.setText(toast.title)
        self.message.setText(toast.message)
        self.message.setVisible(bool(toast.message))
        self.setAccessibleName(toast.title + (': ' + toast.message if toast.message else ''))
        self.reposition()
        self.show()
        self.raise_()
        if QAccessible.isActive():
            QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.Alert))
        if toast.tone != 'error':
            self.timer.start(4000)

    def text(self):
        """Compatibility with the launcher's former copy-toast QLabel."""
        return self.title.text()

    def reposition(self):
        parent = self.parentWidget()
        if parent is None:
            return
        self.setFixedWidth(max(1, min(520, parent.width() - 32)))
        layout = self.layout()
        if layout is not None:
            layout.activate()
            self.setFixedHeight(max(layout.minimumSize().height(), layout.totalHeightForWidth(self.width())))
        footer = self.cfg['footer']['height'] if self.cfg['footer']['visible'] else 0
        self.move((parent.width() - self.width()) // 2, max(12, parent.height() - footer - self.height() - 12))

    def dismiss(self):
        self.timer.stop()
        if self.close_button.hasFocus():
            self.focusNextChild()
        self.hide()
        self.title.clear()
        self.message.clear()
        self.setAccessibleName('')

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self.reposition()
        return super().eventFilter(watched, event)
