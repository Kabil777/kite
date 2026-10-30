"""Qt frontend. Providers own data; one result/action protocol drives every view."""
import argparse
import base64
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sqlite3
import math
import threading
import unicodedata
from dataclasses import asdict
from urllib.parse import urlencode, urlparse

from PySide6.QtCore import (QEvent, QLockFile, QObject, QProcess, QRect, QSize, Qt, QTimer, QUrl)
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QKeySequence, QPainter, QShortcut, QPixmap)
from PySide6.QtNetwork import (QLocalServer, QLocalSocket, QNetworkAccessManager, QNetworkReply, QNetworkRequest)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QProgressBar, QPushButton, QStyledItemDelegate, QStyle,
                               QStyleOptionViewItem, QVBoxLayout, QPlainTextEdit, QStackedWidget, QSizePolicy,
                               QWidget)

from .core import APP_ROOT, CONFIG, DATA, STATE, Result, load_config, math_like, rank, suggestions
from .ranking import record_visit
from .root_ranking import RootRanking
from .favorites import FavoritesStore
from .applications import Applications
from .frontend.widgets import (ActionPanel, ElidedLabel, FooterButton, faded,
                      TypeFilter, glyph_font, hex_color, key_hint, paint_icon, pixel_font, plugin_icon, themed_icon)
from .dmenu import options_from_bytes
from .plugin_api import Event as PluginEvent, Result as PluginResult, Toast, _canonical_shortcut, load_plugins, validate_toast, validate_view
from .plugin_manager import Manager as PluginManager, UseLease, mutation_lock
from .frontend import Form, ImagePreview, MarkdownView, ToastBanner, TypeLabel, clear_layout, input_values, render_nodes
from .plugin_runtime import Call as PluginCall, Runtime as PluginRuntime
from .host_services import context as plugin_context, plugin_row, rows as plugin_rows


def plugin_command(identifier, manifest):
    icon, glyph = plugin_icon(identifier, manifest.get('glyph', ''))
    return Result('plugin:' + identifier, manifest['name'], 'Plugin', icon, 'plugin', identifier, glyph=glyph)


class Jobs(QObject):
    """Newest job per provider wins; output, runtime and lifetime are bounded."""
    def __init__(self, parent, copy_command: str | list[str] = 'auto'):
        super().__init__(parent)
        self.copy_command = copy_command
        self.running = {}
        self.retiring = {}

    def plugin_services_idle(self):
        return not any(key.startswith('plugin-service:') for key in (*self.running, *self.retiring.values()))

    def cancel(self, key):
        process = self.running.pop(key, None)
        if process:
            self.retiring[process] = key
            process.kill()

    def start(self, key, argv, request, callback, timeout=2500, cwd=None):
        self.cancel(key)
        process = QProcess(self)
        self.running[key] = process
        timer = QTimer(process)
        timer.setSingleShot(True)
        output = bytearray()

        def done(payload):
            timer.stop()
            if self.running.get(key) is process:
                self.running.pop(key)
                if process.state() != QProcess.ProcessState.NotRunning:
                    self.retiring[process] = key
                callback(payload)

        def read():
            output.extend(process.readAllStandardOutput().data())
            process.readAllStandardError()
            limit = 2 * 1024 * 1024 if key in ('apps', 'clip-preview') or key.startswith('plugin-service:') else 262144
            if len(output) > limit:
                done({'error': f'Provider output exceeded {limit // 1024} KiB'})
                process.kill()

        def finished(*_):
            self.retiring.pop(process, None)
            read()
            try:
                payload = json.loads(output)
                if not isinstance(payload, dict):
                    raise ValueError('Expected a JSON object')
            except (ValueError, UnicodeError):
                payload = {'error': 'Provider failed or returned invalid JSON'}
            done(payload)
            process.deleteLater()

        def expired():
            done({'error': 'Provider timed out'})
            process.kill()

        process.readyReadStandardOutput.connect(read)
        process.readyReadStandardError.connect(read)
        process.finished.connect(finished)
        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                self.retiring.pop(process, None)
                done({'error': process.errorString()})
                process.deleteLater()
        process.errorOccurred.connect(failed)
        process.started.connect(lambda: (process.write(json.dumps(request).encode() + b'\n'),
                                         process.closeWriteChannel()))
        timer.timeout.connect(expired)
        if cwd is not None:
            process.setWorkingDirectory(str(cwd))
        process.start(argv[0], argv[1:])
        timer.start(timeout)

    def builtin(self, key, request, callback, timeout=2500):
        if request.get('kind') in ('copy', 'clip'):
            request = {**request, 'copy_command': self.copy_command}
        argv = ([sys.executable, '-B', '-m', 'main.plugin_worker', '--service'] if key.startswith('plugin-service:')
                else [sys.executable, '-m', 'main.core'])
        self.start(key, argv, request, callback, timeout, cwd=APP_ROOT)

    def stop(self):
        processes = list(self.running.values()) + list(self.retiring)
        for key in list(self.running):
            self.cancel(key)
        # Reap killed children before Qt destroys their signal targets on shutdown.
        for process in processes:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.waitForFinished(200)


