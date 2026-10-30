"""Synthetic packages only: no network, user stores, clipboard or application activation."""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from unittest.mock import patch
from threading import Event
from types import SimpleNamespace
import zipfile

from main import plugin_manager as pm
from main.core import Result, load_config
from main.plugin_api import _read_manifest, load_plugins
from main.plugin_protocol import Framer, encode, from_wire, request, response
from main.ranking_plugin import make_request, validate_request, validate_result

ROOT = Path(__file__).resolve().parents[1]


def rejects(action):
    try:
        action()
    except (ValueError, TypeError, OSError):
        return
    raise AssertionError('Invalid input was accepted')


def protocol():
    candidates = [Result('a', 'Alpha', kind='app'), Result('b', 'Beta', kind='app')]
    options = {'sensitivity': 'medium', 'learn_queries': False}
    params = make_request(candidates, 'a', {'a': {'count': 3, 'terms': ['private']}},
                          {'a': 'al'}, ['b'], options, 60)
    assert params['usage'] == {'a': {'count': 3}}
    for bad in ({'catalog_revision': 0, 'ordered_ids': ['missing']},
                {'catalog_revision': 0, 'ordered_ids': ['a', 'a']},
                {'catalog_revision': 1, 'ordered_ids': []},
                {'catalog_revision': False, 'ordered_ids': []}):
        rejects(lambda bad=bad: validate_result(bad, params))
    for key, value in (('protocol_version', True), ('limit', -1), ('candidates', params['candidates'] * 2)):
        rejects(lambda key=key, value=value: validate_request(dict(params, **{key: value})))
    builtin = next(module for manifest, module in load_plugins(ROOT / 'main/bundled_plugins')
                   if manifest['id'] == 'ranking')
    expected = [row.id for row in builtin.rank_rows(candidates, 'a', {'a': {'count': 3}}, 60, aliases={'a': 'al'},
                                                   learn_queries=False)]
    assert validate_result(builtin.rank(params, None), params) == expected
    if shutil.which('node'):
        messages = [request('init', 'initialize', {'api_version': 2, 'wire_version': 1, 'plugin_id': 'node-tool'}),
                    request('search', 'search', {'query': 'Hi'}),
                    request('form', 'handle_event', {'event': {'node_id': 'greeting', 'name': 'activate', 'payload': {}}}),
                    request('submit', 'handle_event', {'event': {'node_id': 'greeting-form', 'name': 'submit',
                                                                 'payload': {'inputs': {'name': 'Fixture'}}}})]
        output = subprocess.run(['node', 'backend.mjs'], cwd=ROOT / 'examples/node-tool',
                                input=b''.join(map(encode, messages)), capture_output=True, check=True, timeout=5)
        responses = Framer().feed(output.stdout)
        assert from_wire(responses[1]['result']).results[0].title == 'Hi'
        assert from_wire(responses[2]['result']).nodes[0].type == 'form'
        assert from_wire(responses[3]['result']).title == 'Hello, Fixture'
        rank_init = request('init', 'initialize', {'api_version': 2, 'wire_version': 1, 'plugin_id': 'node-ranking'})
        output = subprocess.run(['node', 'backend.mjs'], cwd=ROOT / 'examples/node-ranking',
                                input=encode(rank_init) + encode(request('rank', 'rank', params)),
                                capture_output=True, check=True, timeout=5)
        assert validate_result(Framer().feed(output.stdout)[1]['result'], params) == ['b', 'a']


