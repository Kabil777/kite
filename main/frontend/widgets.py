"""Small Qt widgets for the launcher footer and inset, searchable Actions panel."""
from functools import lru_cache
from pathlib import Path
import unicodedata
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QIcon, QKeySequence, QPainter, QRawFont
from shiboken6 import isValid
from .shortcut_hint import KEY_SYMBOLS, ShortcutHint
from PySide6.QtWidgets import (QApplication, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSizePolicy, QStyledItemDelegate,
                               QStyle, QStyleOptionViewItem, QVBoxLayout, QWidget)


ICON_FONT = 'JetBrainsMono Nerd Font'


def pixel_font(family, size, weight=QFont.Weight.Normal):
    font = QFont(family)
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


@lru_cache(maxsize=256)
def glyph_font(glyph, size):
    """Use only a font that actually supplies the glyph; never display missing-glyph boxes."""
    if not glyph:
        return None
    families = QFontDatabase.families()
    candidates = ([ICON_FONT] if ICON_FONT in families else [])
    candidates += [family for family in families if family != ICON_FONT and ('nerd' in family.casefold() or family.endswith((' NF', ' NFM', ' NFP')))]
    if all(unicodedata.category(char) != 'Co' for char in glyph):
        candidates.append(QApplication.font().family())
    for family in candidates:
        font = pixel_font(family, size)
        raw = QRawFont.fromFont(font)
        if raw.isValid() and all(char.isspace() or raw.supportsCharacter(ord(char)) for char in glyph):
            return font
    return None


def paint_icon(painter, rect, glyph, name, size=19):
    font = glyph_font(glyph, size)
    if font is not None:
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
    else:
        themed_icon(name).paint(painter, rect)


def plugin_icon(identifier, glyph=''):
    # ponytail: one known mascot, not a new manifest/artwork registry.
    if identifier == 'gopass':
        return str(Path(__file__).resolve().parents[2] / 'assets/icons/gopass.png'), ''
    return 'application-x-executable', glyph or '\uf1b3'


def faded(color, opacity):
    value = QColor(color)
    value.setAlphaF(opacity)
    return value


def hex_color(text):
    color = QColor(text.strip())
    if len(text.strip()) == 9 and color.isValid():  # CSS #RRGGBBAA, not Qt's #AARRGGBB.
        value = text.strip()[1:]
        color.setRgb(int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16), int(value[6:], 16))
    return color


def themed_icon(name, fallback='application-x-executable'):
    icon = QIcon(name) if name.startswith('/') else QIcon.fromTheme(name)
    # A theme entry can exist but point at a broken link or unsupported image.
    if icon.pixmap(32, 32).isNull():
        icon = QIcon.fromTheme(fallback)
    return icon if not icon.isNull() else QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)


class FooterButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        layout = self.layout()
        return layout.sizeHint() if layout else super().sizeHint()

    def minimumSizeHint(self):
        return self.sizeHint()


class ElidedLabel(QLabel):
    """Keep full accessible text/tooltips without pushing footer controls off screen."""
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.contentsRect(), self.alignment(),
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width()))


def key_hint(keys, family='Adwaita Sans', colors=None):
    """Compatibility factory for the reusable ShortcutHint component."""
    return ShortcutHint(keys, family, colors)


