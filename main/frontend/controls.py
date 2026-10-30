"""Shared plugin field/button styling, independent of launcher navigation."""
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLineEdit, QPushButton, QSizePolicy
from .widgets import pixel_font


class TextInput(QLineEdit):
    def __init__(self, text, placeholder, password, config, parent=None):
        super().__init__(text, parent)
        colors = config['colors']
        self.setFont(pixel_font(config['results']['font_family'], 14))
        self.setPlaceholderText(placeholder)
        self.setEchoMode(QLineEdit.EchoMode.Password if password else QLineEdit.EchoMode.Normal)
        self.setMaxLength(4096)
        self.setFixedHeight(42)
        self.setStyleSheet(f"""
            QLineEdit {{ background: {colors['search']}; color: {colors['text']};
                border: 1px solid {colors['border']}; border-radius: 4px; padding: 0 12px;
                selection-background-color: {colors['selected']}; }}
            QLineEdit:focus {{ border-color: {colors['accent']}; }}
            QLineEdit:disabled {{ color: {colors['muted']}; }}
        """)


class Button(QPushButton):
    def __init__(self, text, config, variant='secondary', parent=None):
        super().__init__(text, parent)
        colors = config['colors']
        background = colors['accent'] if variant == 'primary' else colors['search']
        foreground = colors['background'] if variant == 'primary' else colors['text']
        border = colors['accent'] if variant == 'primary' else colors['border']
        if variant == 'danger':
            foreground, border = '#e89b9b', '#e89b9b'
        self.setFont(pixel_font(config['results']['font_family'], 14, QFont.Weight.DemiBold))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(40)
        self.setMinimumWidth(max(96, self.fontMetrics().horizontalAdvance(text) + 32))
        self.setStyleSheet(f"""
            QPushButton {{ background: {background}; color: {foreground}; border: 1px solid {border};
                border-radius: 4px; padding: 0 16px; }}
            QPushButton:hover {{ border-color: {colors['text']}; }}
            QPushButton:focus {{ border: 2px solid {colors['text']}; }}
            QPushButton:disabled {{ background: {colors['search']}; color: {colors['muted']};
                border-color: {colors['border']}; }}
        """)