def cached_transport():
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from main.plugin_runtime import Call, Session
    app = QApplication.instance() or QApplication([])
    owner = QObject()
    owner.suspended = False
    owner.manager = SimpleNamespace(activation_directory=lambda identifier, directory: directory)
    owner.is_current = lambda call: not call.cancelled.is_set()
    owner.jobs = SimpleNamespace(cancel=lambda key: None)
    p = params = make_request([Result('a', 'Alpha')], '', {}, {}, [],
                             {'sensitivity': 'medium', 'learn_queries': True}, 60)
    p['catalog_revision'] = 1
    sent, finished = [], []
    session = Session(owner, 'fixture', ROOT)
    session.initialized = True
    session.ranking_version = 2
    session.send = lambda message: sent.append(message) or True
    first = Call('fixture', 1, 'rank', p, Event(), known_ids=frozenset({'a'}))
    second = Call('fixture', 2, 'rank', dict(p, query='new'), Event(), known_ids=frozenset({'a'}))
    second.signals.finished.connect(lambda pid, rev, value, error: finished.append((rev, value, error)))
    try:
        session.submit(first)
        catalog_id = sent[-1]['id']
        assert sent[-1]['method'] == 'catalog'
        session.submit(second)
        assert sent[-1]['method'] == 'cancel' and sent[-1]['params']['id'] == catalog_id
        session.message(response(catalog_id, {'catalog_revision': 1, 'count': 1}))
        assert sent[-1]['method'] == 'rank' and 'candidates' not in sent[-1]['params']
        assert sum(message['method'] == 'catalog' for message in sent) == 1
        query_id = sent[-1]['id']
        session.message(response(catalog_id, {'catalog_revision': 1, 'count': 1}))  # late duplicate ignored
        session.message(response(query_id, {'catalog_revision': 1, 'ordered_ids': ['a']}))
        assert finished == [(2, ['a'], None)]
        third = Call('fixture', 3, 'rank', dict(params, catalog_revision=2), Event(), known_ids=frozenset({'a'}))
        session.submit(third)
        rejects(lambda: session.message(response(sent[-1]['id'], {'catalog_revision': 2, 'count': 9})))
    finally:
        session.terminate()
    restarted = Session(owner, 'fixture', ROOT)
    restarted.initialized = True
    restarted.ranking_version = 2
    restarted.send = lambda message: sent.append(message) or True
    try:
        restarted.submit(Call('fixture', 4, 'rank', p, Event(), known_ids=frozenset({'a'})))
        assert sent[-1]['method'] == 'catalog', 'A restarted worker incorrectly reused a previous process catalog'
    finally:
        restarted.terminate()
    assert app is not None


def packages(manager):
    manager.config_path.parent.mkdir(parents=True, exist_ok=True)
    manager.config_path.write_text('plugins: {}\n')
    source = 'https://raw.githubusercontent.com/fixture/catalog/main/index.json'
    manager.set_source(source)
    for identifier in ('node-tool', 'node-ranking'):
        contents = io.BytesIO()
        with zipfile.ZipFile(contents, 'w') as archive:
            for name in ('plugin.json', 'backend.mjs'):
                archive.writestr(identifier + '/' + name, (ROOT / 'examples' / identifier / name).read_bytes())
        payload = contents.getvalue()
        release = {'version': '1.0.0', 'api_version': 2,
                   'url': f'https://github.com/fixture/plugins/releases/download/v1.0.0/{identifier}.zip',
                   'sha256': hashlib.sha256(payload).hexdigest()}
        entry = dict(release, id=identifier, name=_read_manifest(ROOT / 'examples' / identifier)['name'],
                     summary='fixture', author='Fixture', repository='https://github.com/fixture/plugins',
                     latest='1.0.0', releases=[release], catalog_url=source)
        with patch.object(pm, 'download', return_value=payload), patch.object(pm, 'units_clear', return_value=True):
            prepared = manager.prepare(entry)
            try:
                manager.install(prepared)
            finally:
                manager.discard(prepared)
        payload_path = manager.payload(identifier, manager.registry()['installed'][identifier])
        assert not (payload_path / '__init__.py').exists()
    found = manager.discover({}, lambda name, error: (_ for _ in ()).throw(AssertionError(error)))
    assert {m['id'] for m, _ in found} == {'node-tool', 'node-ranking'}
    with patch.object(pm, 'units_clear', return_value=True):
        manager.set_enabled('node-ranking', False)
    assert {m['id'] for m, _ in manager.discover({}, lambda *_: None)} == {'node-tool'}
    with patch.object(pm, 'units_clear', return_value=True):
        manager.set_enabled('node-ranking', True)
    assert {m['id'] for m, _ in manager.discover({'node-tool': {'enabled': False}}, lambda *_: None)} == {'node-ranking'}
    package = manager.authored / 'invalid'
    package.mkdir(parents=True)
    base = {'id': 'invalid', 'name': 'Invalid', 'api_version': 2, 'command': ['node', 'backend.mjs']}
    for changes in ({'command': 'node backend.mjs'}, {'command': ['node\0']}, {'entry': '__init__.py'},
                    {'role': 'unknown'}, {'command': ['../outside']}, {'execution': 'unknown'}, {'execution': 'child'}):
        (package / 'plugin.json').write_text(json.dumps(dict(base, **changes)))
        rejects(lambda: _read_manifest(package))
    shutil.rmtree(package)


