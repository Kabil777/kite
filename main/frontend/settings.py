"""Native settings editors generated only from the plugin's declarative contract."""
from html import escape
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout, QWidget)
from ..plugin_settings import default, value, validate_values


class SettingsFields(QWidget):
    changed = Signal()

    def __init__(self, fields, values, parent=None, compact=False):
        super().__init__(parent)
        self.fields, self.editors, self.inherit, self.invalid = fields, {}, {}, {}
        config = getattr(self.window(), 'cfg', {})
        colors = config.get('colors', {'background': '#232428', 'search': '#2a2b30', 'selected': '#373940', 'text': '#f1f2f5', 'muted': '#a1a4af', 'border': '#41434b'})
        self.setObjectName('pluginSettingsFields')
        self.setStyleSheet(f"""
            QWidget#pluginSettingsFields {{ background: {colors['background']}; }}
            QLineEdit, QDoubleSpinBox, QComboBox, QListWidget {{ background: {colors['search']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 4px; padding: 4px; }}
            QPushButton {{ background: {colors['selected']}; color: {colors['text']}; border: 1px solid {colors['border']}; border-radius: 4px; padding: 4px 7px; }}
            QCheckBox {{ color: {colors['muted']}; font-size: 11px; }}
            QLabel {{ color: {colors['text']}; }}
            QComboBox QAbstractItemView {{ background: {colors['search']}; color: {colors['text']}; }}
        """)
        layout = QHBoxLayout(self) if compact else QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        base_layout = layout
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced.hide()
        group = ''
        for field in fields:
            if compact:
                cell = QWidget()
                base_layout.addWidget(cell, 1)
                layout = QVBoxLayout(cell)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(2)
            else:
                layout = advanced_layout if field.get('advanced') else base_layout
            key, kind = field['key'], field['type']
            current = values.get(key, default(field))
            error = ''
            try:
                current = value(field, current)
            except ValueError as exception:
                error = str(exception)
            if not compact and field.get('group') and field['group'] != group:
                group = field['group']
                title = QLabel(group)
                title.setTextFormat(Qt.TextFormat.PlainText)
                title.setStyleSheet('font-weight: 600; margin-top: 8px;')
                layout.addWidget(title)
            label = QLabel(field['label'])
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
            row = QWidget()
            horizontal = QHBoxLayout(row)
            horizontal.setContentsMargins(0, 0, 0, 0)
            if kind.endswith('_list'):
                editor = QListWidget()
                editor.setMaximumHeight(104)
                for item in current if isinstance(current, list) else []:
                    editor.addItem(item)
                    editor.item(editor.count() - 1).setFlags(editor.item(editor.count() - 1).flags() | Qt.ItemFlag.ItemIsEditable)
                editor.itemChanged.connect(lambda *_, k=key: (self.invalid.pop(k, None), self.changed.emit()))
                entry = QLineEdit()
                entry.setPlaceholderText(field.get('placeholder', 'Add a path or item'))
                add = QPushButton('Add')
                remove = QPushButton('Remove')
                def append(e=editor, text=entry, data=field):
                    candidate = text.text()
                    if candidate and e.count() < data.get('max_items', 16):
                        e.addItem(candidate)
                        e.item(e.count() - 1).setFlags(e.item(e.count() - 1).flags() | Qt.ItemFlag.ItemIsEditable)
                        text.clear()
                        self.invalid.pop(data['key'], None)
                        self.changed.emit()
                add.clicked.connect(lambda _=False, callback=append: callback())
                remove.clicked.connect(lambda _=False, e=editor, k=key: (e.takeItem(e.currentRow()), self.invalid.pop(k, None), self.changed.emit()))
                box = QVBoxLayout()
                box.addWidget(editor)
                controls = QHBoxLayout()
                controls.addWidget(entry)
                controls.addWidget(add)
                controls.addWidget(remove)
                reset = QPushButton('Clear')
                reset.clicked.connect(lambda _=False, e=editor, k=key: (self.invalid.pop(k, None), e.clear(), self.changed.emit()))
                controls.addWidget(reset)
                if kind != 'string_list':
                    browse = QPushButton('Browse…')
                    def pick(e=editor, data=field):
                        paths = ([QFileDialog.getExistingDirectory(self, data['label'])] if data['type'] == 'directory_list' else
                                 QFileDialog.getOpenFileNames(self, data['label'])[0])
                        for path in paths:
                            if path and e.count() < data.get('max_items', 16):
                                e.addItem(path)
                                e.item(e.count() - 1).setFlags(e.item(e.count() - 1).flags() | Qt.ItemFlag.ItemIsEditable)
                                self.invalid.pop(data['key'], None)
                                self.changed.emit()
                    browse.clicked.connect(lambda _=False, callback=pick: callback())
                    controls.addWidget(browse)
                box.addLayout(controls)
                horizontal.addLayout(box, 1)
            elif kind == 'boolean':
                editor = QCheckBox('Enabled')
                editor.setChecked(current is True)
                editor.toggled.connect(self.changed)
            elif kind == 'number':
                editor = QDoubleSpinBox()
                editor.setRange(field.get('min', -1e9), field.get('max', 1e9))
                editor.setDecimals(0 if field.get('integer') else field.get('decimals', 3))
                editor.setSingleStep(field.get('step', 1))
                editor.setValue(current if type(current) in (int, float) else 0)
                editor.valueChanged.connect(self.changed)
            elif kind == 'choice':
                editor = QComboBox()
                editor.addItems(field['options'])
                editor.setPlaceholderText('Backend default')
                if current is None:
                    editor.setCurrentIndex(-1)
                else:
                    editor.setCurrentText(current if isinstance(current, str) else field['options'][0])
                editor.currentTextChanged.connect(self.changed)
            else:
                editor = QLineEdit(current if isinstance(current, str) else '')
                editor.setMaxLength(field.get('max_length', 1024))
                editor.setPlaceholderText(field.get('placeholder', ''))
                editor.textEdited.connect(self.changed)
                if kind in ('directory', 'file', 'color'):
                    browse = QPushButton('Choose…')
                    def choose(e=editor, data=field):
                        if data['type'] == 'color':
                            raw = e.text().lstrip('#')
                            initial = '#' + (raw[-2:] + raw[:-2] if len(raw) == 8 else raw)
                            color = QColorDialog.getColor(QColor(initial), self,
                                                          options=QColorDialog.ColorDialogOption.ShowAlphaChannel)
                            path = f'{color.red():02x}{color.green():02x}{color.blue():02x}{color.alpha():02x}' if color.isValid() else ''
                        elif data['type'] == 'directory':
                            path = QFileDialog.getExistingDirectory(self, data['label'])
                        else:
                            path = QFileDialog.getOpenFileName(self, data['label'])[0]
                        if path:
                            e.setText(path)
                            self.changed.emit()
                    browse.clicked.connect(lambda _=False, callback=choose: callback())
                    horizontal.addWidget(browse)
            editor.setObjectName('config-' + key)
            editor.setAccessibleName(field['label'])
            editor.setToolTip('<qt>' + escape(field.get('help', '')) + '</qt>')
            self.editors[key] = editor
            if not kind.endswith('_list'):
                horizontal.insertWidget(0, editor, 1)
            if field.get('optional'):
                inherited = QCheckBox('Default' if compact else 'Backend default')
                inherited.setChecked(current is None)
                row.setEnabled(current is not None)
                inherited.toggled.connect(lambda checked, target=row: target.setEnabled(not checked))
                inherited.toggled.connect(self.changed)
                self.inherit[key] = inherited
                inherited.toggled.connect(lambda *_, k=key: self.invalid.pop(k, None))
                layout.addWidget(inherited)
            layout.addWidget(row)
            if error:
                self.invalid[key] = error
                if not kind.endswith('_list'):
                    signal = (editor.toggled if kind == 'boolean' else editor.valueChanged if kind == 'number' else
                              editor.currentTextChanged if kind == 'choice' else editor.textEdited)
                    signal.connect(lambda *_, k=key: self.invalid.pop(k, None))
            if (field.get('help') or error) and not compact:
                help_text = QLabel(error or field['help'])
                help_text.setWordWrap(True)
                help_text.setTextFormat(Qt.TextFormat.PlainText)
                layout.addWidget(help_text)
        if not compact and any(field.get('advanced') for field in fields):
            toggle = QCheckBox('Advanced options')
            toggle.toggled.connect(advanced.setVisible)
            base_layout.addWidget(toggle)
            base_layout.addWidget(advanced)
        else:
            advanced.deleteLater()
        if not compact:
            base_layout.addStretch(1)

    def values(self):
        if self.invalid:
            raise ValueError(next(iter(self.invalid.values())))
        values = {}
        for field in self.fields:
            key, kind = field['key'], field['type']
            editor = self.editors[key]
            if key in self.inherit and self.inherit[key].isChecked():
                data = None
            elif kind.endswith('_list'):
                data = [editor.item(index).text() for index in range(editor.count())]
            elif kind == 'number':
                data = editor.value()
            elif kind == 'boolean':
                data = editor.isChecked()
            elif kind == 'choice':
                data = editor.currentText()
            else:
                data = editor.text()
            values[key] = data
        return validate_values(self.fields, values)
