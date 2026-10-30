"""Check the optional Rust plugin on synthetic rows; never enable it in user settings."""
import argparse
import hashlib
import json
import re
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
from unittest.mock import patch

from main.core import Result, load_config
from main.plugin_protocol import Framer, encode, request
from main.ranking import rank
from main.ranking_plugin import catalog_request, make_request, query_request, validate_result

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'examples/rust-ranking'


def params(rows, query='', usage=None, aliases=None, favorites=(), sensitivity='medium', learn=True):
    return make_request(rows, query, usage or {}, aliases or {}, favorites,
                        {'sensitivity': sensitivity, 'learn_queries': learn}, 60)


class Peer:
    def __init__(self, binary):
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, cwd=PACKAGE)
        self.framer, self.messages, self.sequence = Framer(), [], 0

    def send(self, method, values):
        self.sequence += 1
        self.process.stdin.write(encode(request(self.sequence, method, values)))
        self.process.stdin.flush()
        return self.sequence

    def receive(self):
        deadline = time.monotonic() + 5
        while not self.messages and time.monotonic() < deadline:
            if select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                chunk = os.read(self.process.stdout.fileno(), 65536)
                assert chunk, 'Rust backend exited unexpectedly'
                self.messages.extend(self.framer.feed(chunk))
        assert self.messages, 'Rust response timed out'
        return self.messages.pop(0)

    def initialize(self):
        identity = self.send('initialize', {'api_version': 2, 'wire_version': 1, 'plugin_id': 'rust-ranking',
                            'directory': str(PACKAGE), 'config': {}, 'environment': {}})
        assert self.receive() == {'jsonrpc': '2.0', 'id': identity, 'result': {
            'api_version': 2, 'wire_version': 1, 'plugin_id': 'rust-ranking', 'has_handler': False, 'ranking_version': 2}}

    def rank(self, values, known_ids=None):
        identity = self.send('rank', values)
        reply = self.receive()
        assert reply['id'] == identity and 'result' in reply, reply
        return validate_result(reply['result'], values, known_ids)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.write(encode(request(None, 'shutdown')))
            self.process.stdin.flush()
        try:
            assert self.process.wait(timeout=5) == 0
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.process.stdin.close()
            self.process.stdout.close()