FIXTURE = '''import readline from 'node:readline';
const send = (id, result) => process.stdout.write(JSON.stringify({jsonrpc:'2.0', id, result})+'\\n');
for await (const line of readline.createInterface({input:process.stdin})) {
 const m=JSON.parse(line), p=m.params||{};
 if(m.method==='shutdown') break;
 if(m.method==='cancel') continue;
 if(m.method==='initialize') send(m.id,{api_version:2,wire_version:1,plugin_id:p.plugin_id,has_handler:false});
 if(m.method==='rank') {
   if(p.query==='crash') process.exit(1);
   if(p.query==='hang') continue;
   if(p.query==='flood') { process.stdout.write('x'.repeat(5*1024*1024)); continue; }
   if(p.query==='slow') await new Promise(resolve=>setTimeout(resolve,180));
   const ids=p.query==='bad' ? ['unknown'] : p.candidates.map(r=>r.id).reverse();
   send(m.id,{catalog_revision:p.catalog_revision,ordered_ids:ids.slice(0,p.limit)});
 }
}
'''


def runtime(manager, temporary):
    if not shutil.which('node'):
        print('SKIP Node execution: node is not installed')
        return
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from main import launcher
    app = QApplication.instance() or QApplication([])
    original_builtin = launcher.Jobs.builtin

    def builtin(self, key, params, callback, timeout=2500):
        if key == 'apps':
            QTimer.singleShot(0, lambda: callback({'results': [
                {'id': 'a', 'title': 'Alpha', 'kind': 'app'}, {'id': 'b', 'title': 'Beta', 'kind': 'app'}]}))
        else:
            original_builtin(self, key, params, callback, timeout)

    directory = manager.authored / 'fixture-ranker'
    directory.mkdir(parents=True)
    (directory / 'plugin.json').write_text(json.dumps({'id': 'fixture-ranker', 'name': 'Fixture', 'api_version': 2,
                                                       'role': 'ranking', 'command': ['node', 'backend.mjs']}))
    (directory / 'backend.mjs').write_text(FIXTURE)
    python = manager.authored / 'python-ranker'
    python.mkdir()
    (python / 'plugin.json').write_text(json.dumps({'id': 'python-ranker', 'name': 'Python ranker',
                                                    'api_version': 2, 'role': 'ranking'}))
    (python / '__init__.py').write_text(
        "def rank(p, host):\n    return {'catalog_revision': p['catalog_revision'], 'ordered_ids': ['a']}\n")
    service = manager.authored / 'node-service'
    service.mkdir()
    (service / 'plugin.json').write_text(json.dumps({'id': 'node-service', 'name': 'Node service',
                        'api_version': 2, 'command': ['node', 'backend.mjs']}))
    (service / 'backend.mjs').write_text('''import readline from 'node:readline';
const send=m=>process.stdout.write(JSON.stringify({jsonrpc:'2.0',...m})+'\\n');
let parent;
for await(const line of readline.createInterface({input:process.stdin})) {
 const m=JSON.parse(line), p=m.params||{};
 if(m.method==='shutdown') break;
 if(m.method==='initialize') send({id:m.id,result:{api_version:2,wire_version:1,plugin_id:p.plugin_id,has_handler:false}});
 if(m.method==='search') {
  parent=m.id;
  send({id:'service:1',method:'host.call',params:{parent,name:'math.calculate',args:{query:'2*3'}}});
 }
 if(m.id==='service:1' && m.result) send({id:parent,result:{kind:'view',data:{results:[{id:'six',title:m.result[0].title}]}}});
}
''')
    manager.config_path.write_text('ranking:\n  engine: fixture-ranker\nweb:\n  enabled: false\nplugins: {}\n')
    with patch.object(launcher.Jobs, 'builtin', builtin), patch.object(launcher, 'STATE', temporary / 'usage.json'), \
            patch.object(launcher.Applications, 'start', lambda self: None):
        window = launcher.Launcher(load_config(manager.config_path), config_path=manager.config_path,
                                   favorites_path=temporary / 'favorites.sqlite')
        window.show()

        def until(test, timeout=12):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not test():
                app.processEvents()
                time.sleep(.01)
            assert test(), (window.mode, [row.title for row in window.rows], window.root_ranking.failed,
                            window.plugin_runtime.blocked, window.plugin_runtime.pending)

        try:
            until(lambda: not window.root_ranking.pending and window.root_ranking.ordered is not None
                  and [row.id for row in window.rows if row.id in ('a', 'b')] == ['b', 'a'])
            assert not window.root_ranking.failed, 'Fixture worker failed to initialize'
            assert [r.id for r in window.rows].index('b') < [r.id for r in window.rows].index('a')
            unit = window.plugin_runtime.active.unit
            window.search.setText('slow')
            window.search.setText('latest')
            until(lambda: not window.root_ranking.pending and window.root_ranking.ordered is not None)
            assert window.root_ranking.snapshot['query'] == 'latest'
            assert window.plugin_runtime.active.unit == unit, 'Persistent worker restarted for every query'
            window.search.setText('slow')
            window.apps.append(Result('c', 'Gamma', kind='app'))
            window.render()
            until(lambda: not window.root_ranking.pending and window.root_ranking.ordered is not None
                  and 'c' in window.root_ranking.ordered)
            window.set_mode('plugin:node-tool')
            until(lambda: bool(window.rows) and not window.plugin_calls)
            window.activate()
            until(lambda: window.active_form() is not None)
            assert window.active_form().objectName() == 'greeting-form'
            window.active_form().dirty = False
            window.set_mode('plugin:node-service')
            until(lambda: bool(window.rows) and not window.plugin_calls)
            assert window.rows[0].title == '6', 'Non-Python backend could not call a host service'
            window.set_mode('root')
            until(lambda: not window.root_ranking.pending and bool(window.rows))
            for failure in ('bad', 'crash', 'hang', 'flood'):
                window.search.setText(failure)
                until(lambda: window.root_ranking.failed and not window.root_ranking.pending)
                window.search.setText('Alpha')
                assert any(row.id == 'a' for row in window.rows), 'Built-in fallback did not recover'
                until(lambda: window.plugin_runtime.quiescent)
                window.root_ranking.refresh()
                window.search.setText('restored')
                until(lambda: not window.root_ranking.pending and window.root_ranking.ordered is not None)
            window.cfg['ranking']['engine'] = 'python-ranker'
            window.refresh_plugins('python-ranker')
            until(lambda: not window.root_ranking.pending and window.root_ranking.ordered == ['a']
                  and any(row.id == 'a' for row in window.rows))
        finally:
            form = window.active_form()
            if form:
                form.dirty = False
            window.close()
            until(lambda: not window.isVisible() and window.plugin_runtime.quiescent)


def check():
    protocol()
    cached_transport()
    with tempfile.TemporaryDirectory(prefix='kite-multilang-') as name, ExitStack() as stack:
        root = Path(name)
        stack.enter_context(patch.dict(os.environ, {'XDG_CONFIG_HOME': str(root / 'config'),
            'XDG_DATA_HOME': str(root / 'data'), 'XDG_STATE_HOME': str(root / 'state'),
            'XDG_CACHE_HOME': str(root / 'cache')}))
        manager = pm.Manager()
        packages(manager)
        runtime(manager, root)
    print('Multilingual plugins OK: managed packages, Node UI/forms, ranking, stale queries, fallback and cleanup')


if __name__ == '__main__':
    check()