class TypeFilter(FooterButton):
    """Small in-panel selector; no native Wayland popup grab or platform menu chrome."""
    currentTextChanged = Signal(str)

    def __init__(self, parent, config, return_focus):
        super().__init__(parent)
        self.return_focus = return_focus
        self.setObjectName('clipFilter')
        self.setFixedHeight(34)
        self.setFont(pixel_font(config['results']['font_family'], 13, QFont.Weight.Medium))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(12)
        self.label = QLabel()
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.label, 1)
        font = glyph_font('\uf078', 10)
        arrow = QLabel('\uf078' if font is not None else '▾')
        arrow.setFont(font or pixel_font(config['results']['font_family'], 10))
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(arrow)
        self.popup = QFrame(parent)
        self.popup.setObjectName('clipFilterPopup')
        popup_layout = QVBoxLayout(self.popup)
        popup_layout.setContentsMargins(5, 5, 5, 5)
        self.options = QListWidget()
        self.options.setObjectName('clipFilterOptions')
        self.options.setFont(self.font())
        self.options.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.options.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.options.itemClicked.connect(lambda _: self.choose())
        self.options.installEventFilter(self)
        popup_layout.addWidget(self.options)
        self.popup.hide()
        self.clicked.connect(lambda: self.hidePopup() if self.popup.isVisible() else self.showPopup())
        app = QApplication.instance()
        assert app is not None
        app.installEventFilter(self)

    def addItems(self, items):
        for text in items:
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(0, 34))
            self.options.addItem(item)
        if not self.currentText() and items:
            self.setCurrentText(items[0])

    def currentText(self):
        return self.label.text()

    def setCurrentText(self, text):
        if text != self.currentText() and self.options.findItems(text, Qt.MatchFlag.MatchExactly):
            self.label.setText(text)
            self.setAccessibleName('Filter: ' + text)
            self.currentTextChanged.emit(text)

    def view(self):
        return self.options

    def showPopup(self):
        parent = self.parentWidget()
        assert parent is not None
        point = self.mapTo(parent, QPoint(self.width(), self.height() + 6))
        self.popup.resize(196, self.options.count() * 34 + 12)
        self.popup.move(max(8, min(point.x() - self.popup.width(), parent.width() - self.popup.width() - 8)), point.y())
        self.options.setCurrentRow(next((i for i in range(self.options.count())
                                        if self.options.item(i).text() == self.currentText()), 0))
        self.popup.show()
        self.popup.raise_()
        self.options.setFocus()

    def hidePopup(self):
        if self.popup.isVisible():
            self.popup.hide()
            self.return_focus.setFocus()

    def choose(self):
        item = self.options.currentItem()
        if item:
            self.setCurrentText(item.text())
        self.hidePopup()

    def eventFilter(self, watched, event):
        if not isValid(self.popup):
            return False
        if self.popup.isVisible():
            if watched is self.options and event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self.choose()
                    return True
                if event.key() == Qt.Key.Key_Escape:
                    self.hidePopup()
                    return True
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
                if (watched is not self and not self.isAncestorOf(watched)
                        and watched is not self.popup and not self.popup.isAncestorOf(watched)):
                    self.hidePopup()
            if watched is self.window() and event.type() == QEvent.Type.WindowDeactivate:
                self.hidePopup()
        return super().eventFilter(watched, event)


class ActionDelegate(QStyledItemDelegate):
    def __init__(self, config, parent):
        super().__init__(parent)
        self.cfg = config
        self.view = parent

    def paint(self, painter, option, index):
        action = index.data(Qt.ItemDataRole.UserRole)
        if action is None:
            return super().paint(painter, option, index)
        rect = option.rect
        colors = self.cfg['colors']
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if action.isSeparator():
            painter.setPen(faded(colors['text'], .1))
            painter.drawLine(rect.left() + 8, rect.center().y(), rect.right() - 8, rect.center().y())
        else:
            styled = QStyleOptionViewItem(option)
            self.initStyleOption(styled, index)
            styled.text, styled.icon = '', QIcon()
            styled.state &= ~QStyle.StateFlag.State_HasFocus
            self.view.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, self.view)
            painter.setPen(faded(colors['text'], .75))
            glyph = action.property('glyph') or ''
            if glyph:
                fallback = {'\uf013': 'preferences-system', '\uf021': 'view-refresh', '\uf1f8': 'edit-delete',
                            '\uf060': 'go-previous', '\uf019': 'folder-download', '\uf02d': 'help-contents',
                            '\uf05e': 'process-stop', '\uf0c5': 'edit-copy', '\uf059': 'help-browser',
                            '\uf062': 'go-up', '\uf063': 'go-down', '★': 'starred'}.get(glyph, 'application-x-executable')
                paint_icon(painter, QRect(rect.x() + 10, rect.y(), 18, rect.height()), glyph, fallback, 15)
            right = ShortcutHint.paint(painter, rect.right() - 8, rect.center().y(),
                                       action.property('keys') or '', self.cfg['results']['font_family'], colors)
            painter.setFont(pixel_font(self.cfg['results']['font_family'], 14, QFont.Weight.Medium))
            painter.setPen(QColor(colors['text']))
            title_right = right - 8
            title_box = QRect(rect.x() + 36, rect.y(), max(0, title_right - rect.x() - 36), rect.height())
            title = painter.fontMetrics().elidedText(action.text(), Qt.TextElideMode.ElideRight, title_box.width())
            painter.drawText(title_box, Qt.AlignmentFlag.AlignVCenter, title)
        painter.restore()