def policy_and_rpc(binary):
    peer = Peer(binary)
    try:
        peer.send('rank', params([]))
        assert 'error' in peer.receive(), 'Ranking before initialize was accepted'
        peer.send('initialize', {'api_version': 99, 'wire_version': 1, 'plugin_id': 'rust-ranking',
                                 'directory': str(PACKAGE), 'config': {}, 'environment': {}})
        assert 'error' in peer.receive(), 'Incompatible API was accepted'
        peer.initialize()
        rows = [Result('exact', 'Code', kind='app'), Result('prefix', 'Code Editor', kind='app'),
                Result('word', 'Mozilla Code', kind='app'), Result('keyword', 'Other', keywords='code', kind='app')]
        now = time.time()
        shared = [params(rows), params(rows, 'code'), params(rows, 'code', aliases={'prefix': 'code'}),
                  params(rows, 'code', usage={'prefix': {'count': 20, 'last': now, 'terms': ['code']}}),
                  params(rows, 'code', usage={'prefix': {'count': 20, 'last': now - 18 * 86400, 'terms': ['code']}}),
                  params(rows, 'code', usage={'prefix': {'count': 20, 'last': now, 'terms': ['code']}}, learn=False)]
        for p in shared:
            expected = [row.id for row in rank(rows, p['query'], p['usage'], p['limit'], aliases=p['aliases'],
                        sensitivity=p['sensitivity'], learn_queries=p['learn_queries'])]
            assert peer.rank(p) == expected, (p['query'], expected)
        for tie_rows, query, history in (
            ([Result('a', 'Browser'), Result('b', 'Other', subtitle='Browser'),
              Result('c', 'Browser Tool'), Result('d', 'Hidden', keywords='browser')], 'browser', {}),
            ([Result('z', 'Alpha', kind='mode'), Result('a', 'Alpha', kind='app')], 'alpha', {}),
            ([Result('z', 'Alpha'), Result('a', 'Alpha')], 'alpha',
             {'z': {'count': 30, 'last': now}, 'a': {'anchor': now, 'last': now}}),
        ):
            p = params(tie_rows, query, usage=history)
            expected = [r.id for r in rank(tie_rows, query, history)]
            assert peer.rank(p) == expected, (query, expected)
        assert peer.rank(params(rows, favorites=['keyword', 'word']))[:2] == ['keyword', 'word']
        crowded = [Result(f'app:{i}', f'Alpha {i}', kind='app') for i in range(120)]
        commands = [Result('plugin:fixture', 'Plugin command', kind='plugin'),
                    Result('cmd:fixture', 'System command', kind='mode'),
                    Result('cmd:marketplace', 'Marketplace', kind='marketplace')]
        ordered = peer.rank(params(crowded + commands, favorites=['app:119'],
                                  usage={'app:0': {'count': 10000, 'last': now}}))
        assert len(ordered) == 60 and ordered[0] == 'app:119'
        assert set(ordered[1:4]) == {row.id for row in commands}, 'Apps crowded plugins out before sectioning'
        assert peer.rank(params(rows, 'zzzz')) == []
        typo = [Result('firefox', 'Firefox', kind='app')]
        assert peer.rank(params(typo, 'firefoc')) == ['firefox']
        assert peer.rank(params(typo, 'firefoc', sensitivity='high')) == []
        assert peer.rank(params(typo, 'frfx', sensitivity='high')) == ['firefox']
        assert peer.rank(params(typo, 'firre')) == [], 'Shadow typo leaked into visible results'
        folded = [Result('accent', 'Café Straße Æther', kind='app')]
        for query in ('cafe', 'strasse', 'aether'):
            assert peer.rank(params(folded, query)) == ['accent']
        # Case-sensitive source boundaries survive cached normalization, and revisions echo exactly.
        p = params([Result('camel', 'VisualStudioCode', kind='app')], 'vsc')
        p['catalog_revision'] = 1234
        assert peer.rank(p) == ['camel']
        assert peer.rank(params([])) == []
        assert peer.rank(params(typo, 'firefox')) == ['firefox'], 'Catalog reload retained stale candidates'
        bad = params(typo, 'firefox')
        for key, value in (('protocol_version', 9), ('candidates', bad['candidates'] * 2),
                           ('limit', 0), ('usage', {'firefox': {'count': None}}),
                           ('favorites', ['unknown']), ('surprise', True)):
            invalid = dict(bad, **{key: value})
            peer.send('rank', invalid)
            assert 'error' in peer.receive(), (key, value)
            assert peer.rank(bad) == ['firefox'], 'An invalid request poisoned the catalog'
        # Invalid JSON, duplicate keys, nesting and >4-MiB input are drained; following RPC survives.
        for raw in (b'{broken}\n', b'{"id":1,"id":2}\n',
                    b'{"jsonrpc":"2.0","id":null,"method":"shutdown","params":{}}\n', b'[' * 65 + b'0' + b']' * 65 + b'\n',
                    b'x' * (4 * 1024 * 1024 + 1) + b'\n'):
            peer.process.stdin.write(raw)
            peer.process.stdin.flush()
            assert peer.rank(bad) == ['firefox']
        peer.process.stdin.write(encode(request(None, 'cancel', {'id': 'obsolete'})))
        peer.process.stdin.flush()
        assert peer.rank(bad) == ['firefox']
        cached = dict(bad, catalog_revision=900)
        peer.send('catalog', catalog_request(cached))
        assert peer.receive()['result'] == {'catalog_revision': 900, 'count': 1}
        for text in ('firefox', 'firefoc', 'frfx'):
            query = query_request(dict(cached, query=text))
            assert 'candidates' not in query and len(encode(request('q', 'rank', query))) < 256
            assert peer.rank(query, {'firefox'}) == ['firefox']
        peer.send('rank', query_request(dict(cached, catalog_revision=899)))
        assert 'error' in peer.receive(), 'Stale catalog query was accepted'
        invalid_catalog = catalog_request(dict(cached, catalog_revision=901))
        invalid_catalog['candidates'] = invalid_catalog['candidates'] * 2
        peer.send('catalog', invalid_catalog)
        assert 'error' in peer.receive(), 'Duplicate candidate catalog was accepted'
        assert peer.rank(query_request(cached), {'firefox'}) == ['firefox'], 'Failed load poisoned the old catalog'
        empty_catalog = catalog_request(dict(params([]), catalog_revision=901))
        peer.send('catalog', empty_catalog)
        assert peer.receive()['result'] == {'catalog_revision': 901, 'count': 0}
        assert peer.rank(query_request(dict(params([]), catalog_revision=901)), set()) == []
    finally:
        peer.close()


