"""Bounded application metadata cache and Gio change notifications; never launch apps."""
from importlib import import_module
import json
import os
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QObject, Signal

from .core import Result
from .plugin_manager import _atomic_json, _read_json

MAX_CACHE = 2 * 1024 * 1024
FIELDS = ('id', 'title', 'subtitle', 'icon', 'kind', 'value', 'keywords')


class Applications(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'kite/applications.json'
        self.context = {key: os.environ.get(key) for key in (
            'HOME', 'XDG_DATA_HOME', 'XDG_DATA_DIRS', 'XDG_CURRENT_DESKTOP', 'LANG', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES')}
        self.stopped = threading.Event()
        self.thread = None

    def decode(self, data):
        if (not isinstance(data, dict) or set(data) != {'version', 'context', 'applications'}
                or type(data['version']) is not int or data['version'] != 1 or data['context'] != self.context
                or not isinstance(data['applications'], list) or len(data['applications']) > 10000):
            raise ValueError('Invalid application cache')
        rows, seen = [], set()
        for row in data['applications']:
            if (not isinstance(row, dict) or set(row) != set(FIELDS)
                    or any(not isinstance(value, str) or '\0' in value or len(value) > 8192 for value in row.values())
                    or not row['id'] or len(row['id']) > 1024 or row['id'] in seen or row['kind'] != 'app'
                    or row['value'] and (not Path(row['value']).is_absolute() or not row['value'].endswith('.desktop'))):
                raise ValueError('Invalid cached application')
            seen.add(row['id'])
            rows.append(Result(**row))
        return rows

    def load(self):
        try:
            return self.decode(_read_json(self.path, MAX_CACHE))
        except (OSError, ValueError, TypeError):
            return None  # A corrupt/missing cache is a miss, not an empty application catalog.

    def save(self, rows):
        data = {'version': 1, 'context': self.context,
                'applications': [{key: getattr(row, key) for key in FIELDS} for row in rows]}
        try:
            self.decode(data)
            if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_CACHE:
                raise ValueError('Application cache exceeds its size limit')
            _atomic_json(self.path, data)
            return True
        except (OSError, ValueError, TypeError) as error:
            print('Application cache unavailable:', error, file=sys.stderr)
            return False

    def start(self):
        if self.thread is None and not self.stopped.is_set():
            self.thread = threading.Thread(target=self.watch, daemon=True, name='kite-app-monitor')
            self.thread.start()

    def stop(self):
        self.stopped.set()

    def notify(self):
        if not self.stopped.is_set():
            try:
                self.changed.emit()
            except RuntimeError:  # The Qt owner may have been destroyed during shutdown.
                self.stop()

    def watch(self):
        context = monitor = source = None
        connection = None
        try:
            Gio = import_module('gi.repository.Gio')
            GLib = import_module('gi.repository.GLib')
            context = GLib.MainContext.new()
            context.push_thread_default()
            loop = GLib.MainLoop.new(context, False)
            monitor = Gio.AppInfoMonitor.get()
            def changed(*_):
                Gio.AppInfo.get_all()  # Re-arm notifications after invalidating Gio's cached database.
                self.notify()
            connection = monitor.connect('changed', changed)
            Gio.AppInfo.get_all()  # Prime Gio's application database and its directory monitors off the GUI thread.
            source = GLib.timeout_source_new(250)
            def alive(*_):
                if self.stopped.is_set():
                    loop.quit()
                    return False
                return True
            source.set_callback(alive)
            source.attach(context)
            self.notify()  # Reconcile changes during monitor startup as well as while KITE was closed.
            if not self.stopped.is_set():
                loop.run()
        except Exception as error:
            print('Application change monitor unavailable:', error, file=sys.stderr)
        finally:
            if source is not None:
                source.destroy()
            if connection is not None:
                monitor.disconnect(connection)
            if context is not None:
                context.pop_thread_default()
