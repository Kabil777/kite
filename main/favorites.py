"""Small SQLite store for ordered launcher favorites, seeded from legacy YAML once."""
import os
from pathlib import Path
import sqlite3


def default_path():
    state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state'))
    return state / 'kite' / 'favorites.sqlite'


class FavoritesStore:
    def __init__(self, path=None, initial=()):
        self.path = Path(path) if path is not None else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=2) as db:
            db.execute('CREATE TABLE IF NOT EXISTS favorites (id TEXT PRIMARY KEY, position INTEGER NOT NULL)')
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA user_version').fetchone()[0] < 1:
                db.executemany('INSERT OR IGNORE INTO favorites VALUES (?, ?)',
                               ((item, index) for index, item in enumerate(dict.fromkeys(initial))))
                db.execute('PRAGMA user_version = 1')

    def ids(self):
        with sqlite3.connect(self.path, timeout=2) as db:
            return [row[0] for row in db.execute('SELECT id FROM favorites ORDER BY position, id')]

    def add(self, item):
        if not isinstance(item, str) or not item:
            raise ValueError('Favorite ID must be a nonempty string')
        with sqlite3.connect(self.path, timeout=2) as db:
            position = db.execute('SELECT COALESCE(MAX(position), -1) + 1 FROM favorites').fetchone()[0]
            return db.execute('INSERT OR IGNORE INTO favorites VALUES (?, ?)', (item, position)).rowcount == 1

    def remove(self, item):
        with sqlite3.connect(self.path, timeout=2) as db:
            removed = db.execute('DELETE FROM favorites WHERE id = ?', (item,)).rowcount == 1
            if removed:
                self._compact(db)
            return removed

    def move(self, item, offset):
        if offset not in (-1, 1):
            raise ValueError('Favorite offset must be -1 or 1')
        with sqlite3.connect(self.path, timeout=2) as db:
            rows = [row[0] for row in db.execute('SELECT id FROM favorites ORDER BY position, id')]
            try:
                index = rows.index(item)
            except ValueError:
                return False
            target = index + offset
            if not 0 <= target < len(rows):
                return False
            rows[index], rows[target] = rows[target], rows[index]
            db.executemany('UPDATE favorites SET position = ? WHERE id = ?',
                           ((position, value) for position, value in enumerate(rows)))
            return True

    @staticmethod
    def _compact(db):
        rows = [row[0] for row in db.execute('SELECT id FROM favorites ORDER BY position, id')]
        db.executemany('UPDATE favorites SET position = ? WHERE id = ?',
                       ((position, value) for position, value in enumerate(rows)))
