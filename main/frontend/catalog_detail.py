"""Shared selected-image surface and contract-driven session options for tool catalogs."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget
from .image_preview import ImagePreview
from .renderer import clear_layout
from .settings import SettingsFields
from .controls import Button


class CatalogDetail(QFrame):
    optionsChanged = Signal(object)
    saveRequested = Signal()
    settingsRequested = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        colors = config['colors']
        self.identity = None
        self.session = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        self.image = ImagePreview(colors)
        self.caption = QLabel('Select an image')
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet(f"color: {colors['muted']};")
        layout.addWidget(self.image, 1)
        layout.addWidget(self.caption)
        self.quick_box = QWidget()
        self.quick_layout = QVBoxLayout(self.quick_box)
        self.quick_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.quick_box)
        self.options = QScrollArea()
        self.options.setWidgetResizable(True)
        self.options.setStyleSheet(f"QScrollArea {{ border: none; background: {colors['background']}; }}")
        self.options.hide()
        layout.addWidget(self.options, 1)
        controls = QHBoxLayout()
        self.settings_button = Button('Settings', config)
        self.settings_button.clicked.connect(self.settingsRequested)
        self.options_button = Button('Options…', config)
        self.options_button.clicked.connect(self.toggle_options)
        self.defaults_button = Button('Save defaults', config)
        self.defaults_button.clicked.connect(self.saveRequested)
        self.reset_button = Button('Reset', config)
        self.reset_button.clicked.connect(self.reset)
        for button in (self.settings_button, self.options_button, self.reset_button, self.defaults_button):
            controls.addWidget(button)
        layout.addLayout(controls)
        self.quick = self.editor = None

    def configure(self, identifier, fields, catalog, values):
        identity = identifier, repr(fields), catalog.settings_keys, catalog.option_keys
        if self.identity == identity:
            return
        self.identity, self.catalog = identity, catalog
        old = self.options.takeWidget()
        if old:
            old.deleteLater()
        self.editor = None
        self.options.hide()
        self.image.show()
        self.caption.show()
        self.options_button.setText('Options…')
        keys = set(catalog.option_keys or catalog.settings_keys)
        if not keys.issubset({field['key'] for field in fields}) or not set(catalog.settings_keys).issubset(keys):
            raise ValueError('Catalog options must reference declared configuration fields')
        self.option_fields = [field for field in fields if field['key'] in keys]
        self.quick_fields = [field for field in fields if field['key'] in catalog.settings_keys]
        self.session = {key: value for key, value in values.items() if key in keys}
        self.options_button.setVisible(bool(keys))
        self.defaults_button.setVisible(bool(keys))
        self.reset_button.setVisible(bool(keys))
        self.rebuild_quick()

    def rebuild_quick(self):
        clear_layout(self.quick_layout)
        self.quick = SettingsFields(self.quick_fields, self.session, self.quick_box, compact=True)
        self.quick_layout.addWidget(self.quick)
        self.quick.changed.connect(lambda: self.changed(self.quick))
        self.quick_box.setVisible(bool(self.quick_fields) and self.options.isHidden())

    def changed(self, editor):
        try:
            self.session.update(editor.values())
            self.optionsChanged.emit(dict(self.session))
        except ValueError as error:
            self.caption.setText(str(error))

    def toggle_options(self):
        opening = self.options.isHidden()
        if opening:
            old = self.options.takeWidget()
            if old:
                old.deleteLater()
            self.editor = SettingsFields(self.option_fields, self.session, self.options)
            self.editor.changed.connect(lambda: self.changed(self.editor))
            self.options.setWidget(self.editor)
        else:
            self.rebuild_quick()
        self.options.setVisible(opening)
        self.image.setVisible(not opening)
        self.caption.setVisible(not opening)
        self.quick_box.setVisible(not opening and bool(self.quick_fields))
        self.options_button.setText('Back to preview' if opening else 'Options…')

    def reset(self):
        from ..plugin_settings import default
        self.session = {field['key']: default(field) for field in self.option_fields}
        self.optionsChanged.emit(dict(self.session))
        if not self.options.isHidden():
            self.toggle_options()
        self.rebuild_quick()

    def set_busy(self, busy):
        for button in (self.defaults_button, self.settings_button, self.options_button, self.reset_button):
            button.setEnabled(not busy)
        self.quick_box.setEnabled(not busy)
        self.options.setEnabled(not busy)