def qt_plugin():
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication
    from main import launcher
    from main.plugin_runtime import Session
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix='kite-rust-rpc-') as name, patch.dict(os.environ, {
        'XDG_CONFIG_HOME': name + '/config', 'XDG_DATA_HOME': name + '/data',
        'XDG_STATE_HOME': name + '/state', 'XDG_CACHE_HOME': name + '/cache',
    }):
        root = Path(name)
        authored = root / 'config/kite/plugin'
        authored.mkdir(parents=True)
        (authored / 'rust-ranking').symlink_to(PACKAGE, target_is_directory=True)
        tool = authored / 'tool-fixture'
        tool.mkdir()
        (tool / 'plugin.json').write_text(json.dumps({'id': 'tool-fixture', 'name': 'Tool fixture',
            'api_version': 2, 'role': 'tool', 'command': ['/bin/false']}))
        config_path = root / 'config/kite/config.yaml'
        config_path.write_text('ranking:\n  engine: rust-ranking\nfavorites:\n  - firefox\nweb:\n  enabled: false\nmath:\n  enabled: false\n')
        original = launcher.Jobs.builtin

        def builtin(self, key, p, callback, timeout=2500):
            if key == 'apps':
                QTimer.singleShot(0, lambda: callback({'results': [
                    {'id': 'firefox', 'title': 'Firefox', 'kind': 'app'},
                    {'id': 'terminal', 'title': 'Terminal', 'kind': 'app'}]}))
            else:
                original(self, key, p, callback, timeout)

        sent = []
        original_send = Session.send
        def tracked(session, message):
            if session.plugin_id == 'rust-ranking' and message.get('method') in ('rank', 'catalog'):
                sent.append((message['method'], message['params']))
            return original_send(session, message)

        with patch.object(launcher.Jobs, 'builtin', builtin), patch.object(launcher, 'STATE', root / 'usage.json'), \
                patch.object(Session, 'send', tracked), patch.object(launcher.Applications, 'start', lambda self: None):
            window = launcher.Launcher(load_config(config_path), config_path=config_path,
                                      favorites_path=root / 'favorites.sqlite')
            window.show()
            startup_texts = [window.empty_hint.text()]
            render = window.render
            def observed_render():
                render()
                if window.root_ranking.pending and window.root_ranking.ordered is None and window.apps_cached:
                    assert any(row.kind == 'app' for row in window.rows), 'Cached apps waited for Rust initialization'
                    assert window.loading_bar.isHidden(), 'Cached startup flashed a ranking loader'
                if not window.rows and window.is_busy():
                    startup_texts.append(window.empty_hint.text())
                    assert window.loading_bar.isHidden(), 'Two startup loading indicators were shown'
            window.render = observed_render

            def until(test):
                end = time.monotonic() + 12
                while time.monotonic() < end and not test():
                    app.processEvents()
                    time.sleep(.01)
                assert test(), (window.root_ranking.failed, window.plugin_runtime.blocked, [r.id for r in window.rows])

            try:
                until(lambda: not window.root_ranking.pending and window.root_ranking.ordered is not None and bool(window.rows))
                assert window.plugin_runtime.active.ranking_version == 2
                assert set(startup_texts) == {'Loading applications…'}, startup_texts
                headings = [window.list.item(i).text() for i in range(window.list.count())
                            if window.list.item(i).data(Qt.ItemDataRole.UserRole + 2)]
                assert headings == ['Favorites', 'Plugins', 'Applications'], headings
                session = window.plugin_runtime.active
                assert session.direct and session.unit is None, 'Rust was launched as a systemd unit'
                pid = session.process.processId()
                assert Path(f'/proc/{pid}/exe').resolve() == (PACKAGE / 'target/release/kite-flea-ranking').resolve()
                status = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines())
                assert int(status['PPid']) == os.getpid() and int(status['Threads']) == 1
                old_items = [window.list.item(i) for i in range(window.list.count())]
                stale = next(row for row in window.rows if row.id == 'firefox')
                resets = []
                window.list.model().modelReset.connect(lambda: resets.append(True))
                window.search.setText('firefoc')
                assert window.root_ranking.pending and any(row.id == 'firefox' for row in window.rows)
                assert not window.is_busy() and window.loading_bar.isHidden(), 'Warm ranking flashed a loader'
                assert all(window.list.item(i) is item for i, item in enumerate(old_items))
                assert window.selected_result() is None and not window.primary_button.isEnabled()
                with patch.object(window.jobs, 'builtin', side_effect=AssertionError('Stale result activated')):
                    window.activate_result(stale)
                until(lambda: not window.root_ranking.pending and any(r.id == 'firefox' for r in window.rows))
                assert not window.root_ranking.failed
                assert window.plugin_runtime.active.process.processId() == pid, 'RPC child was not persistent'
                window.search.setText('terminal')
                until(lambda: not window.root_ranking.pending and any(r.id == 'terminal' for r in window.rows))
                assert not resets, 'Root query refresh reset the list model'
                assert sum(method == 'catalog' for method, _ in sent) == 1, 'Catalog resent for a query'
                assert all('candidates' not in p for method, p in sent if method == 'rank')
                window.usage['terminal'] = {'count': 10, 'last': time.time()}
                window.root_ranking.invalidate()
                window.render()
                until(lambda: not window.root_ranking.pending)
                assert sum(method == 'catalog' for method, _ in sent) == 2, 'Usage revision was not reloaded'
                window.cfg['aliases'].append({'id': 'terminal', 'alias': 'zzterm'})
                window.search.setText('zzterm')
                until(lambda: not window.root_ranking.pending and any(r.id == 'terminal' for r in window.rows))
                assert sum(method == 'catalog' for method, _ in sent) == 3, 'Aliases did not invalidate the catalog'
                window.apps.extend(Result(f'app:extra:{i}', f'Crowded app {i}', kind='app') for i in range(120))
                window.root_ranking.invalidate()
                window.search.clear()
                command_ids = {row.id for row in window.commands}
                until(lambda: not window.root_ranking.pending and command_ids <= {row.id for row in window.rows})
                assert 'plugin:tool-fixture' in {row.id for row in window.rows}, 'Installed tool vanished from Plugins'
                assert len(window.rows) == window.cfg['results']['limit']
            finally:
                window.close()
                until(lambda: not window.isVisible() and window.plugin_runtime.quiescent)


