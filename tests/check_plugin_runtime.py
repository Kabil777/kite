"""Fixture-only v2 discovery/protocol/worker checks. No user plugins or credentials."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from main.plugin_api import API_VERSION, discover_plugins, plugin_root, user_plugin_root


def package(root, name, version=API_VERSION, source="raise RuntimeError('discovery executed plugin code')"):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'plugin.json').write_text(json.dumps({'id': name, 'name': name, 'api_version': version}))
    (directory / '__init__.py').write_text(source)
    return directory


def discovery():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with patch.dict(os.environ, XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data')):
            config, data = user_plugin_root(), plugin_root()
            assert config == root / 'config/kite/plugin'
            assert data == root / 'data/kite/plugins'
            original = package(config, 'probe')
            data.mkdir(parents=True)
            (data / 'probe').symlink_to(original, target_is_directory=True)
            found = discover_plugins()
            assert len(found) == 1 and found[0][0]['id'] == 'probe' and isinstance(found[0][1], Path)
            # A distinct package claiming this ID disables the ambiguous ID, not just the second copy.
            (data / 'probe').unlink()
            package(data, 'probe')
            errors = []
            assert discover_plugins(on_error=lambda name, text: errors.append((name, text))) == []
            assert any('Duplicate' in text for _, text in errors)
            package(config, 'clipboard')
            package(config, 'old', version=API_VERSION - 1)
            package(config, 'disabled', version=API_VERSION - 1)
            huge = package(config, 'huge')
            (huge / 'plugin.json').write_bytes(b' ' * 16385)
            invalid = package(config, 'invalid')
            (invalid / 'plugin.json').write_text('{')
            (config / 'broken').symlink_to(root / 'missing', target_is_directory=True)
            errors.clear()
            assert discover_plugins(config={'disabled': {'enabled': False}},
                                    on_error=lambda name, text: errors.append((name, text))) == []
            by_name = dict(errors)
            assert 'reserved' in by_name['clipboard'] and 'Migrate' in by_name['old']
            assert 'disabled' not in by_name and '16 KiB' in by_name['huge'] and 'invalid' in by_name
            assert 'Broken' in by_name['broken']
            # Bounded directory enumeration is independent of whether files are manifests.
            crowded = root / 'crowded'; crowded.mkdir()
            for index in range(1001):
                (crowded / str(index)).touch()
            errors.clear()
            assert discover_plugins((crowded,), on_error=lambda name, text: errors.append(text)) == []
            assert '1000' in errors[0]
    print('v2 metadata discovery OK; no plugin entry executed')


def protocol():
    import sys
    from main.plugin_api import Action, Catalog, Event, MenuAction, Node, Result, Toast, View
    from main.plugin_protocol import MAX_FRAME, Framer, ProtocolError, encode, event_from_wire, from_wire, request, response, to_wire
    value = View(nodes=(Node('column', 'root', children=(Node('label', 'text', {'text': 'Unicode ↩ \\n [safe]'}),)),),
                 results=(Result('entry', 'Entry', action=Action('activate', 'entry', push=True), tag='Vault'),),
                 catalog=Catalog(filters=('All', 'One')), actions=(MenuAction('refresh', 'Refresh', Action('query', ''), shortcut='Ctrl+R'),),
                 toast=Toast('Refreshed', tone='success'))
    message = response('generation:1', to_wire(value))
    data = encode(message)
    framer = Framer(); messages = []
    for byte in data:
        messages.extend(framer.feed(bytes([byte])))
    assert messages == [json.loads(data)] and not framer.buffer
    assert from_wire(messages[0]['result']) == value
    assert len(Framer().feed(data + data)) == 2
    assert from_wire(to_wire(Toast('No remote', tone='error'))) == Toast('No remote', tone='error')
    assert event_from_wire({'node_id': 'entry', 'name': 'activate', 'payload': {'query': ''}}) == Event('entry', 'activate', {'query': ''})
    invalid_frames = [b'{}\n', b'\xff\n', b'\n', b'{"jsonrpc":"2.0","jsonrpc":"2.0"}\n',
                      b'{"jsonrpc":"2.0","id":1,"result":NaN}\n',
                      b'{"jsonrpc":"2.0","id":1,"result":1e400}\n',
                      b'[' * 65 + b'0' + b']' * 65 + b'\n', data * 33, b'x' * (MAX_FRAME + 1),
                      json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': 'no'}).encode('utf-16') + b'\n']
    for frame in invalid_frames:
        try:
            Framer().feed(frame)
        except ProtocolError:
            pass
        else:
            raise AssertionError('Malformed/oversized frame accepted')
    for payload in ({'kind': 'os.system', 'data': {}}, {'kind': 'view', 'data': {'nodes': 'bad'}},
                    {'kind': 'view', 'data': {'unexpected': True}}, {'kind': 'toast', 'data': {'title': ''}}):
        try:
            from_wire(payload)
        except ProtocolError:
            pass
        else:
            raise AssertionError('Invalid DTO accepted')
    for malformed in ([1], None):
        try:
            encode({'jsonrpc': '2.0', 'id': 1, 'method': malformed})
        except ProtocolError:
            pass
        else:
            raise AssertionError('Invalid method accepted')
    huge = View(nodes=tuple(Node('markdown', str(index), {'text': 'x' * 131072}) for index in range(33)))
    try:
        encode(response(1, to_wire(huge)))
    except ProtocolError:
        pass
    else:
        raise AssertionError('Aggregate frame limit not enforced')
    assert not any(name.startswith('PySide6') for name in sys.modules)
    print('v2 protocol/DTO checks OK; Qt not imported')


class Peer:
    """Real isolated fixture worker; never points at an installed/user package."""
    def __init__(self, directory, config=None, allowed=()):
        import subprocess, sys, uuid
        from main.plugin_protocol import Framer
        self.unit = f'kite-plugin-{os.getpid()}-{uuid.uuid4().hex}'
        self.process = subprocess.Popen([sys.executable, '-B', '-m', 'main.plugin_worker', '--supervise',
                                         '--owner', str(os.getpid()), '--unit', self.unit,
                                         *[arg for unit in allowed for arg in ('--allow-unit', unit)]],
                                        cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.framer, self.messages = Framer(), []
        from main.plugin_protocol import WIRE_VERSION
        environment = {key: os.environ[key] for key in ('HOME', 'PATH', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'LANG') if key in os.environ}
        self.send('init', 'initialize', {'api_version': API_VERSION, 'wire_version': WIRE_VERSION,
                  'plugin_id': directory.name, 'directory': str(directory), 'config': config or {}, 'environment': environment})

    def send(self, identity, method, params=None):
        from main.plugin_protocol import encode, request
        self.process.stdin.write(encode(request(identity, method, params)))
        self.process.stdin.flush()

    def receive(self, timeout=8):
        import select, time
        deadline = time.monotonic() + timeout
        while not self.messages and time.monotonic() < deadline:
            if select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    raise EOFError('Fixture worker exited; supervisor status ' + str(self.process.poll()))
                self.messages.extend(self.framer.feed(data))
        assert self.messages, 'Fixture worker response timed out'
        return self.messages.pop(0)

    def close(self):
        from main.plugin_worker import stop_unit
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=6)
        finally:
            assert stop_unit(self.unit), 'Fixture unit cleanup not confirmed'
            self.process.stdin.close(); self.process.stdout.close()


def workers():
    import subprocess, sys, time
    from main.plugin_protocol import from_wire
    source = '''from pathlib import Path
import os, subprocess, sys, time
from plugin_api import View, Result, Action
print('fixture import noise')
def search(query, host):
    print('fixture request noise')
    if query == 'exit': os._exit(23)
    if query == 'hang': time.sleep(60)
    if query == 'service':
        return View(results=(Result('service', host.call('fixture.echo', {'value': 'echo'})),))
    if query == 'memory':
        data = []
        while True: data.append(bytearray(2 * 1024 * 1024))
    if query == 'child':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        Path(host.config['marker']).write_text(str(child.pid))
    return View(results=(Result('fixture', query, action=Action('copy', 'fixture-only')),))
'''
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        directory = package(root, 'fixture', source=source)
        marker = root / 'child.pid'
        peer = Peer(directory, {'marker': str(marker)})
        try:
            assert peer.receive()['result']['plugin_id'] == 'fixture'
            peer.send('one', 'search', {'query': 'hello'})
            assert from_wire(peer.receive()['result']).results[0].title == 'hello'
            peer.send('service', 'search', {'query': 'service'})
            service = peer.receive()
            assert service['method'] == 'host.call' and service['params']['parent'] == 'service'
            from main.plugin_protocol import encode, response
            peer.process.stdin.write(encode(response(service['id'], 'echo'))); peer.process.stdin.flush()
            assert from_wire(peer.receive()['result']).results[0].title == 'echo'
            peer.send('child', 'search', {'query': 'child'}); peer.receive()
            child = int(marker.read_text())
            peer.send('hang', 'search', {'query': 'hang'})
            # A hung worker does not prevent a different unit from starting/responding.
            other = Peer(directory, allowed=[peer.unit])
            try:
                other.receive(); other.send('two', 'search', {'query': 'responsive'})
                assert from_wire(other.receive()['result']).results[0].title == 'responsive'
            finally:
                other.close()
            peer.send(None, 'cancel', {'id': 'hang'})
            time.sleep(.3)
        finally:
            peer.close()
        assert not Path('/proc', str(child)).exists() or Path('/proc', str(child), 'stat').read_text().split()[2] == 'Z'
        for query in ('exit', 'memory'):
            peer = Peer(directory)
            try:
                peer.receive()
                if query == 'memory':
                    subprocess.run(['systemctl', '--user', 'set-property', '--runtime', peer.unit, 'MemoryMax=64M'],
                                   check=True, capture_output=True, timeout=5)
                peer.send(query, 'search', {'query': query})
                try:
                    peer.receive()
                except EOFError:
                    pass
                else:
                    raise AssertionError('Crash/OOM worker unexpectedly replied')
            finally:
                peer.close()
        started = root / 'import-started'
        stuck = package(root, 'stuck', source=f'from pathlib import Path\nimport time\nPath({str(started)!r}).touch()\ntime.sleep(60)\n')
        peer = Peer(stuck)
        try:
            deadline = time.monotonic() + 6
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert started.exists(), 'Fixture import never started'
        finally:
            peer.close()
        owner_info = root / 'owner.json'
        code = '''import json, sys, time
from pathlib import Path
from tests.check_plugin_runtime import Peer
peer = Peer(Path(sys.argv[1]), {'marker': sys.argv[2]})
peer.receive()
peer.send('child', 'search', {'query': 'child'})
peer.receive()
Path(sys.argv[3]).write_text(json.dumps({'unit': peer.unit, 'supervisor': peer.process.pid}))
time.sleep(60)
'''
        owner = subprocess.Popen([sys.executable, '-B', '-c', code, str(directory), str(marker), str(owner_info)],
                                 cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        unit = None
        try:
            deadline = time.monotonic() + 8
            while not owner_info.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert owner_info.exists(), 'Fixture owner did not initialize'
            info = json.loads(owner_info.read_text()); unit = info['unit']; child = int(marker.read_text())
            owner.kill(); owner.wait(timeout=3)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                supervisor = Path('/proc', str(info['supervisor']), 'stat')
                if not supervisor.exists() or supervisor.read_text().split()[2] == 'Z':
                    break
                time.sleep(.02)
            assert not Path('/proc', str(child)).exists() or Path('/proc', str(child), 'stat').read_text().split()[2] == 'Z'
        finally:
            if owner.poll() is None:
                owner.kill(); owner.wait()
            if unit:
                from main.plugin_worker import stop_unit
                assert stop_unit(unit)
        import uuid
        unit = f'kite-plugin-{os.getpid()}-{uuid.uuid4().hex}'
        unavailable = subprocess.run([sys.executable, '-B', '-m', 'main.plugin_worker', '--supervise',
                                      '--owner', str(os.getpid()), '--unit', unit], cwd=Path(__file__).resolve().parents[1],
                                     env=dict(os.environ, PATH='/nonexistent'), capture_output=True, timeout=5)
        assert unavailable.returncode == 78 and not unavailable.stdout
    print('systemd workers OK: RPC/services, print redirection, hung call/import, detached children, owner death, crash/OOM, no fallback')


def qt_runtime():
    import sys, time
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit
    from main.core import DATA, load_config
    from main.launcher import Jobs, Launcher
    app = QApplication.instance() or QApplication([])
    original_builtin = Jobs.builtin
    def builtin(self, key, request, callback, timeout=2500):
        if key == 'apps':
            callback({'results': []})
        else:
            original_builtin(self, key, request, callback, timeout)
    with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
        'XDG_CONFIG_HOME': temporary + '/config', 'XDG_DATA_HOME': temporary + '/data',
        'XDG_STATE_HOME': temporary + '/state', 'XDG_CACHE_HOME': temporary + '/cache',
    }), patch.object(Jobs, 'builtin', builtin), patch('main.applications.Applications.start', lambda self: None):
        root = Path(temporary)
        imported, busy, calls = root / 'imported', root / 'busy', root / 'calls'
        source = '''import os, time
from pathlib import Path
from plugin_api import Action, MenuAction, Node, Result, Toast, View
Path(IMPORT_MARKER).write_text(str(os.getpid()))
def search(query, host):
    with Path(CALL_MARKER).open('a') as log: log.write(query + '\\n')
    if query == 'crash': os._exit(78)
    if query == 'invalid': return View(nodes=(Node('unknown', 'bad'),))
    if query == 'flood': os.write(1, b'x' * (2 * 1024 * 1024))
    if query == 'timeout': time.sleep(60)
    if query == 'copy':
        host.config['clipboard']['copy_command'] = ['nonexistent-worker-override']
        host.call('clipboard.write', {'value': 'worker-fixture-only'})
    if query == 'old':
        Path(BUSY_MARKER).touch()
        time.sleep(2)
    title = host.call('math.calculate', {'query': '2*3'})[0]['title'] if query == 'service' else query or 'fixture'
    return View(results=(Result('fixture', title, keywords=query),), actions=(
        MenuAction('sync', 'Sync', Action('activate', 'sync'), shortcut='Ctrl+S'),
        MenuAction('create', 'Create', Action('activate', 'create', push=True), shortcut='Alt+I'),))
def handle_event(event, host):
    if event.node_id == 'create':
        return View(nodes=(Node('form', 'edit', {'title': 'Fixture form'}, children=(
            Node('text_input', 'entry', {'text': 'fixture/path'}),
            Node('text_input', 'password', {'password': True}),), events={'submit': {}}),),
            actions=(MenuAction('cancel', 'Cancel', Action('back', ''), shortcut='Ctrl+G'),))
    return Toast('Fixture error', 'No remote or simulated save failure.', 'error')
'''.replace('IMPORT_MARKER', repr(str(imported))).replace('BUSY_MARKER', repr(str(busy))).replace('CALL_MARKER', repr(str(calls)))
        directory = package(user_plugin_root(), 'probe', source=source)
        (directory / 'HOWTO.md').write_text('# probe\n## Start here\nFixture user manual.')
        checkout = root / 'probe-checkout'
        directory.rename(checkout)
        directory.symlink_to(checkout, target_is_directory=True)
        cfg = load_config(DATA / 'defaults.yaml')
        copied = root / 'copied'
        cfg['clipboard']['copy_command'] = [sys.executable, '-c',
            'import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())', str(copied)]
        window = Launcher(cfg, favorites_path=root / 'favorites.sqlite')
        window.show(); window.activateWindow(); app.processEvents()
        def until(test, timeout=8):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not test():
                app.processEvents(); time.sleep(.01)
            assert test(), ('Qt worker condition timed out', window.mode, [row.title for row in window.rows],
                            list(window.plugin_calls), window.toast.text(), window.toast.message.text(),
                            list(window.jobs.running))
        beats = []
        heartbeat = QTimer(); heartbeat.timeout.connect(lambda: beats.append(1)); heartbeat.start(10)
        try:
            assert not imported.exists(), 'GUI discovery imported external code'
            assert not any(name == '_launcher_plugin_' + str(directory.resolve()).encode().hex() for name in tuple(sys.modules))
            window.set_mode('plugin:probe')
            until(lambda: bool(window.rows) and not window.plugin_calls)
            assert int(imported.read_text()) != os.getpid()
            window.search.setText('old'); until(busy.exists)
            old_unit = window.plugin_runtime.active.unit
            window.search.setText('new')
            until(lambda: bool(window.rows) and window.rows[0].title == 'new' and not window.plugin_calls)
            assert window.plugin_runtime.active.unit != old_unit
            assert len(beats) > 10, 'Worker cancellation blocked the UI event loop'
            window.search.setText('service')
            until(lambda: bool(window.rows) and window.rows[0].title == '6' and not window.plugin_calls)
            window.search.setText('copy')
            until(lambda: copied.exists() and not window.plugin_calls)
            assert copied.read_bytes() == b'worker-fixture-only', 'Worker service ignored host clipboard configuration'
            for failure in ('crash', 'invalid', 'flood', 'timeout'):
                window.toast.dismiss()
                window.search.setText(failure)
                until(lambda: window.toast.isVisible() and not window.plugin_calls)
                assert not window.plugin_runtime.blocked, 'Plugin exit code confused with supervisor failure'
                assert calls.read_text().splitlines().count(failure) == 1, 'Failed operation was replayed'
                window.search.setText('service')
                until(lambda: bool(window.rows) and window.rows[0].title == '6' and not window.plugin_calls)
            window.search.setFocus()
            QTest.keyClick(window.search, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
            until(lambda: window.toast.isVisible() and not window.plugin_calls)
            assert window.rows[0].title == '6'
            window.toast.close_button.click()
            QTest.keyClick(window.search, Qt.Key.Key_I, Qt.KeyboardModifier.AltModifier)
            until(lambda: window.active_form() is not None and not window.plugin_calls)
            form = window.active_form()
            password = form.fields.findChild(QLineEdit, 'password')
            password.setFocus(); QTest.keyClicks(password, 'fixture-only-input')
            form.submit_button.click()
            until(lambda: window.toast.isVisible() and not window.plugin_calls)
            assert window.active_form() is form and password.text() == '' and not password.isUndoAvailable()
            assert form.fields.findChild(QLineEdit, 'entry').text() == 'fixture/path'
            QTest.keyClick(password, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
            assert not form.confirmation.isHidden()
            window.back(); assert form.confirmation.isHidden()
            form.dirty = False
            window.set_mode('root')
            until(lambda: not window.plugin_runtime.sessions)
            assert not window.plugin_shortcuts and not window.toast.isVisible()
            window.toggle_help(); assert window.plugin_help_title.text() == 'KITE'; window.back()
        finally:
            heartbeat.stop()
            form = window.active_form()
            if form: form.dirty = False
            window.close()
            until(lambda: not window.isVisible())
        with patch.dict(os.environ, PATH='/nonexistent'):
            imported.unlink()
            unavailable = Launcher(load_config(DATA / 'defaults.yaml'), favorites_path=root / 'other.sqlite')
            unavailable.show(); unavailable.set_mode('plugin:probe')
            window = unavailable
            until(lambda: unavailable.toast.isVisible() and not unavailable.plugin_calls)
            assert not imported.exists() and unavailable.plugin_runtime.blocked
            unavailable.set_mode('emoji'); assert unavailable.rows
            unavailable.close(); until(lambda: not unavailable.isVisible())
    print('Qt worker integration OK: no GUI imports, hard cancellation, responsive UI, services, forms/toasts, safe close and no fallback')


if __name__ == '__main__':
    discovery()
    protocol()
    workers()
    qt_runtime()
