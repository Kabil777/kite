"""Full-content, scrollable form with shared controls and inline discard confirmation."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QScrollArea, QVBoxLayout, QWidget)
from .controls import Button
from .widgets import pixel_font


class Form(QScrollArea):
    submitted = Signal()
    backRequested = Signal()
    discardRequested = Signal()
    keepEditing = Signal()

    def __init__(self, props, config, parent=None):
        super().__init__(parent)
        self.dirty = False
        self.busy = False
        self.can_submit = False
        self._focus_id = ''
        self.title_text = props.get('title', 'Form')
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet('QScrollArea { border: none; background: transparent; }')
        body = QWidget()
        body.setStyleSheet(f"background: {config['colors']['background']};")
        outer = QVBoxLayout(body)
        outer.setContentsMargins(24, 16, 24, 20)
        outer.setSpacing(12)
        for key, size, weight, color in (
            ('title', 20, QFont.Weight.DemiBold, config['colors']['text']),
            ('description', 13, QFont.Weight.Normal, config['colors']['muted']),
            ('error', 13, QFont.Weight.Medium, '#e89b9b'),
        ):
            label = QLabel(props.get(key, ''))
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setFont(pixel_font(config['results']['font_family'], size, weight))
            label.setStyleSheet(f'color: {color}; background: transparent;')
            outer.addWidget(label)
            label.setVisible(bool(props.get(key)))
        self.confirmation = QFrame()
        self.confirmation.setStyleSheet(f"QFrame {{ background: {config['colors']['search']}; border: none; }}")
        confirm_layout = QVBoxLayout(self.confirmation)
        confirm_layout.setContentsMargins(12, 12, 12, 12)
        warning = QLabel('Discard unsaved changes? Any operation already sent may have completed.')
        warning.setTextFormat(Qt.TextFormat.PlainText)
        warning.setWordWrap(True)
        warning.setFont(pixel_font(config['results']['font_family'], 13))
        confirm_layout.addWidget(warning)
        choices = QHBoxLayout()
        discard = Button('Discard changes', config, 'danger')
        discard.clicked.connect(self.discardRequested)
        keep = Button('Keep editing', config)
        keep.clicked.connect(self.hide_discard_prompt)
        choices.addWidget(discard)
        choices.addWidget(keep)
        choices.addStretch(1)
        confirm_layout.addLayout(choices)
        self.confirmation.hide()
        outer.addWidget(self.confirmation)
        self.fields = QWidget()
        self.fields_layout = QVBoxLayout(self.fields)
        self.fields_layout.setContentsMargins(0, 0, 0, 0)
        self.fields_layout.setSpacing(8)
        outer.addWidget(self.fields)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.submit_button = Button(props.get('submit_text', 'Save'), config, 'primary')
        self.submit_button.clicked.connect(self.submitted)
        self.cancel_button = Button('Cancel', config)
        self.cancel_button.clicked.connect(self.backRequested)
        actions.addWidget(self.submit_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        outer.addLayout(actions)
        outer.addStretch(1)
        self.setWidget(body)

    def bind_fields(self, can_submit):
        self.can_submit = can_submit
        self.submit_button.setEnabled(can_submit)
        for field in self.fields.findChildren(QLineEdit):
            field.textEdited.connect(self.mark_dirty)
            field.returnPressed.connect(self.submit_button.click)
        for field in self.fields.findChildren(QComboBox):
            field.activated.connect(self.mark_dirty)
        for field in self.fields.findChildren(QListWidget):
            field.itemClicked.connect(self.mark_dirty)

    def mark_dirty(self, *_):
        self.dirty = True

    def set_busy(self, busy):
        self.busy = busy
        self.fields.setEnabled(not busy and self.confirmation.isHidden())
        self.submit_button.setEnabled(self.can_submit and not busy and self.confirmation.isHidden())

    def show_discard_prompt(self):
        focus = QApplication.focusWidget()
        self._focus_id = focus.objectName() if focus else ''
        self.confirmation.show()
        self.set_busy(self.busy)
        self.widget().layout().activate()
        self.ensureWidgetVisible(self.confirmation)
        self.confirmation.findChildren(Button)[1].setFocus()

    def hide_discard_prompt(self):
        self.confirmation.hide()
        self.set_busy(self.busy)
        field = self.fields.findChild(QWidget, self._focus_id) if self._focus_id else None
        if field:
            field.setFocus()
        self.keepEditing.emit()

    def wipe_passwords(self):
        for field in self.fields.findChildren(QLineEdit):
            if field.echoMode() == QLineEdit.EchoMode.Password:
                field.setText('')  # Also resets QLineEdit's undo/redo history.