class ResultDelegate(QStyledItemDelegate):
    def __init__(self, config, parent: QListWidget):
        super().__init__(parent)
        self.cfg = config
        self.view = parent

    def title_font(self, row):
        size = self.cfg['web']['suggestion_font_size'] if row.id.startswith('web:suggestion:') else self.cfg['results']['font_size']
        return pixel_font(self.cfg['results']['font_family'], size, QFont.Weight.DemiBold)

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole + 1)
        if row is None:
            if index.data(Qt.ItemDataRole.UserRole + 2):
                painter.save()
                painter.setFont(pixel_font(self.cfg['results']['font_family'], 12, QFont.Weight.DemiBold))
                painter.setPen(faded(self.cfg['colors']['text'], .5))
                painter.drawText(option.rect.adjusted(12, 0, -12, -6), Qt.AlignmentFlag.AlignBottom,
                                 str(index.data(Qt.ItemDataRole.DisplayRole)))
                painter.restore()
                return
            return super().paint(painter, option, index)
        if row.id.startswith('emoji:') and self.view.viewMode() == QListWidget.ViewMode.IconMode:
            painter.save()
            painter.setClipRect(option.rect)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            tile = option.rect.adjusted(4, 4, -4, -4)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            colors = self.cfg['colors']
            painter.setPen(QColor(colors['text']) if selected else Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors['selected' if selected else 'search']))
            painter.drawRoundedRect(tile, 8, 8)
            painter.setFont(pixel_font('Noto Color Emoji', max(12, min(self.cfg['emoji']['font_size'], tile.width() - 20))))
            painter.setPen(QColor(colors['text']))
            painter.drawText(tile, Qt.AlignmentFlag.AlignCenter, str(row.value))
            painter.restore()
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.text, styled.icon = '', QIcon()
        styled.state &= ~QStyle.StateFlag.State_HasFocus
        self.view.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, self.view)
        painter.save()
        rect = option.rect
        size, padding = self.cfg['icons']['size'], self.cfg['results']['padding']
        colors = self.cfg['colors']
        if row.id == 'math':
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen if selected else faded(colors['text'], .08))
            painter.setBrush(QColor(colors['selected']) if selected else faded(colors['text'], .04))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
            half, column = rect.width() // 2, rect.width() // 2 - 20
            approx = row.subtitle.removesuffix(row.expression).removesuffix(' · ') if row.expression else ''
            for offset, text, caption in [(0, row.expression, 'Question'),
                                          (half + 20, row.title, '≈ ' + approx if approx else 'Answer')]:
                font = pixel_font(self.cfg['results']['font_family'], 24,
                                  QFont.Weight.DemiBold if offset else QFont.Weight.Medium)
                painter.setFont(font)
                while painter.fontMetrics().horizontalAdvance(text) > column - 24 and font.pixelSize() > 14:
                    font.setPixelSize(font.pixelSize() - 1)
                    painter.setFont(font)
                box = QRect(rect.x() + offset + 12, rect.center().y() - 10 - painter.fontMetrics().height() // 2,
                            column - 24, painter.fontMetrics().height())
                painter.setPen(QColor(colors['accent' if offset and selected else 'text']))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter,
                                 painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, box.width()))
                painter.setFont(pixel_font(self.cfg['results']['font_family'], 11, QFont.Weight.Medium))
                caption = painter.fontMetrics().elidedText(caption, Qt.TextElideMode.ElideRight, column - 40)
                width, height = painter.fontMetrics().horizontalAdvance(caption) + 16, painter.fontMetrics().height() + 6
                pill = QRect(rect.x() + offset + (column - width) // 2, box.bottom() + 8, width, height)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(faded(colors['text'], .08))
                painter.drawRoundedRect(pill, height / 2, height / 2)
                painter.setPen(faded(colors['text'], .7))
                painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, caption)
            arrow = QRect(rect.center().x() - 14, rect.center().y() - 14, 28, 28)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(faded(colors['text'], .08))
            painter.drawEllipse(arrow)
            painter.setPen(faded(colors['text'], .7))
            painter.setFont(pixel_font(self.cfg['results']['font_family'], 12))
            painter.drawText(arrow, Qt.AlignmentFlag.AlignCenter, '→')
            painter.restore()
            return
        if row.kind == 'clip':
            painter.setClipRect(rect)
            icon_rect = QRect(rect.x() + 10, rect.center().y() - 10, 20, 20)
            if row.subtitle == 'Color':
                color = hex_color(row.title)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(color)
                painter.setPen(color.darker(120))
                painter.drawEllipse(icon_rect.adjusted(1, 1, -1, -1))
            elif any(unicodedata.category(char) == 'So' for char in row.title):
                painter.setFont(pixel_font('Noto Color Emoji', 18))
                painter.setPen(QColor(colors['text']))
                painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, row.title)
            else:
                painter.setPen(QColor(colors['text']))
                paint_icon(painter, icon_rect, row.glyph, row.icon or 'text-x-generic', 18)
            painter.setFont(self.title_font(row))
            painter.setPen(QColor(colors['text']))
            box = rect.adjusted(40, 0, -8, 0)
            painter.drawText(box, Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(row.title.replace('\n', ' '), Qt.TextElideMode.ElideRight, box.width()))
            painter.restore()
            return
        icon_rect = QRect(rect.x() + padding, rect.y() + (rect.height() - size) // 2, size, size)
        if row.id.startswith('emoji:'):
            painter.setFont(pixel_font('Noto Color Emoji', 17))
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, str(row.value))
        else:
            painter.setPen(QColor(colors['text']))
            paint_icon(painter, icon_rect, row.glyph, row.icon or 'application-x-executable')
        x = icon_rect.x() + size + 10
        label = {'app': 'Application', 'mode': 'Plugin', 'plugin': 'Plugin', 'web': 'Web Search',
                 'copy': 'Copy', 'clip': 'Clipboard', 'directory': 'Folder',
                 'focus': 'Window', 'open': 'Open', 'exec': 'Command'}.get(row.kind, '')
        if row.id.startswith('web:'):
            label = 'Web Search'
        alias = next((a['alias'] for a in self.cfg['aliases'] if a['id'] == row.id), '')
        right = rect.right() - 12
        label_width = TypeLabel.paint(painter, QRect(x, rect.y(), max(0, right - x), rect.height()),
                                      row.tag or label, self.cfg)
        right -= label_width + 10
        if row.id in self.cfg['favorites']:
            themed_icon('starred', 'emblem-favorite').paint(
                painter, QRect(right - 16, rect.center().y() - 7, 14, 14))
            right -= 24
        if alias:
            painter.setFont(pixel_font(self.cfg['results']['font_family'], 11, QFont.Weight.Medium))
            alias = painter.fontMetrics().elidedText(alias, Qt.TextElideMode.ElideRight, 140)
            alias_width, alias_height = painter.fontMetrics().horizontalAdvance(alias) + 12, painter.fontMetrics().height() + 4
            box = QRect(right - alias_width, rect.center().y() - alias_height // 2, alias_width, alias_height)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(faded(colors['text'], .3))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(faded(colors['text'], .7))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, alias)
            right -= alias_width + 10
        width = max(0, right - x)
        if self.cfg['results']['layout'] == 'inline':
            painter.setFont(self.title_font(row))
            painter.setPen(QColor(colors['text']))
            title = painter.fontMetrics().elidedText(row.title.replace('\n', ' '), Qt.TextElideMode.ElideRight, width)
            painter.drawText(QRect(x, rect.y(), width, rect.height()), Qt.AlignmentFlag.AlignVCenter, title)
            title_width = painter.fontMetrics().horizontalAdvance(title) + 8
            painter.setFont(pixel_font(self.cfg['results']['font_family'], 14, QFont.Weight.Medium))
            painter.setPen(faded(colors['text'], .5))
            subtitle = '' if row.subtitle == label else row.subtitle.replace('\n', ' ')
            subtitle = painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, max(0, width - title_width))
            painter.drawText(QRect(x + title_width, rect.y(), max(0, width - title_width), rect.height()),
                             Qt.AlignmentFlag.AlignVCenter, subtitle)
            painter.restore()
            return
        painter.setFont(self.title_font(row))
        painter.setPen(QColor(colors['text']))
        title = painter.fontMetrics().elidedText(row.title.replace('\n', ' '), Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRect(x, rect.y() + 6, width, rect.height() // 2),
                         Qt.AlignmentFlag.AlignVCenter, title)
        painter.setFont(pixel_font(self.cfg['results']['font_family'], 14, QFont.Weight.Medium))
        painter.setPen(QColor(self.cfg['colors']['muted']))
        subtitle = painter.fontMetrics().elidedText(row.subtitle.replace('\n', ' '), Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRect(x, rect.y() + rect.height() // 2, width, rect.height() // 2 - 4),
                         Qt.AlignmentFlag.AlignVCenter, subtitle)
        painter.restore()


class Launcher(QWidget):
    def __init__(self, config, mode='root', config_path=CONFIG, favorites_path=None):
        super().__init__()
        self.cfg, self.mode, self.config_path = config, mode, config_path
        try:
            self.favorites_store = FavoritesStore(favorites_path, config['favorites'])
            self.cfg['favorites'] = self.favorites_store.ids()
        except (OSError, sqlite3.Error) as error:
            self.favorites_store = None
            print('Favorites unavailable; using read-only YAML list:', error, file=sys.stderr)
        self.setFont(pixel_font(config['results']['font_family'], config['results']['font_size'], QFont.Weight.Medium))
        self.apps_loading, self.mode_loading, self.mode_error = True, False, ''
        self.emoji_columns = config['emoji']['columns']
        self.revision = 0
        self.apps, self.rows, self.extra, self.mode_rows = [], [], {}, []
        self.applications = Applications(self)
        cached_apps = self.applications.load() if mode != 'dmenu' else None
        self.apps_cached = cached_apps is not None
        if cached_apps is not None:
            self.apps, self.apps_loading = cached_apps, False
        self.apps_refreshing = self.apps_dirty = False
        self.apps_refresh_timer = QTimer(self)
        self.apps_refresh_timer.setSingleShot(True)
        self.apps_refresh_timer.setInterval(200)
        self.apps_refresh_timer.timeout.connect(self.refresh_apps)
        self.applications.changed.connect(self.apps_refresh_timer.start)
        self.directory = Path.home()
        self.reply = None
        self.web_cache = {}
        self.bundled_plugins = {manifest['id']: module for manifest, module in load_plugins(DATA / 'bundled_plugins')}
        self.catalogs = {name: module.presentation(config) for name, module in self.bundled_plugins.items()
                         if name != 'ranking'}
        self.marketplace = None
        self.package_manager = PluginManager(config_path)
        self.package_lease = None
        self.plugin_errors = []
        try:
            with mutation_lock(self.package_manager.store):
                self.package_lease = UseLease(self.package_manager.store)
            if self.package_manager._has_journal():
                self.package_manager.recover(self.package_lease)
            self.read_plugins()
        except (OSError, ValueError, RuntimeError) as error:
            self.plugins = {}
            self.plugin_errors.append(str(error)[:240])
        self.plugin_views = {}
        self.plugin_help_id = None
        self.plugin_shortcuts = []
        self.navigation_stack = []
        self.navigation_selection = None
        self.pending_leave = None
        self.plugin_tokens, self.plugin_calls = {}, {}
        self.jobs = Jobs(self, config['clipboard']['copy_command'])
        self.closing_workers = False
        self.plugin_runtime = PluginRuntime(self, self.jobs, self.cfg, self.plugin_call_is_current, self.package_manager)
        self.plugin_runtime.idle.connect(self.plugin_workers_idle)
        self.root_ranking = RootRanking(self)
        self.network = QNetworkAccessManager(self)
        self.usage = {}
        self.history_writable = True
        try:
            data = json.loads(STATE.read_text())
            if not isinstance(data, dict):
                raise ValueError('Usage history must be an object')
            self.usage = {key: val for key, val in data.items()
                          if isinstance(val, dict) and type(val.get('count')) is int and val['count'] >= 0
                          and type(val.get('last')) in (int, float) and math.isfinite(val['last'])
                          and 0 <= val['last'] <= 1e12
                          and ('anchor' not in val or type(val['anchor']) in (int, float) and math.isfinite(val['anchor']))
                          and ('terms' not in val or isinstance(val['terms'], list)
                               and all(isinstance(t, str) for t in val['terms']))}
            if len(self.usage) != len(data):
                raise ValueError('Invalid usage history record')
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as error:
            self.history_writable = False
            print('Usage history preserved, writes disabled:', error, file=sys.stderr)
        self.setWindowTitle('KITE')
        self.setWindowIcon(QIcon(str(APP_ROOT / 'assets/icons/kite.png')))
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(min(config['window']['width'], screen.width() - 10),
                          min(config['window']['height'], screen.height() - 10))
        self.move(screen.x() + (screen.width() - self.width()) // 2,
                  screen.y() + max(5, min(screen.height() // 4, screen.height() - self.height() - 5)))
        icon_dirs = [Path.home() / '.icons', Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'icons']
        icon_dirs += [Path(directory) / 'icons' for directory in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')]
        QIcon.setThemeSearchPaths([str(path) for path in icon_dirs] + QIcon.themeSearchPaths())
        QIcon.setThemeName(config['icons']['theme'])
        QIcon.setFallbackThemeName('Adwaita')
        self.build_ui()
        self.toast = ToastBanner(self.cfg, self.panel)
        self.math_timer = QTimer(self)
        self.math_timer.setSingleShot(True)
        self.math_timer.timeout.connect(self.request_math)
        self.math_error_timer = QTimer(self)
        self.math_error_timer.setSingleShot(True)
        self.math_error_timer.timeout.connect(self.show_math_error)
        self.math_error = None
        self.last_math_error = None
        self.math_toast_visible = False
        self.web_timer = QTimer(self)
        self.web_timer.setSingleShot(True)
        self.web_timer.timeout.connect(self.request_web)
        self.extension_timer = QTimer(self)
        self.extension_timer.setSingleShot(True)
        self.extension_timer.timeout.connect(self.request_extensions)
        self.plugin_timer = QTimer(self)
        self.plugin_timer.setSingleShot(True)
        self.plugin_timer.timeout.connect(self.request_plugin)
        self.clip_preview_timer = QTimer(self)
        self.clip_preview_timer.setSingleShot(True)
        self.clip_preview_timer.timeout.connect(self.request_clip_preview)
        self.clip_preview_id = ''
        self.search.textChanged.connect(self.changed)
        self.search.returnPressed.connect(self.activate)
        self.search.installEventFilter(self)
        self.list.itemClicked.connect(self.result_clicked)
        self.list.itemDoubleClicked.connect(lambda item: self.activate() if self.mode in ('clipboard', 'marketplace')
                                            and item.flags() & Qt.ItemFlag.ItemIsSelectable else None)
        self.shortcuts = []
        shortcut_specs = [('Escape', self.back), ('Ctrl+K', self.show_actions),
                          ('Ctrl+?', self.toggle_help), ('Ctrl+,', self.open_config),
                          ('Ctrl+L', self.web_mode), ('Ctrl+Return', self.search_current_web)]
        for key, callback in shortcut_specs:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self.shortcuts.append(shortcut)
        copy_preview = QShortcut(QKeySequence('Return'), self.clip_text)
        copy_preview.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        copy_preview.activated.connect(self.activate)
        self.shortcuts.append(copy_preview)
        self.commands = [
            Result('cmd:emoji', 'Emoji', 'Search symbols and emoji', 'face-smile', 'mode', 'emoji', 'smile emoticons', glyph='\uf118'),
            Result('cmd:clipboard', 'Clipboard history', '', 'edit-paste', 'mode', 'clipboard', 'paste copy', glyph='\uf0ea'),
            Result('cmd:math', 'Calculator', 'Or type math directly in search', 'accessories-calculator', 'mode', 'calc', 'math numbers', glyph='\uf1ec'),
            Result('cmd:web', 'Search web', 'Google autocomplete · Ctrl+L', 'web-browser', 'mode', 'web', 'google internet', glyph='\uf1a0'),
            Result('cmd:files', 'Files', 'Browse your home folder', 'system-file-manager', 'mode', 'files', 'folder documents', glyph='\uf07b'),
            Result('cmd:windows', 'Switch windows', 'Focus an open window', 'preferences-system-windows', 'mode', 'windows', 'focus app', glyph='\uf24d'),
            Result('cmd:marketplace', 'Plugin marketplace', 'Browse and manage trusted plugins', 'application-x-addon', 'marketplace', '', 'extensions install update disable uninstall', glyph='\uf1b3'),
        ]
        self.commands.extend(plugin_command(plugin_id, manifest)
                             for plugin_id, (manifest, _) in self.plugins.items() if manifest.get('role') != 'ranking')
        if mode != 'dmenu':
            self.refresh_apps()
            QTimer.singleShot(0, self.applications.start)
        else:
            self.apps_loading = False
        self.set_mode(mode)
        if self.plugin_errors:
            self.show_toast(Toast('Some plugins unavailable', '\n'.join(self.plugin_errors), 'error'))

    def open_marketplace(self):
        self.set_mode('marketplace')

    def read_plugins(self):
        self.plugin_errors = []
        self.plugins = {manifest['id']: (manifest, directory) for manifest, directory in self.package_manager.discover(
            self.cfg['plugins'], lambda name, error: self.plugin_errors.append(f'{name}: {error}'[:240])
            if len(self.plugin_errors) < 3 else None)}

    def refresh_plugins(self, identifier=None):
        self.root_ranking.refresh()
        self.plugin_runtime.deactivate()
        changed = {identifier} if identifier else set(self.plugins)
        if self.mode.startswith('plugin:'):
            self.revision += 1
        for token in self.plugin_tokens.values():
            token.set()
        self.plugin_tokens.clear()
        self.plugin_calls.clear()
        try:
            self.read_plugins()
        except (OSError, ValueError, RuntimeError) as error:
            self.plugins = {}
            self.plugin_errors = [str(error)[:240]]
        self.commands = [row for row in self.commands if row.kind != 'plugin']
        self.commands.extend(plugin_command(key, manifest) for key, (manifest, _) in self.plugins.items()
                             if manifest.get('role') != 'ranking')
        for key in changed:
            self.plugin_views.pop(key, None)
            self.extra.pop('plugin:' + key, None)
        self.navigation_stack = [state for state in self.navigation_stack if state[0] not in {'plugin:' + key for key in changed}]
        if self.mode in {'plugin:' + key for key in changed}:
            self.clear_plugin_shortcuts()
            self.set_mode('root', record=False)
        elif self.mode == 'root':
            self.render()
        if self.plugin_errors:
            self.show_toast(Toast('Some plugins unavailable', '\n'.join(self.plugin_errors)[:512], 'error'))

    def plugin_call_is_current(self, call):
        if call.method == 'rank':
            return self.root_ranking.current(call)
        current = self.plugin_calls.get(call.plugin_id)
        return (self.mode == 'plugin:' + call.plugin_id and self.revision == call.revision
                and current is not None and current[0] is call and not call.cancelled.is_set())

    def plugin_workers_idle(self):
        if self.closing_workers:
            QTimer.singleShot(0, self.close)

    def build_ui(self):
        c = self.cfg['colors']
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QFrame()
        self.panel.setObjectName('panel')
        outer.addWidget(self.panel)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.bar = QFrame()
        self.bar.setObjectName('searchbar')
        self.bar.setFixedHeight(self.cfg['search']['height'])
        bar = QHBoxLayout(self.bar)
        padding = self.cfg['search']['padding']
        bar.setContentsMargins(padding, 0, padding + 2, 0)
        bar.setSpacing(12)
        self.lead = QPushButton('\uf002')
        self.lead.setObjectName('searchLead')
        self.lead.setFixedSize(28, 28)
        self.lead.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lead.clicked.connect(lambda: self.back() if self.mode != 'root' else None)
        bar.addWidget(self.lead)
        self.search = QLineEdit()
        self.search.setAccessibleName('Search apps, commands, math or web')
        self.search.setFont(pixel_font(self.cfg['search']['font_family'], self.cfg['search']['font_size'], QFont.Weight.Medium))
        self.search.setTextMargins(-2, 0, -2, 0)  # Cancel Qt's native inset; match QML text origin.
        self.search.setMaxLength(512)
        self.search.setClearButtonEnabled(False)
        bar.addWidget(self.search, 1)
        self.clip_filter = TypeFilter(self.panel, self.cfg, self.search)
        self.clip_filter.addItems(self.catalogs['clipboard'].filters)
        self.clip_filter.setFixedWidth(160)
        self.clip_filter.setAccessibleName('Filter clipboard content type')
        self.clip_filter.hide()
        self.clip_filter.currentTextChanged.connect(self.catalog_filter_changed)
        bar.addWidget(self.clip_filter)
        self.loading_bar = QProgressBar(self.bar)
        self.loading_bar.setObjectName('loadingBar')
        self.loading_bar.setAccessibleName('Loading results')
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.hide()
        self.bar.installEventFilter(self)
        layout.addWidget(self.bar)
        self.results_heading = QLabel('Results')
        self.results_heading.setObjectName('resultsHeading')
        self.results_heading.setFixedHeight(28)
        self.results_heading.setContentsMargins(self.cfg['window']['padding'] + 12, 0, 0, 0)
        self.results_heading.hide()
        layout.addWidget(self.results_heading)
        self.list = QListWidget()
        self.list.setAccessibleName('Search results')
        self.list.setItemDelegate(ResultDelegate(self.cfg, self.list))
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.empty_hint = QLabel(self.list.viewport())
        self.empty_hint.setObjectName('emptyHint')
        self.empty_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty_hint.hide()
        self.list.viewport().installEventFilter(self)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self.clip_selection_changed)
        self.list.currentItemChanged.connect(lambda *_: self.marketplace.show_details()
                                            if self.mode == 'marketplace' and self.marketplace else None)
        self.clip_type = QLabel('')
        self.clip_type.setObjectName('clipType')
        self.clip_preview = ImagePreview(c)
        self.clip_preview.setText('Select clipboard item')
        self.clip_preview.setObjectName('clipPreview')
        self.clip_text = QPlainTextEdit()
        self.clip_text.setObjectName('clipText')
        self.clip_text.setReadOnly(True)
        self.clip_text.document().setDocumentMargin(14)
        self.clip_text.setAccessibleName('Clipboard content preview')
        self.clip_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.clip_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.clip_stack = QStackedWidget()
        self.clip_stack.addWidget(self.clip_preview)
        self.clip_stack.addWidget(self.clip_text)
        self.clip_details = QWidget()
        self.clip_details.setObjectName('clipDetails')
        metadata_layout = QVBoxLayout(self.clip_details)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(0)
        self.clip_detail_values, self.clip_detail_rows = {}, {}
        for name in ('Content Type', 'Size', 'Dimensions'):
            row = QFrame()
            row.setObjectName('clipMetaRow')
            row.setFixedHeight(28)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(12)
            label = QLabel(name)
            label.setObjectName('clipMetaKey')
            value = ElidedLabel()
            value.setObjectName('clipMetaValue')
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(label, 1)
            row_layout.addWidget(value, 1)
            self.clip_detail_values[name], self.clip_detail_rows[name] = value, row
            metadata_layout.addWidget(row)
        self.clip_detail_panel = QFrame()
        self.clip_detail_panel.setObjectName('clipDetailPanel')
        detail_layout = QVBoxLayout(self.clip_detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(0)
        detail_layout.addWidget(self.clip_stack, 3)
        information = QFrame()
        self.clip_information = information
        information.setObjectName('clipInformation')
        information.hide()
        info_layout = QVBoxLayout(information)
        info_layout.setContentsMargins(16, 10, 16, 10)
        info_layout.setSpacing(6)
        info_layout.addWidget(self.clip_type)
        info_layout.addWidget(self.clip_details)
        detail_layout.addWidget(information)
        self.clip_detail_panel.hide()
        self.plugin_detail_panel = QFrame()
        self.plugin_detail_panel.setObjectName('pluginDetailPanel')
        self.plugin_detail_layout = QVBoxLayout(self.plugin_detail_panel)
        self.plugin_detail_layout.setContentsMargins(16, 12, 16, 12)
        self.plugin_detail_layout.setSpacing(8)
        self.plugin_detail_panel.hide()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setSpacing(self.cfg['results']['gap'])
        self.list.setIconSize(QSize(self.cfg['icons']['size'], self.cfg['icons']['size']))
        pad = self.cfg['window']['padding']
        self.list.setViewportMargins(pad, 4, pad, 4)
        self.list.setFont(pixel_font(self.cfg['results']['font_family'], self.cfg['results']['font_size']))
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.list, 3)
        content_layout.addWidget(self.clip_detail_panel, 5)
        content_layout.addWidget(self.plugin_detail_panel, 5)
        layout.addWidget(content, 1)
        self.footerbar = QFrame()
        self.footerbar.setObjectName('footerbar')
        self.footerbar.setFixedHeight(self.cfg['footer']['height'])
        self.footerbar.setVisible(self.cfg['footer']['visible'])
        footer_layout = QHBoxLayout(self.footerbar)
        footer_layout.setContentsMargins(16, 0, 14, 0)
        footer_layout.setSpacing(8)
        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(24, 28)
        self.brand_icon.setPixmap(QIcon(str(APP_ROOT / 'assets/icons/kite.png')).pixmap(24, 24))
        self.brand_icon.setAccessibleName('KITE')
        self.brand_icon.setToolTip('KITE — a native Qt launcher')
        footer_layout.addWidget(self.brand_icon)
        self.footer = ElidedLabel()
        self.footer.setObjectName('footer')
        self.footer.setTextFormat(Qt.TextFormat.PlainText)
        footer_layout.addWidget(self.footer, 1)
        self.primary_button = FooterButton()
        self.primary_button.setObjectName('footerButton')
        self.primary_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.primary_button.clicked.connect(self.activate)
        primary_layout = QHBoxLayout(self.primary_button)
        primary_layout.setContentsMargins(4, 4, 4, 4)
        primary_layout.setSpacing(8)
        self.primary_label = QLabel('Open')
        self.primary_label.setObjectName('primaryLabel')
        self.primary_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        primary_layout.addWidget(self.primary_label)
        primary_layout.addWidget(key_hint('Enter', self.cfg['results']['font_family'], c))
        footer_layout.addWidget(self.primary_button)
        divider = QFrame()
        divider.setObjectName('footerDivider')
        divider.setFixedSize(1, 14)
        footer_layout.addWidget(divider)
        self.actions_button = FooterButton()
        self.actions_button.setObjectName('footerButton')
        self.actions_button.setAccessibleName('Actions for selected result')
        self.actions_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.actions_button.clicked.connect(self.show_actions)
        action_layout = QHBoxLayout(self.actions_button)
        action_layout.setContentsMargins(4, 4, 4, 4)
        action_layout.setSpacing(8)
        action_label = QLabel('Actions')
        action_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        action_layout.addWidget(action_label)
        action_layout.addWidget(key_hint('Ctrl K', self.cfg['results']['font_family'], c))
        footer_layout.addWidget(self.actions_button)
        layout.addWidget(self.footerbar)
        self.action_menu = ActionPanel(self.panel, self.cfg, self.search, self.actions_button)
        fg = QColor(c['text'])
        rgb = f'{fg.red()}, {fg.green()}, {fg.blue()}'
        self.setStyleSheet(f'''
            QFrame#panel {{ background: {c['background']}; border: 2px solid {c['border']};
                border-radius: {self.cfg['window']['radius']}px; }}
            QFrame#searchbar {{ background: {c['background']}; border: none;
                border-bottom: 1px solid rgba({rgb}, 30);
                border-top-left-radius: {self.cfg['window']['radius']}px;
                border-top-right-radius: {self.cfg['window']['radius']}px; }}
            QLineEdit {{ background: transparent; color: {c['text']}; border: none;
                padding: 0; selection-background-color: {c['selected']}; }}
            QListWidget {{ background: transparent; color: {c['text']}; border: none; outline: none; }}
            QListWidget::item {{ border-radius: 6px; padding: 0 {self.cfg['results']['padding']}px; }}
            QListWidget::item:selected {{ background: {c['selected']}; color: {c['text']}; }}
            QListWidget::item:hover {{ background: {c['selected']}; }}
            QFrame#footerbar {{ border-top: 1px solid rgba({rgb}, 30); background: rgba({rgb}, 10);
                border-bottom-left-radius: {self.cfg['window']['radius']}px;
                border-bottom-right-radius: {self.cfg['window']['radius']}px; }}
            QLabel {{ color: {c['text']}; background: transparent; }}
            QLabel#footer {{ color: rgba({rgb}, 179); font-size: 12px; font-weight: 500; }}
            QLabel#emptyHint {{ color: {c['muted']}; font-size: 14px; font-weight: 500; }}
            QLabel#resultsHeading {{ color: rgba({rgb}, 128); font-size: 12px; font-weight: 600; }}
            QPushButton#searchLead {{ background: transparent; border: none; padding: 0; color: {c['muted']}; }}
            QPushButton#footerButton {{ background: transparent; border: none; border-radius: 4px;
                padding: 0; font-size: 12px; font-weight: 500; }}
            QPushButton#footerButton:hover {{ background: {c['selected']}; }}
            QPushButton#footerButton QLabel {{ font-size: 12px; font-weight: 500; color: rgba({rgb}, 210); }}
            QPushButton#footerButton QLabel#primaryLabel {{ color: {c['text']}; }}
            QFrame#footerDivider {{ background: rgba({rgb}, 51); border: none; }}
            QFrame#actionDivider {{ background: rgba({rgb}, 25); border: none; }}
            QFrame#actionPanel {{ background: {c['background']}; border: 1px solid rgba({rgb}, 38); border-radius: 8px; }}
            QListWidget#actionList {{ background: transparent; border: none; }}
            QListWidget#actionList::item {{ border-radius: 6px; padding: 0; }}
            QLineEdit#actionSearch {{ font-size: 14px; font-weight: 500; padding: 0; }}
            QPushButton#clipFilter {{ background: {c['background']}; border: 1px solid {c['border']}; border-radius: 6px; }}
            QPushButton#clipFilter:hover, QPushButton#clipFilter:focus {{ border-color: {c['muted']}; }}
            QFrame#clipFilterPopup {{ background: {c['background']}; border: 1px solid {c['border']}; border-radius: 8px; }}
            QListWidget#clipFilterOptions {{ background: transparent; border: none; padding: 0; outline: none; }}
            QListWidget#clipFilterOptions::item {{ padding: 0 10px; border-radius: 4px; }}
            QListWidget#clipFilterOptions::item:selected, QListWidget#clipFilterOptions::item:hover {{ background: {c['selected']}; color: {c['text']}; }}
            QFrame#clipDetailPanel {{ background: rgba({rgb}, 8); border-left: 1px solid rgba({rgb}, 30); }}
            QFrame#pluginDetailPanel {{ background: {c['background']}; border-left: 1px solid {c['border']}; }}
            QFrame#pluginHelpPanel {{ background: {c['background']}; border: none; }}
            QFrame#pluginHelpHeader {{ background: transparent; border: none; }}
            QFrame#pluginHelpRule {{ background: {c['border']}; border: none; }}
            QLabel#pluginHelpTitle {{ color: {c['text']}; font-family: '{MarkdownView.BODY_FONT}';
                font-size: 14px; font-weight: 600; }}
            QPushButton#pluginHelpClose {{ background: transparent; color: {c['muted']};
                font-family: '{MarkdownView.BODY_FONT}';
                border: none; border-radius: 0; font-size: 14px; font-weight: 500; padding: 0; }}
            QPushButton#pluginHelpClose:hover {{ color: {c['text']}; }}
            QFrame#pluginDetailPanel QComboBox {{
                background: {c['search']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}
            QFrame#clipInformation {{ background: rgba({rgb}, 8); border: none; }}
            QLabel#clipPreview {{ color: {c['muted']}; font-size: 14px; }}
            QLabel#clipType {{ color: {c['text']}; font-size: 13px; font-weight: 700; }}
            QFrame#clipMetaRow {{ border: none; }}
            QLabel#clipMetaKey {{ color: {c['muted']}; font-size: 13px; }}
            QLabel#clipMetaValue {{ color: {c['text']}; font-size: 13px; }}
            QPlainTextEdit#clipText {{ background: transparent; color: {c['text']}; border: none; font-size: 14px; }}
            QProgressBar#loadingBar {{ border: none; background: transparent; }}
            QProgressBar#loadingBar::chunk {{ background: {c['accent']}; width: 100px; }}
            QToolTip {{ color: {c['text']}; background: {c['search']}; border: 1px solid {c['border']}; }}
        ''')
        palette = self.search.palette()
        palette.setColor(palette.ColorRole.PlaceholderText, faded(c['text'], .45))
        self.search.setPalette(palette)
        self.plugin_help_panel = QFrame(self.panel)
        self.plugin_help_panel.setObjectName('pluginHelpPanel')
        help_layout = QVBoxLayout(self.plugin_help_panel)
        help_layout.setContentsMargins(12, 2, 12, 0)
        help_layout.setSpacing(0)
        header = QFrame()
        header.setObjectName('pluginHelpHeader')
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        self.plugin_help_title = QLabel()
        self.plugin_help_title.setObjectName('pluginHelpTitle')
        header_layout.addWidget(self.plugin_help_title, 1)
        close_help = QPushButton('How to')
        close_help.setObjectName('pluginHelpClose')
        close_help.setFixedSize(68, 56)
        close_help.setAccessibleName('Close plugin guide')
        close_help.setToolTip('Close guide · Esc')
        close_help.clicked.connect(self.close_plugin_help)
        header_layout.addWidget(close_help)
        help_layout.addWidget(header)
        rule = QFrame()
        rule.setObjectName('pluginHelpRule')
        rule.setFixedHeight(1)
        help_layout.addWidget(rule)
        self.plugin_help_browser = MarkdownView(c)
        self.plugin_help_browser.setObjectName('pluginHelpBrowser')
        self.plugin_help_browser.setAccessibleName('Plugin getting started guide')
        help_layout.addWidget(self.plugin_help_browser, 1)
        self.plugin_help_panel.hide()

    def refresh_apps(self):
        if self.closing_workers:
            return
        if self.apps_refreshing:
            self.apps_dirty = True
            return
        self.apps_refreshing = True
        self.jobs.builtin('apps', {'kind': 'apps'}, self.apps_loaded, 5000)

    def apps_loaded(self, payload):
        self.apps_refreshing = False
        if self.closing_workers:
            return
        was_loading, self.apps_loading = self.apps_loading, False
        try:
            if payload.get('error') or not isinstance(payload.get('results'), list):
                raise ValueError(payload.get('error') or 'Invalid application response')
            rows = [Result(**row) for row in payload['results']]
            changed = {row.id: row for row in rows} != {row.id: row for row in self.apps}
            if changed:
                self.apps = rows
                self.root_ranking.invalidate()
            if changed or not self.apps_cached:
                self.apps_cached = self.applications.save(rows)
            if self.mode == 'root' and (changed or was_loading):
                self.render()
        except (TypeError, ValueError) as error:
            if self.mode == 'root':
                self.render()
                self.footer.setText('App indexing: ' + str(error)[:240])
        self.update_busy()
        if self.apps_dirty:
            self.apps_dirty = False
            self.apps_refresh_timer.start()

    def set_mode(self, mode, record=True):
        if self.closing_workers:
            return False
        if not self.confirm_form_leave(lambda: self.set_mode(mode, record=record)):
            return False
        if self.mode == 'marketplace' and mode != self.mode and self.marketplace is not None:
            if not self.marketplace.leave(lambda: self.set_mode(mode, record=record)):
                return False
        if record and hasattr(self, 'mode') and mode != self.mode and self.active_form() is None:
            self.push_navigation()
        self.clear_plugin_shortcuts()
        self.toast.dismiss()
        self.wipe_form_passwords()
        if self.plugin_help_id is not None:
            self.close_plugin_help()
        self.jobs.cancel('mode')
        self.jobs.cancel('clip-preview')
        for token in self.plugin_tokens.values():
            token.set()
        self.plugin_tokens.clear()
        self.plugin_calls.clear()
        self.root_ranking.cancel()
        self.plugin_runtime.deactivate()
        if mode.startswith('plugin:') and (mode.removeprefix('plugin:') not in self.plugins
                or self.plugins[mode.removeprefix('plugin:')][0].get('role') == 'ranking'):
            raise ValueError('Unknown plugin: ' + mode.removeprefix('plugin:'))
        self.clip_filter.hidePopup()
        active_plugin = mode.removeprefix('plugin:') if mode.startswith('plugin:') else None
        for plugin_id in list(self.plugin_views):
            if plugin_id != active_plugin:
                self.plugin_views.pop(plugin_id, None)
                self.extra.pop('plugin:' + plugin_id, None)
        self.rendered_plugin_view = None
        clear_layout(self.plugin_detail_layout)
        self.mode = mode
        if mode == 'marketplace':
            if self.marketplace is None:
                from .plugin_manager_ui import Marketplace
                self.marketplace = Marketplace(self)
            self.catalogs[mode] = self.marketplace.presentation()
        self.clip_filter.setEnabled(True)
        self.list.setEnabled(True)
        self.mode_loading, self.mode_error = mode in ('clipboard', 'windows', 'files'), ''
        if mode in self.bundled_plugins and mode != 'ranking':
            self.catalogs[mode] = self.bundled_plugins[mode].presentation(self.cfg)
        if mode.startswith('plugin:'):
            self.catalogs.pop(mode, None)
        catalog = self.catalogs.get(mode)
        heading = catalog.heading if catalog else ''
        if mode == 'web' or mode.startswith('plugin:'):
            heading = heading or 'Results'
        self.results_heading.setText(heading)
        self.results_heading.setVisible(bool(heading))
        self.clip_filter.setVisible(bool(catalog and catalog.filters))
        self.clip_detail_panel.setVisible(bool(catalog and catalog.layout == 'list_detail'))
        self.plugin_detail_panel.setVisible(False)
        self.clip_preview_id = ''
        self.clip_preview_timer.stop()
        self.clip_preview.setPixmap(QPixmap())
        self.clip_preview.setText('Select clipboard item')
        self.clip_stack.setCurrentWidget(self.clip_preview)
        self.clip_text.clear()
        self.clip_type.clear()
        self.clip_details.hide()
        self.clip_information.hide()
        self.mode_rows = []
        self.configure_catalog(catalog)
        placeholders = {'root': self.cfg['search']['placeholder'],
                        'emoji': 'Search emoji…', 'clipboard': 'Search clipboard…', 'dmenu': 'Type to filter choices…',
                        'calc': 'Type a calculation…',
                        'web': 'Search the web…', 'files': 'Search this folder…',
                        'windows': 'Search windows…'}
        self.search.setPlaceholderText(catalog.placeholder if catalog else placeholders.get(mode,
            'Search ' + self.plugins[mode.removeprefix('plugin:')][0]['name'] + '…'
            if mode.startswith('plugin:') else placeholders['root']))
        glyph = '\uf002' if mode == 'root' else '\uf060'
        font = glyph_font(glyph, self.cfg['icons']['search_size'] if mode == 'root' else 18)
        self.lead.setText(glyph if font is not None else '')
        self.lead.setFont(font or self.font())
        self.lead.setIcon(QIcon() if font is not None else themed_icon('system-search' if mode == 'root' else 'go-previous'))
        self.lead.setAccessibleName('KITE' if mode == 'root' else 'Back to KITE')
        self.lead.setCursor(Qt.CursorShape.ArrowCursor if mode == 'root' else Qt.CursorShape.PointingHandCursor)
        self.search.clear()
        if mode == 'emoji':
            self.mode_rows = plugin_rows(self.bundled_plugins['emoji'].search('', plugin_context(self.cfg)))
        self.changed()
        if mode == 'marketplace':
            self.marketplace.enter()
        if mode in ('clipboard', 'windows', 'files'):
            def loaded(payload):
                if self.mode != mode:
                    return
                self.mode_loading = False
                if payload.get('error'):
                    self.mode_error = str(payload['error'])
                    self.render()
                    self.footer.setText(self.mode_error)
                else:
                    self.mode_rows = [Result(**row) for row in payload.get('results', [])]
                    self.render()
            self.jobs.builtin('mode', {'kind': mode, 'value': str(self.directory)}, loaded, 5000)
        self.search.setFocus()
        self.refresh_plugin_shortcuts()
        return True

    def configure_catalog(self, catalog):
        grid = bool(catalog and catalog.layout == 'grid')
        self.list.setViewMode(QListWidget.ViewMode.IconMode if grid else QListWidget.ViewMode.ListMode)
        self.list.setFlow(QListWidget.Flow.LeftToRight if grid else QListWidget.Flow.TopToBottom)
        self.list.setWrapping(grid)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setUniformItemSizes(grid)
        self.list.setSpacing(0 if grid else self.cfg['results']['gap'])
        if grid:
            self.update_emoji_grid()
        else:
            self.list.setGridSize(QSize())
        filters = catalog.filters if catalog else ()
        self.clip_filter.setVisible(bool(filters))
        existing = tuple(self.clip_filter.options.item(i).text() for i in range(self.clip_filter.options.count()))
        if filters and filters != existing:
            self.clip_filter.blockSignals(True)
            self.clip_filter.options.clear()
            self.clip_filter.label.clear()
            self.clip_filter.addItems(filters)
            self.clip_filter.blockSignals(False)

    def catalog_filter_changed(self, value):
        if self.mode == 'marketplace':
            self.marketplace.filter_changed(value)
        elif self.mode.startswith('plugin:'):
            self.plugin_event(self.mode.removeprefix('plugin:'),
                              PluginEvent('catalog', 'filter', {'value': value, 'query': self.search.text()}))
        else:
            self.render()

    def changed(self, *_):
        text = self.search.text().lstrip()
        if self.mode == 'root' and text.startswith((';', '"')):
            self.push_navigation(self.navigation_state(query=''))
            self.set_mode('clipboard' if text[0] == ';' else 'emoji', record=False)
            self.search.setText(text[1:].lstrip())
            return
        if self.mode == 'root' and text.startswith('?'):
            self.push_navigation(self.navigation_state(query=''))
            self.set_mode('web', record=False)
            self.search.setText(text[1:].lstrip())
            return
        if self.mode in ('root', 'calc') and text.startswith('='):
            if self.mode == 'root':
                self.push_navigation(self.navigation_state(query=''))
                self.set_mode('calc', record=False)
            self.search.setText(text.lstrip('=').lstrip())
            return
        self.selection_moved = False
        if hasattr(self, 'action_menu'):
            self.action_menu.close()
        self.revision += 1
        self.extra = {}
        self.math_timer.stop()
        self.math_error_timer.stop()
        self.math_error = None
        if self.last_math_error and self.last_math_error[:2] != (self.mode, self.search.text().strip()):
            self.last_math_error = None
        if self.math_toast_visible:
            self.toast.dismiss()
            self.math_toast_visible = False
        self.web_timer.stop()
        self.extension_timer.stop()
        self.plugin_timer.stop()
        for token in self.plugin_tokens.values():
            token.set()
        if self.mode.startswith('plugin:'):
            self.wipe_form_passwords()
            self.mode_rows = []
            self.plugin_views.clear()
            self.plugin_calls.clear()
            self.plugin_runtime.cancel()
            self.refresh_plugin_shortcuts()
        for key in list(self.jobs.running):
            if key.startswith(('math', 'extension:')):
                self.jobs.cancel(key)
        if self.reply:
            self.reply.abort()
            self.reply = None
        query = self.search.text().strip()
        if self.mode == 'marketplace':
            self.marketplace.publish()
            return
        if (self.mode in ('root', 'calc') and self.cfg['math']['enabled'] and query
                and (self.mode == 'calc' or math_like(query))):
            self.math_timer.start(self.cfg['math']['debounce_ms'])
        if len(self.web_query()) >= self.cfg['web']['min_characters'] and self.cfg['web']['enabled']:
            self.web_timer.start(self.cfg['web']['debounce_ms'])
        if self.mode == 'root' and any(query.startswith(e['prefix']) for e in self.cfg['extensions']):
            self.extension_timer.start(150)
        if self.mode.startswith('plugin:'):
            self.plugin_timer.start(100)
        self.render()

    def is_busy(self):
        return bool(self.mode_loading or self.mode == 'root' and
                    (self.apps_loading or self.root_ranking.pending and self.root_ranking.ordered is None and not self.apps_cached) or self.plugin_calls
                    or self.math_timer.isActive() or self.web_timer.isActive() or self.extension_timer.isActive() or self.plugin_timer.isActive()
                    or self.reply is not None
                    or any(key == 'math' or key.startswith('extension:') for key in self.jobs.running))

    def update_busy(self):
        self.loading_bar.setVisible(self.plugin_help_id is None and self.is_busy()
                                    and not (self.mode == 'root' and not self.rows))
        form = self.active_form()
        if form:
            form.set_busy(self.marketplace.busy if self.mode == 'marketplace' else
                          self.mode.removeprefix('plugin:') in self.plugin_calls)
            self.update_primary()
        self.update_plugin_shortcut_state()

    def clear_plugin_shortcuts(self):
        for shortcut in self.plugin_shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.plugin_shortcuts.clear()

    def refresh_plugin_shortcuts(self):
        self.clear_plugin_shortcuts()
        if not getattr(self, 'mode', '').startswith('plugin:'):
            return
        plugin_id = self.mode.removeprefix('plugin:')
        entry = self.plugins.get(plugin_id)
        if entry is None:
            return

        def bind(keys, callback, back=False):
            sequence = QKeySequence.fromString(keys, QKeySequence.SequenceFormat.PortableText)
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.setProperty('plugin_id', plugin_id)
            shortcut.setProperty('plugin_back', back)
            shortcut.activated.connect(callback)
            self.plugin_shortcuts.append(shortcut)

        view = self.plugin_views.get(plugin_id)
        if view:
            for item in view.actions:
                if item.shortcut:
                    row = plugin_row(plugin_id, PluginResult(item.id, item.title, action=item.action, glyph=item.glyph))
                    bind(_canonical_shortcut(item.shortcut),
                         lambda r=row, v=view: self.run_plugin_shortcut(plugin_id, v, r), row.kind == 'back')
        self.update_plugin_shortcut_state()

    def update_plugin_shortcut_state(self):
        active = self.mode.removeprefix('plugin:') if getattr(self, 'mode', '').startswith('plugin:') else None
        form = self.active_form()
        blocked = bool(form and not form.confirmation.isHidden())
        for shortcut in self.plugin_shortcuts:
            enabled = shortcut.property('plugin_id') == active
            enabled = enabled and self.plugin_help_id is None
            if not shortcut.property('plugin_back'):
                enabled = enabled and active not in self.plugin_calls and not blocked
            shortcut.setEnabled(bool(enabled))

    def run_plugin_shortcut(self, plugin_id, view, row):
        if (self.mode != 'plugin:' + plugin_id or self.plugin_views.get(plugin_id) is not view
                or self.plugin_help_id is not None or plugin_id in self.plugin_calls and row.kind != 'back'):
            return
        form = self.active_form()
        if form and not form.confirmation.isHidden() and row.kind != 'back':
            return
        if self.action_menu.isVisible():
            self.action_menu.close()
        self.activate_result(row)

    def toggle_help(self):
        if self.action_menu.isVisible():
            self.action_menu.close()
        if self.plugin_help_id is not None:
            self.close_plugin_help()
        else:
            self.show_plugin_help()

    def show_toast(self, toast):
        validate_toast(toast)
        self.math_toast_visible = False
        self.toast.show_toast(toast)

    def update_emoji_grid(self):
        tile = max(32, self.list.viewport().width() // self.cfg['emoji']['columns'])
        self.emoji_columns = max(1, self.list.viewport().width() // tile)
        size = QSize(tile, tile)
        if self.list.gridSize() != size:
            self.list.setGridSize(size)

    def show_copy_toast(self):
        self.show_toast(Toast('Copied to clipboard', tone='success'))

    def web_query(self):
        query = self.search.text().strip()
        excluded = math_like(query) or any(query.startswith(e['prefix']) for e in self.cfg['extensions'])
        return self.bundled_plugins['web'].query_for(self.mode, query, self.cfg, excluded)

    def render(self):
        if self.list.viewMode() == QListWidget.ViewMode.IconMode:
            self.update_emoji_grid()
        query = self.search.text().strip()
        aliases = {a['id']: a['alias'].strip() for a in self.cfg['aliases']}
        candidates = self.apps + self.commands if self.mode == 'root' else self.mode_rows
        if self.mode == 'clipboard':
            candidates = [row for row in candidates if self.clip_matches(row)]
        catalog = self.catalogs.get(self.mode)
        ranked = (self.root_ranking.rows(candidates, query, aliases) if self.mode == 'root' else
                  candidates[:self.cfg['results']['limit']] if (self.mode == 'dmenu' or catalog and catalog.preserve_order) and not query else
                  rank(candidates, query, self.usage, self.cfg['results']['limit'], aliases=aliases,
                       sensitivity=self.cfg['ranking']['sensitivity'],
                       learn_queries=self.cfg['ranking']['learn_queries']))
        groups = [('', self.extra.get('math', []))]
        if self.mode == 'root' and not query:
            all_rows = ranked if self.root_ranking.external else self.apps + self.commands
            by_id = {r.id: r for r in all_rows}
            favorites = list(dict.fromkeys(self.cfg['favorites']))
            taken = set(favorites)
            groups.append(('Favorites', [by_id[i] for i in favorites if i in by_id]))
            for name, rows in [('Plugins', self.commands), ('Applications', self.apps)]:
                if self.root_ranking.external:
                    ids = {row.id for row in rows} - taken
                    groups.append((name, [row for row in ranked if row.id in ids]))
                else:
                    groups.append((name, self.bundled_plugins['ranking'].rank_rows([r for r in rows if r.id not in taken], '', self.usage,
                                              self.cfg['results']['limit'], aliases=aliases)))
        else:
            groups.append(('Results' if self.mode == 'root' else 'History' if self.mode == 'clipboard' else '', ranked))
        for key, values in self.extra.items():
            if key.startswith('extension:') or self.mode == 'root' and key.startswith('plugin:'):
                groups.append((key.removeprefix('extension:').removeprefix('plugin:'), values))
        web = self.web_query()
        fallback = web or (query if self.mode == 'root' else '')
        if fallback:
            from .host_services import result_row
            fallbacks = [result_row(item) for item in self.bundled_plugins['web'].fallbacks(fallback, self.mode == 'root')]
            groups.append(('Use “' + fallback + '” with…' if self.mode == 'root' else '', fallbacks))
        groups.append(('Google suggestions' if self.mode == 'root' else '', self.extra.get('web', [])))
        if self.mode == 'root' and self.apps_loading:
            groups = []  # Reveal the complete root catalog together, not a partial commands-only view.
        selected_item = self.list.currentItem()
        selected = selected_item.data(Qt.ItemDataRole.UserRole + 1) if selected_item else None
        selected_id = self.navigation_selection or (selected.id if selected and self.selection_moved else None)
        self.rows = []
        reuse = self.mode == 'root'
        if not reuse:
            self.list.clear()
        selected_index, first_index, position = -1, -1, 0
        updates = self.list.updatesEnabled()
        signals = self.list.blockSignals(True)
        self.list.setUpdatesEnabled(False)
        try:
            for title, rows in groups:
                rows = rows[:self.cfg['results']['limit'] - len(self.rows)]
                if not rows:
                    continue
                entries = [(title, None)] if title else []
                entries.extend(('', row) for row in rows)
                for heading, row in entries:
                    item = self.list.item(position)
                    if item is None:
                        item = QListWidgetItem()
                        self.list.addItem(item)
                    signature = (('section', heading) if row is None else
                                 ('row', row.title, row.subtitle, row.icon, row.glyph, row.accessibility_label,
                                  self.mode, row.id == 'math', self.list.gridSize().width()))
                    if item.data(Qt.ItemDataRole.UserRole + 3) != signature:
                        item.setData(Qt.ItemDataRole.UserRole + 3, signature)
                        item.setData(Qt.ItemDataRole.UserRole + 2, row is None)
                        item.setText((heading if row is None else row.title).replace('\n', ' ')[:600])
                        item.setFlags(Qt.ItemFlag.NoItemFlags if row is None else
                                      Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                        item.setIcon(themed_icon(row.icon) if row is not None and not row.glyph
                                     and not row.id.startswith('emoji:') else QIcon())
                        item.setForeground(QColor(self.cfg['colors']['muted']) if row is None else QBrush())
                        item.setFont(pixel_font(self.cfg['results']['font_family'], 12, QFont.Weight.DemiBold)
                                     if row is None else self.list.font())
                        item.setToolTip('' if row is None else row.title if self.mode == 'emoji' else row.subtitle)
                        item.setData(Qt.ItemDataRole.AccessibleTextRole,
                                     heading if row is None else row.accessibility_label or row.title)
                        item.setSizeHint(QSize(0, 32) if row is None else
                                         self.list.gridSize() if self.list.viewMode() == QListWidget.ViewMode.IconMode else
                                         QSize(0, max(118, self.cfg['results']['row_height']) if row.id == 'math'
                                               else 56 if self.mode == 'clipboard' else self.cfg['results']['row_height']))
                    item.setData(Qt.ItemDataRole.UserRole, row.id if row else None)
                    item.setData(Qt.ItemDataRole.UserRole + 1, row)
                    if row is not None:
                        if first_index < 0:
                            first_index = position
                        if row.id == selected_id:
                            selected_index = position
                        self.rows.append(row)
                    position += 1
            while self.list.count() > position:
                self.list.takeItem(self.list.count() - 1)
        finally:
            self.list.blockSignals(signals)
            self.list.setUpdatesEnabled(updates)
        if not self.rows:
            prompts = {'calc': 'Type a calculation to begin', 'windows': 'No open windows', 'files': 'This folder is empty'}
            prompts.update({name: catalog.empty for name, catalog in self.catalogs.items()})
            loading_text = ('Loading applications…' if self.mode == 'root' and
                            (self.apps_loading or self.root_ranking.pending and self.root_ranking.ordered is None)
                            else 'Loading…')
            text = (loading_text if self.is_busy() else 'Could not load results' if self.mode_error else
                    prompts.get(self.mode, 'Type to search') if not query else 'No matching results')
            self.empty_hint.setText(text)
            self.empty_hint.setGeometry(self.list.viewport().rect())
            self.empty_hint.raise_()
        self.empty_hint.setVisible(not self.rows)
        self.list.setCurrentRow(selected_index if selected_index >= 0 else first_index)
        if selected_index >= 0 and self.navigation_selection:
            self.navigation_selection = None
            self.selection_moved = True
        elif not self.is_busy():
            self.navigation_selection = None
        full_form = False
        if self.mode == 'marketplace':
            self.marketplace.show_details()
            full_form = self.marketplace.page != 'list'
            self.results_heading.setText(f'{self.marketplace.tab} · {len(self.rows)}')
            self.results_heading.setVisible(not full_form)
        elif self.mode.startswith('plugin:'):
            plugin_id = self.mode.removeprefix('plugin:')
            view = self.plugin_views.get(plugin_id)
            self.plugin_detail_panel.setVisible(bool(view and view.nodes))
            full_form = bool(view and view.nodes and view.nodes[0].type == 'form')
            if view is not self.rendered_plugin_view:
                previous_form = self.active_form()
                dirty = bool(previous_form and previous_form.dirty)
                old_form_id = previous_form.objectName() if previous_form else ''
                self.wipe_form_passwords()
                self.pending_leave = None
                render_nodes(self.plugin_detail_layout, view.nodes if view else (),
                             lambda event: self.plugin_event(plugin_id, event), self.cfg)
                self.rendered_plugin_view = view
                form = self.active_form()
                if form and view is not None and view.nodes:
                    form.dirty = dirty and form.objectName() == old_form_id
                    if not previous_form or view.nodes[0].props.get('error'):
                        field = form.fields.findChild(QLineEdit)
                        if field:
                            field.setFocus()
                            field.setCursorPosition(len(field.text()))
            catalog = self.catalogs.get(self.mode)
            heading = catalog.heading if catalog and catalog.heading else 'Results'
            self.results_heading.setText(f'{heading} · {len(self.mode_rows)}')
            self.results_heading.show()
        elif self.mode == 'web':
            catalog = self.catalogs.get(self.mode)
            heading = catalog.heading if catalog and catalog.heading else 'Results'
            count = sum(len(rows) for _, rows in groups[1:])
            self.results_heading.setText(f'{heading} · {count}')
            self.results_heading.show()
        self.list.setVisible(not full_form)
        self.search.setReadOnly(full_form or self.mode == 'marketplace' and self.marketplace.busy)
        self.plugin_detail_layout.setContentsMargins(*(0, 0, 0, 0) if full_form else (16, 12, 16, 12))
        self.plugin_detail_panel.setStyleSheet('QFrame#pluginDetailPanel { border: none; }' if full_form else '')
        if full_form:
            self.results_heading.hide()
            self.search.setPlaceholderText('Plugin marketplace' if self.mode == 'marketplace' else
                                           self.plugins[self.mode.removeprefix('plugin:')][0]['name'])
        elif self.mode.startswith('plugin:'):
            catalog = self.catalogs.get(self.mode)
            self.search.setPlaceholderText(catalog.placeholder if catalog else
                                           'Search ' + self.plugins[self.mode.removeprefix('plugin:')][0]['name'] + '…')
        context = {'root': 'KITE', 'emoji': 'Search Emoji & Symbols', 'clipboard': 'Clipboard history',
                   'calc': 'Calculator', 'files': str(self.directory), 'windows': 'Window switcher',
                   'web': 'Live Google suggestions · Enter opens selected result' if self.cfg['web']['enabled'] else 'Suggestions off · Enter searches Google',
                   'dmenu': 'dmenu · Enter selects · Esc cancels'}
        context.update({name: catalog.footer for name, catalog in self.catalogs.items()})
        footer_context = (context.get(self.mode) or 'Enter runs selected result · Ctrl+K actions'
                          if self.mode.startswith('plugin:') else context.get(self.mode, context['root']))
        self.footer.setText('Calculator' if self.extra.get('math') else footer_context)
        self.footer.setToolTip('Web mode sends text to Google while typing. Esc returns to apps.'
                               if self.mode == 'web' and self.cfg['web']['enabled'] else
                               footer_context + ' · Esc back · Ctrl+L web search')
        self.update_primary()
        self.update_busy()

    def clip_matches(self, row):
        return self.bundled_plugins['clipboard'].matches(row.title, row.subtitle, self.clip_filter.currentText())

    def clip_selection_changed(self, *_):
        self.update_primary()
        if self.mode != 'clipboard':
            return
        row = self.selected_result()
        self.jobs.cancel('clip-preview')
        self.clip_preview_timer.stop()
        self.clip_preview_id = row.id if row else ''
        self.clip_stack.setCurrentWidget(self.clip_preview)
        self.clip_preview.setPixmap(QPixmap())
        self.clip_preview.setText('Loading preview…' if row else 'Select clipboard item')
        self.clip_text.clear()
        self.clip_type.clear()
        self.clip_details.hide()
        self.clip_information.hide()
        if row:
            self.clip_preview_timer.start(90)

    def request_clip_preview(self):
        row = self.selected_result()
        if self.mode != 'clipboard' or not row:
            return
        row_id = row.id
        def loaded(payload):
            if self.mode != 'clipboard' or self.clip_preview_id != row_id:
                return
            if payload.get('error'):
                self.clip_preview.setText(str(payload['error']))
                return
            result = payload.get('results', {})
            if not isinstance(result, dict):
                self.clip_preview.setText('Preview unavailable')
                return
            kind, preview = result.get('type', 'Text'), result.get('preview', '')
            self.clip_type.setText('Information')
            details = self.bundled_plugins['clipboard'].information(result)
            values = dict(details)
            for name, label in self.clip_detail_values.items():
                label.setText(str(values.get(name, '')))
                self.clip_detail_rows[name].setVisible(name in values)
            self.clip_details.show()
            self.clip_information.show()
            info_layout = self.clip_information.layout()
            if info_layout:
                info_layout.activate()
            detail_layout = self.clip_detail_panel.layout()
            if detail_layout:
                detail_layout.activate()
            if kind == 'Image':
                try:
                    self.clip_preview.set_data(base64.b64decode(preview, validate=True))
                except (ValueError, TypeError):
                    self.clip_preview.setText('Image preview unavailable')

            elif kind == 'Color':
                color = hex_color(preview)
                pixmap = QPixmap(200, 180)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setBrush(color)
                painter.setPen(color.darker(120))
                painter.drawEllipse(40, 10, 120, 120)
                painter.setPen(QColor(self.cfg['colors']['text']))
                painter.setFont(pixel_font(self.cfg['results']['font_family'], 14))
                painter.drawText(QRect(0, 145, 200, 24), Qt.AlignmentFlag.AlignCenter, preview.strip())
                painter.end()
                self.clip_preview.setPixmap(pixmap)
            else:
                self.clip_text.setPlainText(preview or 'Empty clipboard entry')
                self.clip_stack.setCurrentWidget(self.clip_text)
        self.jobs.builtin('clip-preview', {'kind': 'clip-preview', 'value': row.value}, loaded, 5000)

    def result_clicked(self, item):
        if item.flags() & Qt.ItemFlag.ItemIsSelectable:
            self.selection_moved = True
            if self.mode not in ('clipboard', 'marketplace'):
                self.activate()

    def selected_result(self):
        if self.mode == 'root' and self.root_ranking.pending:
            return None
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole + 1) if item else None

    def move_selection(self, delta):
        index = self.list.currentRow() + delta
        while 0 <= index < self.list.count():
            if self.list.item(index).flags() & Qt.ItemFlag.ItemIsSelectable:
                self.selection_moved = True
                self.list.setCurrentRow(index)
                return
            index += delta

    def update_primary(self):
        if self.plugin_help_id is not None:
            self.footer.setText('↑ ↓ scroll · Esc returns to results')
            self.primary_button.setEnabled(False)
            self.actions_button.setEnabled(False)
            return
        form = self.active_form()
        if form:
            self.primary_label.setText(form.submit_button.text())
            self.primary_button.updateGeometry()
            self.primary_button.setAccessibleName(form.submit_button.text())
            self.footer.setText('Working… · Esc back' if form.busy else form.title_text + ' · Esc back')
            self.primary_button.setEnabled(form.submit_button.isEnabled())
            self.actions_button.setEnabled(True)
            return
        row = self.selected_result()
        if self.mode == 'marketplace':
            title = self.marketplace.primary_title()
            self.primary_label.setText(title)
            self.primary_button.setAccessibleName(title)
            self.primary_button.setEnabled(row is not None and not self.marketplace.busy)
            self.actions_button.setEnabled(True)
            return
        title = {'copy': 'Copy to Clipboard' if self.mode == 'emoji' else 'Copy Answer' if row and row.id == 'math' else 'Copy',
                 'clip': 'Copy to Clipboard', 'app': 'Open Application', 'mode': 'Open Plugin', 'focus': 'Focus Window',
                 'web': 'Search Google', 'exec': 'Run'}.get(row.kind, 'Open') if row else 'Open'
        self.primary_label.setText(title)
        self.primary_button.updateGeometry()
        self.primary_button.setAccessibleName(title + ' selected result')
        self.primary_button.setEnabled(row is not None)
        self.actions_button.setEnabled(True)

    def show_actions(self):
        if self.action_menu.isVisible():
            self.action_menu.close()
            return
        row = self.selected_result()
        plugin_id = (self.mode.removeprefix('plugin:') if self.mode.startswith('plugin:') else
                     row.value if row and row.kind == 'plugin' else None)
        view = self.plugin_views.get(plugin_id)
        self.selection_moved = True
        self.action_menu.clear()
        if self.mode == 'marketplace':
            for title, callback, glyph in self.marketplace.actions():
                self.action_menu.add_action(title, callback, glyph)
        elif row is not None:
            self.add_result_actions(row)
        if view and view.actions:
            self.action_menu.addSeparator()
            for item in view.actions:
                menu_row = plugin_row(plugin_id, PluginResult(item.id, item.title, action=item.action, glyph=item.glyph))
                action = self.action_menu.add_action(item.title, lambda _=False, r=menu_row: self.activate_result(r),
                                                     item.glyph, _canonical_shortcut(item.shortcut))
                action.setEnabled(plugin_id not in self.plugin_calls or item.action.type == 'back')
        self.action_menu.add_action('How to', self.toggle_help, '\uf059', 'Ctrl+?')
        self.action_menu.addSeparator()
        self.action_menu.add_action('Edit YAML Settings', self.open_config, '\uf013', 'Ctrl+,')
        self.action_menu.popup()

    def add_result_actions(self, row):
        primary = {'copy': 'Copy Answer' if row.id == 'math' else 'Copy', 'clip': 'Copy to Clipboard',
                   'app': 'Open Application', 'mode': 'Open Plugin', 'focus': 'Focus Window'}.get(row.kind, 'Open / Run')
        primary_glyph = '' if row.id == 'math' else {'app': '\uf35d', 'mode': '\uf054', 'web': '\uf002',
                         'copy': '\uf0c5', 'clip': '\uf0ea', 'exec': '\uf04b', 'focus': '\uf2d0'}.get(row.kind, '\uf35d')
        self.action_menu.add_action(primary, self.activate, primary_glyph, 'Enter')
        self.action_menu.addSeparator()
        def copy(value):
            action_context = (self.mode, self.revision)
            def completed(payload):
                if (self.mode, self.revision) != action_context:
                    return
                if payload.get('error'):
                    self.show_toast(Toast('Could not copy', payload['error'], 'error'))
                else:
                    self.show_copy_toast()
            self.jobs.builtin('action', {'kind': 'copy', 'value': value}, completed, 5000)
        if row.id == 'math':
            self.action_menu.add_action('Copy Question and Answer', lambda: copy(row.expression + ' = ' + row.title),
                                        '', 'Ctrl+Shift+C', True)
        else:
            self.action_menu.add_action('Copy Name', lambda: copy(row.title), keys='Ctrl+Shift+C', bind_shortcut=True)
        self.action_menu.add_action('Copy Result ID', lambda: copy(row.id), keys='Ctrl+Shift+I', bind_shortcut=True)
        try:
            favorite_ids = self.favorites_store.ids() if self.favorites_store else []
        except (OSError, sqlite3.Error) as error:
            favorite_ids = []
            self.footer.setText('Favorites unavailable: ' + str(error)[:200])
        if row.id in {item.id for item in self.apps + self.commands} and self.favorites_store:
            if row.id in favorite_ids:
                self.action_menu.add_action('Remove from Favorites',
                    lambda _=False, result_id=row.id: self.change_favorite(result_id, False), '★', 'Ctrl+Shift+F', True)
                position = favorite_ids.index(row.id)
                if position:
                    self.action_menu.add_action('Move Favorite Up',
                        lambda _=False, result_id=row.id: self.move_favorite(result_id, -1), '\uf062', 'Alt+Up', True)
                if position < len(favorite_ids) - 1:
                    self.action_menu.add_action('Move Favorite Down',
                        lambda _=False, result_id=row.id: self.move_favorite(result_id, 1), '\uf063', 'Alt+Down', True)
            else:
                self.action_menu.add_action('Add to Favorites',
                    lambda _=False, result_id=row.id: self.change_favorite(result_id, True), '★', 'Ctrl+Shift+F', True)
        if row.kind == 'app':
            self.action_menu.add_action('Copy Desktop ID', lambda: copy(row.id.removeprefix('app:')),
                                        keys='Ctrl+Shift+D', bind_shortcut=True)

    def show_plugin_help(self, plugin_id=None):
        plugin_id = plugin_id or (self.mode.removeprefix('plugin:') if self.mode.startswith('plugin:') else None)
        directory, name = None, 'KITE'
        if plugin_id in self.plugins:
            manifest, directory = self.plugins[plugin_id]
            name = manifest['name']
        elif self.mode in self.bundled_plugins:
            plugin_id, name = 'builtin:' + self.mode, self.mode.title()
            directory = Path(self.bundled_plugins[self.mode].__file__).parent
        path = APP_ROOT / 'docs' / 'howto.md'
        if directory is not None:
            path = next((directory / file for file in ('HOWTO.md', 'README.md') if (directory / file).is_file()), path)
            if path == APP_ROOT / 'docs' / 'howto.md':
                plugin_id, name = None, 'KITE'
        try:
            if path.stat().st_size > 131072:
                raise ValueError('Guide exceeds 128 KiB')
            markdown = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError, ValueError) as error:
            self.show_toast(Toast('Guide unavailable', str(error)[:160], 'error'))
            return
        lines = markdown.lstrip().splitlines()
        if lines and lines[0].startswith('# ') and name.casefold() in lines[0].casefold():
            markdown = '\n'.join(lines[1:]).lstrip()
        self.plugin_help_title.setText(name)
        self.plugin_help_browser.set_markdown(markdown)
        if self.plugin_help_id is None:
            self.plugin_help_footer = self.footer.text()
        self.plugin_help_id = plugin_id or '__kite__'
        self.bar.hide()
        self.update_busy()
        inset = max(4, self.cfg['window']['padding'], self.cfg['window']['radius'])
        area = self.panel.rect().adjusted(inset, inset, -inset, -inset)
        area.setBottom(self.footerbar.y() - 1)
        self.plugin_help_panel.setGeometry(area)
        self.update_primary()
        self.plugin_help_panel.show()
        self.plugin_help_panel.raise_()
        self.plugin_help_browser.verticalScrollBar().setValue(0)
        self.plugin_help_browser.setFocus()
        self.update_plugin_shortcut_state()

    def close_plugin_help(self):
        self.plugin_help_id = None
        self.plugin_help_panel.hide()
        self.bar.show()
        self.update_busy()
        self.footer.setText(self.plugin_help_footer)
        self.update_primary()
        self.search.setFocus()

    def open_config(self):
        try:
            editor = shlex.split(os.environ.get('EDITOR', ''))
            if not editor:
                command = ['xdg-open', str(self.config_path)]
            else:
                terminal = shlex.split(os.environ.get('TERMINAL', ''))
                if not terminal and shutil.which('kitty'):
                    terminal = ['kitty']
                if terminal:
                    option = ['--detach'] if Path(terminal[0]).name == 'kitty' else ['-e']
                    command = [*terminal, *option, *editor, str(self.config_path)]
                else:
                    command = [*editor, str(self.config_path)]
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except (OSError, ValueError) as error:
            self.footer.setText(f'Could not open settings: {error}')

    def change_favorite(self, result_id, favorite):
        if not self.favorites_store:
            return
        try:
            if favorite:
                self.favorites_store.add(result_id)
            else:
                self.favorites_store.remove(result_id)
            self.cfg['favorites'] = self.favorites_store.ids()
            self.root_ranking.invalidate()
            self.render()
        except (OSError, sqlite3.Error) as error:
            self.footer.setText('Favorites unavailable: ' + str(error)[:200])

    def move_favorite(self, result_id, offset):
        if not self.favorites_store:
            return
        try:
            if self.favorites_store.move(result_id, offset):
                self.cfg['favorites'] = self.favorites_store.ids()
                self.root_ranking.invalidate()
                self.render()
        except (OSError, sqlite3.Error) as error:
            self.footer.setText('Favorites unavailable: ' + str(error)[:200])

    def show_math_error(self):
        pending, self.math_error = self.math_error, None
        if pending is None or not self.isVisible():
            return
        mode, revision, query, message = pending
        if (mode != self.mode or revision != self.revision
                or query != self.search.text().strip()):
            return
        key = (mode, query, message)
        if key != self.last_math_error:
            self.last_math_error = key
            self.show_toast(Toast('Could not calculate', message, 'error'))
            self.math_toast_visible = True

    def request_math(self):
        query, revision, mode = self.search.text().strip(), self.revision, self.mode
        def completed(payload):
            if revision != self.revision or mode != self.mode:
                return
            self.math_error_timer.stop()
            self.math_error = None
            if payload.get('error'):
                self.extra.pop('math', None)
                if not payload.get('incomplete'):
                    self.math_error = (mode, revision, query, str(payload['error'])[:300])
                    self.math_error_timer.start(600)
                self.render()
                return
            self.last_math_error = None
            self.extra['math'] = [Result(**row) for row in payload.get('results', [])]
            self.render()
        self.jobs.builtin('math', {'kind': 'math', 'query': query}, completed,
                          self.cfg['math']['timeout_ms'])

    def request_web(self):
        query, revision = self.web_query(), self.revision
        if len(query) < self.cfg['web']['min_characters']:
            return
        cached = self.web_cache.get(query)
        if cached:
            self.web_received(query, revision, cached)
            return
        url = QUrl(self.bundled_plugins['web'].suggestion_url(query))
        request = QNetworkRequest(url)
        request.setTransferTimeout(self.cfg['web']['timeout_ms'])
        reply = self.network.get(request)
        self.reply = reply
        def finished():
            if self.reply is reply:
                self.reply = None
            if revision == self.revision and reply.error() == QNetworkReply.NetworkError.NoError:
                try:
                    if reply.bytesAvailable() > 65536:
                        raise ValueError('Suggestion response too large')
                    values = suggestions(json.loads(bytes(reply.readAll().data())))
                    if len(self.web_cache) >= 100:
                        self.web_cache.pop(next(iter(self.web_cache)))
                    self.web_cache[query] = values
                    self.web_received(query, revision, values)
                except (ValueError, UnicodeError) as error:
                    self.footer.setText(str(error) + ' · Enter still searches')
            elif revision == self.revision and reply.error() != QNetworkReply.NetworkError.OperationCanceledError:
                self.footer.setText('Suggestions unavailable · Enter still searches')
            reply.deleteLater()
            self.update_busy()
        reply.readyRead.connect(lambda: reply.abort() if reply.bytesAvailable() > 65536 else None)
        reply.finished.connect(finished)

    def web_received(self, query, revision, values):
        if revision != self.revision or query != self.web_query():
            return
        self.extra['web'] = plugin_rows(self.bundled_plugins['web'].search(query,
            plugin_context(self.cfg, suggestions=values)))
        self.render()

    def update_plugin_view(self, plugin_id, view):
        validate_view(view)
        if self.mode == 'plugin:' + plugin_id and self.action_menu.isVisible():
            self.action_menu.close()
        self.plugin_views[plugin_id] = view
        self.refresh_plugin_shortcuts()
        rows = [plugin_row(plugin_id, item) for item in view.results]
        self.extra['plugin:' + plugin_id] = rows
        if self.mode == 'plugin:' + plugin_id:
            if view.catalog:
                self.catalogs[self.mode] = view.catalog
                self.configure_catalog(view.catalog)
                self.search.setPlaceholderText(view.catalog.placeholder)
                self.results_heading.setText(view.catalog.heading or 'Results')
                self.results_heading.show()
            else:
                self.catalogs.pop(self.mode, None)
                self.configure_catalog(None)
                self.search.setPlaceholderText('Search ' + self.plugins[plugin_id][0]['name'] + '…')
                self.results_heading.setText('Results')
                self.results_heading.show()
            self.mode_rows = rows
            self.render()

    def start_plugin_call(self, plugin_id, method, args, navigation=None, submitted=False):
        self.revision += 1  # Distinguish successive events even when search text is unchanged.
        previous = self.plugin_tokens.get(plugin_id)
        if previous:
            previous.set()
        token = threading.Event()
        self.plugin_tokens[plugin_id] = token
        revision = self.revision
        params = {'query': args[0]} if method == 'search' else {'event': asdict(args[0])}
        call = PluginCall(plugin_id, revision, method, params, token)
        call.navigation, call.submitted = navigation, submitted
        self.plugin_calls[plugin_id] = (call, token, revision)
        call.signals.finished.connect(self.plugin_call_finished)
        self.plugin_runtime.submit(call, self.plugins[plugin_id][1])
        self.update_busy()

    def plugin_call_finished(self, plugin_id, revision, view, error):
        current = self.plugin_calls.get(plugin_id)
        if current is None or current[2] != revision:
            return
        call, token, _ = self.plugin_calls.pop(plugin_id)
        self.plugin_tokens.pop(plugin_id, None)
        if token.is_set() or revision != self.revision or self.mode != 'plugin:' + plugin_id:
            self.update_busy()
            return
        if error:
            if call.submitted:
                self.wipe_form_passwords()
            self.show_toast(Toast('Plugin error', error[:512], 'error'))
        else:
            try:
                if isinstance(view, Toast):
                    if call.submitted and view.tone == 'error':
                        self.wipe_form_passwords()
                    self.update_busy()
                    self.show_toast(view)
                    return
                validate_view(view)
                if not view.error:
                    if call.navigation is not None:
                        self.push_navigation(call.navigation)
                    elif (call.submitted and self.active_form() and not (view.nodes and view.nodes[0].type == 'form')
                          and self.navigation_stack and self.navigation_stack[-1][0] == self.mode):
                        self.navigation_selection = self.navigation_stack.pop()[2]
                self.update_plugin_view(plugin_id, view)
                if view.error:
                    self.footer.setText(view.error[:300])
                if view.toast is not None:
                    self.show_toast(view.toast)
            except (TypeError, ValueError) as exception:
                self.footer.setText('Plugin response invalid: ' + str(exception)[:300])
        self.update_busy()

    def request_plugin(self):
        plugin_id = self.mode.removeprefix('plugin:')
        entry = self.plugins.get(plugin_id)
        if entry is not None:
            self.start_plugin_call(plugin_id, 'search', (self.search.text().strip(),))

    def plugin_event(self, plugin_id, event, navigation=None):
        form = self.active_form()
        if form and event.node_id == form.objectName():
            if event.name == 'back':
                self.back()
                return
            if event.name == 'keep':
                self.pending_leave = None
                self.update_primary()
                self.update_plugin_shortcut_state()
                return
            if event.name == 'discard':
                callback = self.pending_leave or self.pop_navigation
                self.pending_leave = None
                form.wipe_passwords()
                form.dirty = False
                form.hide_discard_prompt()
                callback()
                return
        if self.mode == 'marketplace' and plugin_id == 'marketplace':
            self.marketplace.handle_event(event)
            return
        entry = self.plugins.get(plugin_id)
        if entry is not None:
            self.plugin_timer.stop()
            event = PluginEvent(event.node_id, event.name, {**event.payload,
                'query': self.search.text(), 'inputs': input_values(self.plugin_detail_panel)})
            self.start_plugin_call(plugin_id, 'handle_event', (event,), navigation=navigation,
                                   submitted=bool(form and event.node_id == form.objectName() and event.name == 'submit'))

    def request_extensions(self):
        query, revision = self.search.text().strip(), self.revision
        for extension in self.cfg['extensions']:
            if not query.startswith(extension['prefix']):
                continue
            key = 'extension:' + extension['name']
            def completed(payload, key=key):
                if revision != self.revision:
                    return
                try:
                    rows = payload.get('results', [])
                    if payload.get('error'):
                        raise ValueError(str(payload['error']))
                    if not isinstance(rows, list) or len(rows) > 100:
                        raise ValueError('Extension must return at most 100 results')
                    values = []
                    for index, row in enumerate(rows):
                        if (not isinstance(row, dict) or not isinstance(row.get('title'), str)
                                or not isinstance(row.get('action'), dict)):
                            raise ValueError('Invalid extension result')
                        action = row['action']
                        if action.get('type') not in ('copy', 'open', 'exec'):
                            raise ValueError('Unsupported extension action')
                        val = action.get('value')
                        if action['type'] == 'exec':
                            if not isinstance(val, list) or not val or not all(isinstance(x, str) for x in val):
                                raise ValueError('exec action needs an argv list')
                        elif not isinstance(val, str):
                            raise ValueError('copy/open action needs a string')
                        values.append(Result(key + ':' + str(index), row['title'][:512],
                                             str(row.get('subtitle', ''))[:512],
                                             str(row.get('icon', 'application-x-executable')),
                                             action['type'], val))
                    self.extra[key] = values
                    self.render()
                except ValueError as error:
                    self.render()
                    self.footer.setText(key + ': ' + str(error))
            self.jobs.start(key, extension['command'],
                            {'query': query[len(extension['prefix']):], 'version': 1}, completed)

    def activate(self):
        form = self.active_form()
        if form:
            form.submit_button.click()
        else:
            self.activate_result(self.selected_result())

    def activate_result(self, row):
        if row is None or self.mode == 'root' and self.root_ranking.pending:
            return
        if row.kind == 'back':
            self.back()
            return
        if self.active_form() and row.kind != 'plugin_action' and not self.confirm_form_leave(lambda: self.activate_result(row)):
            return
        if self.mode == 'dmenu':
            self.dmenu_selected = True
            sys.stdout.write(row.value + '\n')
            sys.stdout.flush()
            self.close()
            return
        if row.kind == 'mode':
            self.remember(row)
            self.set_mode(row.value)
            return
        if row.kind == 'marketplace':
            self.open_marketplace()
            return
        if row.kind == 'marketplace_item':
            self.marketplace.primary()
            return
        if row.kind == 'plugin':
            self.set_mode('plugin:' + str(row.value))
            return
        if row.kind == 'plugin_action':
            plugin_id = str(row.id).split(':', 2)[1]
            self.plugin_event(plugin_id, PluginEvent(row.value['result_id'], 'activate', {'value': row.value['value']}),
                              navigation=self.navigation_state() if row.push else None)
            return
        if row.kind == 'query':
            value = str(row.value)
            if row.push and self.search.text() != value:
                self.push_navigation()
            if self.search.text() == value:
                self.changed()
            else:
                self.search.setText(value)
            self.search.setFocus()
            return
        if row.kind == 'directory':
            self.push_navigation()
            self.directory = Path(row.value)
            self.set_mode('files', record=False)
            return
        try:
            query = self.search.text()
            if row.kind in ('copy', 'clip', 'focus', 'app'):
                action_context = (self.mode, self.revision)
                def completed(payload):
                    if (self.mode, self.revision) != action_context:
                        return
                    if row.kind in ('copy', 'clip'):
                        if payload.get('error'):
                            self.show_toast(Toast('Could not copy', payload['error'], 'error'))
                        else:
                            self.show_copy_toast()
                    elif payload.get('error'):
                        self.footer.setText(payload['error'])
                    else:
                        self.remember(row, query)
                        self.close()
                if row.kind not in ('copy', 'clip'):
                    self.footer.setText('Working…')
                self.jobs.builtin('action', {'kind': row.kind, 'value': row.value}, completed, 5000)
                return
            if row.kind == 'web':
                argv = ['xdg-open', 'https://www.google.com/search?' + urlencode({'q': row.value})]
            elif row.kind == 'open':
                parsed = urlparse(row.value)
                if parsed.scheme and parsed.scheme not in ('https', 'http', 'file'):
                    raise ValueError('Only local files and HTTP(S) URLs may be opened')
                argv = ['xdg-open', row.value]
            elif row.kind == 'exec':
                argv = row.value
            else:
                return
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.remember(row)
            self.close()
        except (OSError, ValueError) as error:
            self.footer.setText(str(error))

    def remember(self, row, query=None):
        if row.kind not in ('app', 'mode'):
            return
        query = (self.search.text() if query is None else query) if self.cfg['ranking']['learn_queries'] and self.mode == 'root' else ''
        self.usage[row.id] = record_visit(self.usage.get(row.id, {}), query)
        if not self.cfg['ranking']['learn_queries']:
            self.usage[row.id]['terms'] = []
        self.root_ranking.invalidate()
        if not self.history_writable:
            return
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            temp = STATE.with_suffix('.tmp')
            with temp.open('w') as stream:
                os.chmod(temp, 0o600)
                json.dump(self.usage, stream)
            temp.replace(STATE)
        except OSError as error:
            print('Usage history not saved:', error, file=sys.stderr)

    def active_form(self):
        if self.mode != 'marketplace' and not getattr(self, 'mode', '').startswith('plugin:'):
            return None
        return self.plugin_detail_panel.findChild(Form)

    def wipe_form_passwords(self):
        form = self.active_form()
        if form:
            form.wipe_passwords()

    def confirm_form_leave(self, callback):
        form = self.active_form()
        if form and form.dirty:
            if self.plugin_help_id is not None:
                self.close_plugin_help()
            if self.action_menu.isVisible():
                self.action_menu.close()
            self.pending_leave = callback
            if form.confirmation.isHidden():
                form.show_discard_prompt()
            self.update_primary()
            self.update_plugin_shortcut_state()
            return False
        return True

    def navigation_state(self, query=None):
        row = self.selected_result()
        return (self.mode, self.search.text() if query is None else query, row.id if row else None,
                str(self.directory) if self.mode == 'files' else None)

    def push_navigation(self, state=None):
        state = self.navigation_state() if state is None else state
        # Keep only routing metadata, never View objects, actions, form inputs or decrypted values.
        if self.navigation_stack:
            previous = self.navigation_stack[-1]
            if (previous[0], previous[1], previous[3]) == (state[0], state[1], state[3]):
                return
        self.navigation_stack.append(state)
        del self.navigation_stack[:-64]

    def restore_navigation(self, state):
        mode, query, selection, directory = state
        if self.mode == 'marketplace' and mode != self.mode:
            if not self.marketplace.leave(lambda: self.restore_navigation(state)):
                return
        form = self.active_form()
        if form:
            form.wipe_passwords()
            form.dirty = False
        self.pending_leave = None
        if directory is not None:
            self.directory = Path(directory)
        self.set_mode(mode, record=False)
        self.navigation_selection = selection
        self.search.blockSignals(True)
        self.search.setText(query)
        self.search.blockSignals(False)
        self.changed()

    def pop_navigation(self):
        if self.navigation_stack:
            self.restore_navigation(self.navigation_stack.pop())
        elif self.mode != 'root':
            self.set_mode('root', record=False)
        else:
            self.close()

    def back(self):
        if self.clip_filter.popup.isVisible():
            self.clip_filter.hidePopup()
            return
        if self.plugin_help_id is not None:
            self.close_plugin_help()
            return
        if self.action_menu.isVisible():
            self.action_menu.close()
            return
        form = self.active_form()
        if form and not form.confirmation.isHidden():
            form.hide_discard_prompt()
            return
        if self.mode == 'marketplace':
            if self.confirm_form_leave(self.back):
                self.marketplace.back()
            return
        if not self.confirm_form_leave(self.pop_navigation):
            return
        current = self.plugin_calls.get(self.mode.removeprefix('plugin:')) if self.mode.startswith('plugin:') else None
        if current and current[0].navigation is not None:
            self.restore_navigation(current[0].navigation)
        elif self.mode == 'dmenu':
            self.close()
        else:
            self.pop_navigation()

    def web_mode(self):
        if self.active_form():
            return
        text = self.search.text()
        self.set_mode('web')
        self.search.setText(text.removeprefix('?').strip())

    def search_current_web(self):
        if self.active_form():
            return
        query = self.search.text().strip().removeprefix('?').strip()
        if query:
            subprocess.Popen(['xdg-open', 'https://www.google.com/search?' + urlencode({'q': query})],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.close()

    def eventFilter(self, watched, event):
        if watched is self.bar and event.type() == QEvent.Type.Resize:
            self.loading_bar.setGeometry(0, self.bar.height() - 2, self.bar.width(), 2)
        if event.type() == QEvent.Type.Resize and hasattr(self, 'empty_hint') and watched is self.list.viewport():
            self.empty_hint.setGeometry(self.list.viewport().rect())
            if self.list.viewMode() == QListWidget.ViewMode.IconMode:
                self.update_emoji_grid()
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            control = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if self.list.viewMode() == QListWidget.ViewMode.IconMode:
                horizontal = key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not control
                vertical = key in (Qt.Key.Key_Up, Qt.Key.Key_Down) or control and key in (Qt.Key.Key_N, Qt.Key.Key_P)
                if horizontal or vertical:
                    if self.rows:
                        direction = 1 if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_N) else -1
                        delta = direction * (self.emoji_columns if vertical else 1)
                        self.selection_moved = True
                        self.list.setCurrentRow(max(0, min(len(self.rows) - 1, self.list.currentRow() + delta)))
                    return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) or control and key in (Qt.Key.Key_N, Qt.Key.Key_P):
                delta = 1 if key in (Qt.Key.Key_Down, Qt.Key.Key_N) else -1
                self.move_selection(delta)
                return True
            if key == Qt.Key.Key_Tab:
                row = self.selected_result()
                if row is not None and row.kind == 'web':
                    if self.mode == 'root':
                        self.set_mode('web')
                    self.search.setText(row.value)
                    return True
            if key == Qt.Key.Key_Backspace and self.mode == 'files' and not self.search.text():
                if self.navigation_stack and self.navigation_stack[-1][0] == 'files':
                    self.back()
                else:
                    self.directory = self.directory.parent
                    self.set_mode('files', record=False)
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if not self.confirm_form_leave(self.close):
            event.ignore()
            return
        if self.marketplace is not None and not self.marketplace.leave(self.close):
            event.ignore()
            return
        self.closing_workers = True
        self.applications.stop()
        self.apps_refresh_timer.stop()
        self.root_ranking.cancel()
        if not self.plugin_runtime.quiescent:
            self.plugin_timer.stop()
            for token in self.plugin_tokens.values():
                token.set()
            self.plugin_calls.clear()
            self.wipe_form_passwords()
            self.clear_plugin_shortcuts()
            self.setEnabled(False)
            self.footer.setText('Closing plugins…')
            self.plugin_runtime.deactivate()
            event.ignore()
            return
        self.clear_plugin_shortcuts()
        self.toast.dismiss()
        self.wipe_form_passwords()
        self.navigation_stack.clear()
        self.pending_leave = None
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self.clip_filter)
            app.removeEventFilter(self.action_menu)
        self.clip_filter.popup.hide()
        self.action_menu.hide()
        self.revision += 1
        self.math_timer.stop()
        self.math_error_timer.stop()
        self.math_error = None
        self.web_timer.stop()
        self.extension_timer.stop()
        self.plugin_timer.stop()
        for token in self.plugin_tokens.values():
            token.set()
        self.plugin_views.clear()
        self.rendered_plugin_view = None
        self.plugin_calls.clear()
        self.plugin_runtime.deactivate()
        self.extra.clear()
        self.mode_rows.clear()
        self.rows.clear()
        self.list.clear()
        clear_layout(self.plugin_detail_layout)
        self.clip_preview_timer.stop()
        if self.reply:
            self.reply.abort()
        self.jobs.stop()
        if self.marketplace is not None:
            self.marketplace.shutdown()
        if self.package_lease is not None:
            self.package_lease.close()
            self.package_lease = None
        super().closeEvent(event)
        QApplication.quit()



def main():
    parser = argparse.ArgumentParser(prog='kite', description='KITE — a native Qt launcher')
    parser.add_argument('mode', nargs='?', default='root', choices=['root', 'calc', 'emoji', 'clipboard', 'web', 'files', 'windows'])
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--dmenu', action='store_true', help='read newline-separated choices; print selected line')
    args = parser.parse_args()
    dmenu_rows = options_from_bytes(sys.stdin.buffer.read(1_048_577)) if args.dmenu else None
    config = load_config(args.config)
    app = QApplication([sys.argv[0]])
    app.setDesktopFileName('kite')
    app.setApplicationName('kite')
    app.setApplicationDisplayName('KITE')
    app.setWindowIcon(QIcon(str(APP_ROOT / 'assets/icons/kite.png')))
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        raise RuntimeError('XDG_RUNTIME_DIR is required for per-user launcher IPC')
    if args.dmenu:
        window = Launcher(config, 'dmenu', args.config)
        window.mode_rows = dmenu_rows or []
        window.dmenu_selected = False
        window.apps_loading = False
        window.render()
        window.show()
        window.activateWindow()
        window.search.setFocus()
        result = app.exec()
        return result if window.dmenu_selected else (result or 1)
    socket_name = str(Path(runtime) / 'kite-qt')
    client = QLocalSocket()
    client.connectToServer(socket_name)
    if client.waitForConnected(150):
        client.write(json.dumps({'mode': args.mode}).encode() + b'\n')
        client.waitForBytesWritten(500)
        return 0
    lock = QLockFile(socket_name + '.lock')
    if not lock.tryLock(100):
        raise RuntimeError('Launcher is already starting; try again')
    QLocalServer.removeServer(socket_name)
    server = QLocalServer()
    if not server.listen(socket_name):
        raise RuntimeError(server.errorString())
    window = Launcher(config, args.mode, args.config)
    def connection():
        socket = server.nextPendingConnection()
        buffer = bytearray()
        def receive():
            buffer.extend(socket.readAll().data())
            if len(buffer) > 1024:
                socket.disconnectFromServer()
                return
            if b'\n' not in buffer:
                return
            try:
                message = json.loads(buffer.split(b'\n', 1)[0])
                if message.get('mode') == 'root':
                    window.close()
                elif message.get('mode') in ('calc', 'emoji', 'clipboard', 'web', 'files', 'windows'):
                    window.set_mode(message['mode'])
                    window.raise_()
                    window.activateWindow()
            except ValueError:
                pass
            socket.disconnectFromServer()
        socket.readyRead.connect(receive)
        socket.disconnected.connect(socket.deleteLater)
        if socket.bytesAvailable():
            receive()
    server.newConnection.connect(connection)
    window.show()
    window.activateWindow()
    window.search.setFocus()
    return app.exec()