def main():
    records = re.findall(r'\| `(src/backend/[^`]+)` \| `([a-f0-9]{64})` \|',
                         (PACKAGE / 'PROVENANCE.md').read_text())
    assert len(records) == 5, 'Vendored matcher provenance is incomplete'
    for filename, digest in records:
        assert hashlib.sha256((PACKAGE / filename).read_bytes()).hexdigest() == digest, filename
    assert hashlib.sha256((PACKAGE / 'LICENSE-FLEA').read_bytes()).hexdigest() == (
        'd2a2191fc6fa3fb43cd6dfe0a7b156ded8f4a5e23c498ef1111788c37ec4f0c4')
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-qt', action='store_true', help='Only direct RPC/policy checks; no systemd integration')
    args = parser.parse_args()
    binary = PACKAGE / 'target/release/kite-flea-ranking'
    assert binary.is_file(), 'Build first: cargo build --release --locked --manifest-path examples/rust-ranking/Cargo.toml'
    policy_and_rpc(binary.resolve())
    if not args.skip_qt:
        qt_plugin()
    print('Rust ranking OK: shared KITE ordering, native typo/folding rules, bounded RPC/reloads' +
          (', persistent Qt worker and cleanup' if not args.skip_qt else ' (Qt/systemd skipped)'))


if __name__ == '__main__':
    main()