class ActionPanel(QFrame):
    """Child panel, not a Wayland popup window; works without a compositor input grab."""
    def __init__(self, parent, config, return_focus, toggle_button):
        super().__init__(parent)
        self.cfg, self.return_focus, self.toggle_button = config, return_focus, toggle_button
        self.setObjectName('actionPanel')
        self.setAccessibleName('Actions for selected result')
        self.setFont(pixel_font(config['results']['font_family'], 14, QFont.Weight.Medium))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 0)
        layout.setSpacing(0)
        self.list = QListWidget()
        self.list.setObjectName('actionList')
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setItemDelegate(ActionDelegate(config, self.list))
        self.list.setMouseTracking(True)
        self.list.itemEntered.connect(self.list.setCurrentItem)
        self.list.itemClicked.connect(lambda _: self.activate_selected())
        layout.addWidget(self.list, 1)
        layout.addSpacing(6)
        separator = QFrame()
        separator.setObjectName('actionDivider')
        separator.setFixedHeight(1)
        layout.addWidget(separator)
        searchbar = QWidget()
        searchbar.setFixedHeight(38)
        search_layout = QHBoxLayout(searchbar)
        search_layout.setContentsMargins(8, 0, 8, 0)
        self.search = QLineEdit()
        self.search.setObjectName('actionSearch')
        self.search.setAccessibleName('Filter actions')
        self.search.setPlaceholderText('Search for actions…')
        self.search.setTextMargins(-2, 0, -2, 0)
        palette = self.search.palette()
        palette.setColor(palette.ColorRole.PlaceholderText, faded(config['colors']['text'], .45))
        self.search.setPalette(palette)
        self.search.setMaxLength(100)
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.activate_selected)
        self.search.installEventFilter(self)
        search_layout.addWidget(self.search)
        # Oma-Cast's action filter is text-only; Escape lives in keyboard handling.
        layout.addWidget(searchbar)
        app = QApplication.instance()
        assert app is not None
        app.installEventFilter(self)
        self.hide()

    def clear(self):
        for action in self.actions():
            self.removeAction(action)
            action.deleteLater()
        self.search.clear()
        self.list.clear()

    def add_action(self, title, callback, glyph='\uf0c5', keys='', bind_shortcut=False):
        action = QAction(title, self)
        action.setProperty('glyph', glyph)
        action.setProperty('keys', keys)
        if bind_shortcut:
            action.setShortcut(QKeySequence(keys))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.triggered.connect(self.close)
        action.triggered.connect(callback)
        self.addAction(action)
        return action

    def addSeparator(self):
        action = QAction(self)
        action.setSeparator(True)
        self.addAction(action)

    def refresh(self):
        self.list.clear()
        query = self.search.text().strip().casefold()
        visible = [a for a in self.actions() if a.isSeparator() or query in a.text().casefold()]
        total, pending_separator = 0, None
        for action in visible:
            if action.isSeparator():
                pending_separator = action if self.list.count() > 0 else None
                continue
            if pending_separator:
                separator = QListWidgetItem()
                separator.setFlags(Qt.ItemFlag.NoItemFlags)
                separator.setData(Qt.ItemDataRole.UserRole, pending_separator)
                separator.setSizeHint(QSize(0, 9))
                self.list.addItem(separator)
                total += 9
                pending_separator = None
            item = QListWidgetItem(action.text())
            if not action.isEnabled():
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setData(Qt.ItemDataRole.UserRole, action)
            item.setSizeHint(QSize(0, 38))
            self.list.addItem(item)
            total += 38
        if not self.list.count():
            item = QListWidgetItem('No matching actions')
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            item.setSizeHint(QSize(0, 38))
            self.list.addItem(item)
            total = 38
        self.list.setCurrentRow(next((index for index in range(self.list.count())
                                     if self.list.item(index).flags() & Qt.ItemFlag.ItemIsSelectable), -1))
        parent = self.parentWidget()
        assert parent is not None
        self.resize(min(340, parent.width() - 24),
                    min(360, max(96, parent.height() - self.cfg['search']['height'] - self.cfg['footer']['height'] - 20), total + 54))
        self.move(parent.width() - self.width() - 12,
                  parent.height() - self.cfg['footer']['height'] - self.height() - 8)

    def popup(self):
        self.search.clear()
        self.refresh()
        self.show()
        self.raise_()
        self.search.setFocus()

    def close(self):
        self.hide()
        self.return_focus.setFocus()
        return True

    def activate_selected(self):
        item = self.list.currentItem()
        action = item.data(Qt.ItemDataRole.UserRole) if item else None
        if action and not action.isSeparator():
            self.close()
            action.trigger()

    def eventFilter(self, watched, event):
        if self.isVisible():
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
                if (watched is not self and not self.isAncestorOf(watched)
                        and watched is not self.toggle_button and not self.toggle_button.isAncestorOf(watched)):
                    self.close()
            if watched is self.window() and event.type() == QEvent.Type.WindowDeactivate:
                self.close()
            if watched is self.search and event.type() == QEvent.Type.KeyPress:
                key = event.key()
                control = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) or control and key in (Qt.Key.Key_N, Qt.Key.Key_P):
                    direction = 1 if key in (Qt.Key.Key_Down, Qt.Key.Key_N) else -1
                    index = self.list.currentRow()
                    for _ in range(self.list.count()):
                        index = (index + direction) % self.list.count()
                        if self.list.item(index).flags() & Qt.ItemFlag.ItemIsSelectable:
                            self.list.setCurrentRow(index)
                            break
                    return True
        return super().eventFilter(watched, event)
