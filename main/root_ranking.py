"""Revisioned root catalog plus small queries over the shared plugin runtime."""
import sys
from threading import Event

from PySide6.QtCore import QObject, QTimer

from .plugin_runtime import Call
from .ranking_plugin import make_request, query_request


class RootRanking(QObject):
    def __init__(self, launcher):
        super().__init__(launcher)
        self.host = launcher
        self.call = None
        self.snapshot = self.ordered = self.catalog = None
        self.catalog_rows, self.aliases, self.favorites = [], {}, []
        self.usage = None
        self.by_id, self.known_ids = {}, frozenset()
        self.sequence = self.catalog_revision = 0
        self.request_key = None
        self.failed = False

    @property
    def external(self):
        if self.host.closing_workers or self.host.cfg['ranking']['engine'] == 'builtin':
            return None
        entry = self.host.plugins.get(self.host.cfg['ranking']['engine'])
        return entry if entry and entry[0].get('role') == 'ranking' and not self.failed else None

    @property
    def pending(self):
        return self.call is not None

    def current(self, call):
        return self.call is call and self.host.mode == 'root' and not call.cancelled.is_set()

    def cancel_request(self):
        if self.call:
            self.call.cancelled.set()
        self.call = None

    def invalidate(self):
        """Call when owned candidate fields or in-place usage records change."""
        self.cancel_request()
        self.catalog = self.request_key = None

    def cancel(self):
        self.invalidate()
        self.snapshot = self.ordered = None
        self.catalog_rows, self.by_id, self.known_ids = [], {}, frozenset()
        self.aliases, self.favorites, self.usage = {}, [], None

    def refresh(self):
        self.cancel()
        self.failed = False

    def rows(self, candidates, query, aliases):
        host = self.host
        entry = self.external
        options = host.cfg['ranking']
        if not entry or host.apps_loading:
            return host.bundled_plugins['ranking'].rank_rows(
                candidates, query, host.usage, host.cfg['results']['limit'], aliases=aliases,
                sensitivity=options['sensitivity'], learn_queries=options['learn_queries'])
        try:
            if (self.catalog is None or candidates != self.catalog_rows or aliases != self.aliases
                    or host.cfg['favorites'] != self.favorites or host.usage is not self.usage
                    or options['learn_queries'] != self.catalog['learn_queries']):
                self.cancel_request()
                self.catalog_revision += 1
                self.catalog = make_request(candidates, '', host.usage, aliases, host.cfg['favorites'],
                                            options, host.cfg['results']['limit'])
                self.catalog['catalog_revision'] = self.catalog_revision
                self.catalog_rows, self.aliases = list(candidates), dict(aliases)
                self.favorites, self.usage = list(host.cfg['favorites']), host.usage
                self.by_id = {row.id: row for row in candidates}
                self.known_ids = frozenset(self.by_id)
                self.request_key = None
            key = (entry[0]['id'], query, host.cfg['results']['limit'], options['sensitivity'],
                   options['learn_queries'], self.catalog_revision)
            if key != self.request_key:
                self.cancel_request()
                self.sequence += 1
                self.snapshot = dict(self.catalog, query=query, limit=host.cfg['results']['limit'],
                                     sensitivity=options['sensitivity'], learn_queries=options['learn_queries'])
                query_request(self.snapshot)
                self.request_key = key
                call = Call(entry[0]['id'], self.sequence, 'rank', self.snapshot, Event(), known_ids=self.known_ids)
                self.call = call
                call.signals.finished.connect(lambda pid, rev, value, error: self.finished(call, value, error))
                host.plugin_runtime.submit(call, entry[1])
            # Show cached apps even before the first external reply; activation remains blocked while pending.
            if self.ordered is None and getattr(host, 'apps_cached', False):
                return host.bundled_plugins['ranking'].rank_rows(
                    candidates, query, host.usage, host.cfg['results']['limit'], aliases=aliases,
                    sensitivity=options['sensitivity'], learn_queries=options['learn_queries'])
            # Keep the previous answer visible while pending. The frontend blocks stale activation.
            return [self.by_id[i] for i in self.ordered if i in self.by_id] if self.ordered is not None else []
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            self.failed = True
            self.cancel()
            host.plugin_runtime.deactivate()
            print(f'Ranking plugin failed; using built-in policy: {error}', file=sys.stderr)
            return self.rows(candidates, query, aliases)

    def finished(self, call, ordered, error):
        if not self.current(call):
            return
        self.call = None
        if error:
            self.failed = True
            self.snapshot = self.ordered = None
            self.host.plugin_runtime.deactivate()
            print(f'Ranking plugin failed; using built-in policy: {error}', file=sys.stderr)
        else:
            self.ordered = ordered
        QTimer.singleShot(0, self.host.render)
