"""Host renderer for the small declarative plugin UI vocabulary."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout, QWidget, QApplication)
from ..plugin_api import Event, Node
from .controls import Button, TextInput
from .form import Form
from .image_preview import ImagePreview
from .markdown import MarkdownView
from .shortcut_hint import ShortcutHint
from .type_label import TypeLabel
from .widgets import pixel_font, themed_icon


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child = item.layout()
        if widget:
            # Keep retired controls out of this panel without creating top-level windows.
            parent = layout.parentWidget()
            widget.hide()
            widget.setParent(parent.window() if parent is not None else None)
            widget.deleteLater()
        elif child:
            clear_layout(child)


def input_values(parent):
    values = {widget.objectName(): widget.text() for widget in parent.findChildren(QLineEdit)}
    values.update({widget.objectName(): widget.currentText() for widget in parent.findChildren(QComboBox)})
    values.update({widget.objectName(): widget.currentItem().text() if widget.currentItem() else ''
                   for widget in parent.findChildren(QListWidget)})
    return values


def render_nodes(layout, nodes: tuple[Node, ...], dispatch, config):
    focused = QApplication.focusWidget()
    focus_id = focused.objectName() if focused else ''
    position = focused.cursorPosition() if isinstance(focused, QLineEdit) else None
    clear_layout(layout)
    for node in nodes:
        if node.type in ('row', 'column'):
            widget = QWidget()
            widget.setObjectName(node.id)
            widget.setAccessibleName(node.accessibility_label)
            child_layout = QHBoxLayout(widget) if node.type == 'row' else QVBoxLayout(widget)
            child_layout.setContentsMargins(0, 0, 0, 0)
            child_layout.setSpacing(int(node.props.get('spacing', 8)))
            layout.addWidget(widget)
            render_nodes(child_layout, node.children, dispatch, config)
            continue
        props = node.props
        if node.type == 'form':
            widget = Form(props, config)
            render_nodes(widget.fields_layout, node.children, dispatch, config)
            widget.bind_fields('submit' in node.events)
            widget.submitted.connect(lambda n=node: dispatch(Event(n.id, 'submit', n.events.get('submit', {}))))
            widget.backRequested.connect(lambda n=node: dispatch(Event(n.id, 'back', {})))
            widget.discardRequested.connect(lambda n=node: dispatch(Event(n.id, 'discard', {})))
            widget.keepEditing.connect(lambda n=node: dispatch(Event(n.id, 'keep', {})))
        elif node.type == 'label':
            widget = QLabel(str(props.get('text', '')))
            widget.setWordWrap(props.get('wrap', False) is True)
            widget.setTextFormat(Qt.TextFormat.PlainText)
        elif node.type == 'shortcut_hint':
            widget = ShortcutHint(props.get('keys', ''), config['results']['font_family'], config['colors'])
        elif node.type == 'type_label':
            widget = TypeLabel(props.get('text', ''), config)
        elif node.type == 'icon':
            widget = QLabel()
            icon = themed_icon(str(props.get('name', 'application-x-executable')))
            size = max(12, min(96, int(props.get('size', config['icons']['size']))))
            widget.setPixmap(icon.pixmap(size, size))
            widget.setAccessibleName(node.accessibility_label or str(props.get('name', 'Icon')))
        elif node.type == 'markdown':
            widget = MarkdownView(config['colors'])
            widget.set_markdown(props.get('text', ''))
        elif node.type == 'image_preview':
            widget = ImagePreview(config['colors'])
            widget.setMinimumHeight(160)
            widget.set_source(props['source'], props.get('alt', ''))
        elif node.type == 'button':
            widget = Button(str(props.get('text', '')), config, props.get('variant', 'secondary'))
            widget.setEnabled('activate' in node.events)
            widget.clicked.connect(lambda _=False, n=node: dispatch(Event(n.id, 'activate', n.events.get('activate', {}))))
        elif node.type == 'text_input':
            widget = TextInput(str(props.get('text', '')), str(props.get('placeholder', '')),
                               props.get('password') is True, config)
            if 'change' in node.events:
                widget.textEdited.connect(lambda text, n=node: dispatch(Event(n.id, 'change', {**n.events['change'], 'text': text})))
        elif node.type == 'select':
            widget = QComboBox()
            values = props.get('options', [])
            if isinstance(values, list) and all(isinstance(value, str) for value in values):
                widget.addItems(values)
                widget.setCurrentText(str(props.get('value', values[0] if values else '')))
            if 'change' in node.events:
                widget.currentTextChanged.connect(lambda text, n=node: dispatch(Event(n.id, 'change', {**n.events['change'], 'value': text})))
        elif node.type == 'list':
            widget = QListWidget()
            widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            values = props.get('items', [])
            if isinstance(values, list):
                for value in values[:1000]:
                    item = QListWidgetItem(str(value))
                    widget.addItem(item)
                if props.get('value') in values:
                    widget.setCurrentRow(values.index(props['value']))
            if 'activate' in node.events:
                widget.itemActivated.connect(lambda item, n=node: dispatch(Event(n.id, 'activate', {**n.events['activate'], 'value': item.text()})))
        else:
            raise ValueError('Unsupported plugin component: ' + node.type)
        widget.setObjectName(node.id)
        widget.setAccessibleName(node.accessibility_label or str(props.get('keys', props.get('alt', props.get('text', node.id)))))
        if node.type not in ('markdown', 'type_label', 'form', 'text_input', 'button', 'shortcut_hint'):
            widget.setFont(pixel_font(config['results']['font_family'], config['results']['font_size'], QFont.Weight.Medium))
        layout.addWidget(widget, 1 if node.type in ('markdown', 'image_preview', 'form') else 0)
    if not any(node.type == 'form' for node in nodes):
        layout.addStretch(1)
    if focus_id:
        parent = layout.parentWidget()
        replacement = parent.findChild(QWidget, focus_id) if parent else None
        if replacement:
            replacement.setFocus()
            if isinstance(replacement, QLineEdit) and position is not None:
                replacement.setCursorPosition(min(position, len(replacement.text())))
