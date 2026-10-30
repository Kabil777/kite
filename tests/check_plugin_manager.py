"""Single isolated package-manager check. No GUI, network, real units or live stores."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
import ast
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import stat
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import warnings
import zipfile

from main import plugin_manager as pm
from main import plugin_worker as worker

# Synthetic fixtures only: every byte is served below; no published repository is claimed.
REPO = 'https://github.com/fixture/plugin'
SOURCE = 'https://raw.githubusercontent.com/fixture/catalog/main/index.json'
SOURCE_B = 'https://raw.githubusercontent.com/fixture/catalog/main/other.json'
APP = Path(__file__).resolve().parents[1]


def blocked(*args, **kwargs):
    raise AssertionError('Unexpected network/process action during isolated verification')


def rejects(action):
    try:
        action()
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile):
        return
    raise AssertionError('Unsafe/invalid operation was accepted')


def check():
    resources = {}
    real_download, real_atomic = pm.download, pm._atomic_json
    original_import = builtins.__import__

    def no_qt(name, *args, **kwargs):
        if name.startswith(('PySide6', 'PyQt')):
            raise AssertionError('Qt loaded through the package-manager path')
        return original_import(name, *args, **kwargs)

    def serve(url, limit, cancelled=lambda: False):
        if cancelled():
            raise pm.ManagerError('Cancelled fixture download')
        data = resources[url]
        if len(data) > limit:
            raise pm.ManagerError('Fixture download exceeded limit')
        return data

    with tempfile.TemporaryDirectory(prefix='kite-manager-check-') as temporary, ExitStack() as stack:
        home = Path(temporary)
        environment = {'HOME': str(home), 'XDG_DATA_HOME': str(home / 'data'),
                       'XDG_CONFIG_HOME': str(home / 'config'), 'XDG_STATE_HOME': str(home / 'state'),
                       'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_RUNTIME_DIR': str(home / 'runtime'),
                       'PATH': str(home / 'bin')}
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(patch('builtins.__import__', side_effect=no_qt))
        for target in ('socket.create_connection', 'socket.getaddrinfo', 'socket.socket.connect',
                       'subprocess.run', 'subprocess.Popen', 'os.system', 'os.posix_spawn'):
            stack.enter_context(patch(target, side_effect=blocked))
        stack.enter_context(patch.object(pm, 'download', side_effect=serve))
        stack.enter_context(patch.object(pm, 'units_clear', return_value=True))
        manager = pm.Manager()
        marker = home / 'ENTRYPOINT-WAS-IMPORTED'
        config = b'# preserved comment\nplugins:\n  hello:\n    enabled: false\n    config: {greeting: untouched}\n'
        manager.config_path.parent.mkdir(parents=True)
        manager.config_path.write_bytes(config)
        external = home / 'external-store'
        external.mkdir()
        (external / 'keep').write_text('inert external data')

        def archive(version='1.0.0', identifier='hello', api=2, extra=()):
            data = io.BytesIO()
            with warnings.catch_warnings(), zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as package:
                warnings.simplefilter('ignore', UserWarning)
                manifest = {'id': identifier, 'name': 'Hello', 'api_version': api}
                package.writestr('hello/plugin.json', json.dumps(manifest))
                package.writestr('hello/__init__.py', f'# {version}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise AssertionError("plugin code executed")\n')
                package.writestr('hello/HOWTO.md', '# Hello\nPrepare a greeting. Dependencies are manual.\n')
                for name, content in extra:
                    package.writestr(name, content)
            return data.getvalue()

        def publish(version='1.0.0', payload=None, repo=REPO, source=SOURCE, api=2):
            payload = payload if payload is not None else archive(version)
            url = f'{repo}/releases/download/v{version}/hello.zip'
            release = {'version': version, 'api_version': api, 'url': url, 'sha256': hashlib.sha256(payload).hexdigest()}
            catalog = {'schema': 1, 'plugins': [{'id': 'hello', 'name': 'Hello', 'summary': 'Inert fixture',
                        'author': 'Fixture', 'repository': repo, 'latest': version, 'releases': [release],
                        'commands': ['kite-fixture-missing'], 'requirements': ['Install requirements manually']}]}
            resources[url], resources[source] = payload, json.dumps(catalog).encode()
            snapshot, offline = manager.catalog(refresh=True)
            assert not offline
            return manager.release('hello', snapshot)

        def install(entry, update=False, lease=None):
            prepared = manager.prepare(entry)
            try:
                return manager.install(prepared, update=update, lease=lease)
            finally:
                manager.discard(prepared)

        def cli(*args):
            with patch.object(sys, 'stdin', io.StringIO('')), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return pm.cli(list(args))

        # Catalog/data trust boundaries, including real bounded downloader with a fake response.
        manager.set_source(SOURCE)
        entry = publish()
        base_catalog = json.loads(resources[SOURCE])
        with patch.object(pm, 'MAX_CATALOG', 64):
            rejects(lambda: pm.validate_catalog(base_catalog))
        guide = home / 'guide'
        guide.mkdir()
        (guide / 'HOWTO.md').write_bytes(b'x' * (pm.MAX_GUIDE + 1))
        rejects(lambda: pm.read_guide(guide))
        (guide / 'HOWTO.md').write_bytes(b'\xff')
        rejects(lambda: pm.read_guide(guide))
        for mutate in (lambda c: c['plugins'].append(deepcopy(c['plugins'][0])),
                       lambda c: c['plugins'][0].update(id='marketplace'),
                       lambda c: c['plugins'][0].update(id='clipboard'),
                       lambda c: c['plugins'][0]['releases'][0].update(sha256='bad'),
                       lambda c: c['plugins'][0].update(repository=REPO + '-other'),
                       lambda c: c['plugins'][0].update(latest='1.0.0-beta'),
                       lambda c: c.update(plugins=[c['plugins'][0]] * 1001)):
            bad = deepcopy(base_catalog)
            mutate(bad)
            rejects(lambda: pm.validate_catalog(bad))
        rejects(lambda: pm._decode(b'{"schema":1,"schema":1,"plugins":[]}'))
        for url in ('http://github.com/a/b', 'https://127.0.0.1/x', 'https://github.com@localhost/x',
                    'https://user:password@github.com/a/b', 'https://github.com:444/a/b', 'https://github.com.evil.invalid/a/b'):
            rejects(lambda: pm.github_url(url))
        rejects(lambda: pm._GitHubRedirect().redirect_request(None, None, 302, '', {}, 'http://localhost/asset'))
        class Response(io.BytesIO):
            def geturl(self):
                return SOURCE
        class Opener:
            def open(self, *args, **kwargs):
                return Response(b'x' * 33)
        with patch.object(pm, 'build_opener', return_value=Opener()):
            rejects(lambda: real_download(SOURCE, 32))
            rejects(lambda: real_download(SOURCE, 64, lambda: True))
        with patch.object(pm, 'download', side_effect=pm.ManagerError('offline')):
            cached, offline = manager.catalog(refresh=True)
            assert offline and cached['plugins'][0]['id'] == 'hello'
        manager.set_source(SOURCE_B)
        rejects(lambda: manager.prepare(entry))
        with patch.object(pm, 'download', side_effect=pm.ManagerError('offline')):
            rejects(lambda: manager.catalog())  # Source A's cache is not a source B catalog.
        manager.set_source(SOURCE)

        # Metadata-only install/enable and YAML permission precedence, with inert import sentinel.
        install(entry)
        assert manager.registry()['enabled']['hello'] is True
        row = manager.inventory()[0]
        assert row['status'] == 'Disabled by configuration' and not row['enabled'] and row['managed']
        assert row['missing'] == ['kite-fixture-missing']
        manager.set_enabled('hello', True)
        assert not manager.discover({'hello': {'enabled': False}}, lambda *_: None)
        rejects(lambda: manager.activation_directory('hello', manager.root / 'hello'))
        # Execute the production admission methods without loading QObject/Qt or starting a worker.
        tree = ast.parse((APP / 'main/plugin_runtime.py').read_text())
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in ('start_pending', 'next_call')]
        namespace = {}
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<runtime-admission>', 'exec'), namespace)
        errors = []
        call = SimpleNamespace(plugin_id='hello', cancelled=Event(), finish=lambda **kw: errors.append(kw['error']))
        runtime = SimpleNamespace(suspended=False, pending=(call, manager.root / 'hello'), sessions=[],
                                  blocked=False, manager=manager, is_current=lambda _: True)
        namespace['start_pending'](runtime)
        assert runtime.pending is None and 'disabled' in errors[-1]
        runtime.suspended = True
        session = SimpleNamespace(stopping=False, runtime=runtime, queued=call)
        namespace['next_call'](session)
        assert session.queued is call  # Suspended admission never sends an old queued request.
        manager.config_path.write_bytes(config.replace(b'enabled: false', b'enabled: true'))
        manifest, pinned = manager.discover({'hello': {'enabled': True}}, lambda *_: None)[0]
        record = manager.registry()['installed']['hello']
        assert pinned == manager.payload('hello', record) and manifest['id'] == 'hello'
        assert manager.activation_directory('hello', manager.root / 'hello') == pinned
        (home / 'data-alias').symlink_to(home / 'data', target_is_directory=True)
        (home / 'state-alias').symlink_to(home / 'state', target_is_directory=True)
        with patch.dict(os.environ, {'XDG_DATA_HOME': str(home / 'data-alias'), 'XDG_STATE_HOME': str(home / 'state-alias')}):
            alias = pm.Manager()
            assert alias.state == manager.state and alias.root == manager.root
            assert alias.activation_directory('hello', alias.root / 'hello') == pinned
        manager.set_enabled('hello', False)
        assert not manager.discover({'hello': {'enabled': True}}, lambda *_: None)
        manager.config_path.write_bytes(config)
        assert not marker.exists()

        # Lease/lock coordination and durable disable even when cleanup subsequently fails.
        gui = pm.UseLease(manager.store)
        try:
            assert cli('list') == 0
            with patch.dict(os.environ, {'XDG_STATE_HOME': str(home / 'other-state')}):
                rejects(lambda: pm.Manager().set_source(SOURCE))  # One data store, not one lock per state profile.
            rejects(lambda: manager.set_source(SOURCE))
            with patch.object(pm, 'units_clear', return_value=False):
                manager.set_enabled('hello', False, gui)
                rejects(lambda: manager.uninstall('hello', gui))
            assert manager.registry()['enabled']['hello'] is False
            supervisor = pm.UseLease(manager.store)
            try:
                rejects(lambda: manager.uninstall('hello', gui))
                rejects(lambda: pm.UseLease(manager.store, shared=False))
            finally:
                supervisor.close()
            with pm.mutation_lock(manager.store):
                rejects(manager.inventory)
        finally:
            gui.close()
        exclusive = pm.UseLease(manager.store, shared=False)
        try:
            rejects(lambda: pm.UseLease(manager.store))  # An app cannot start through a commit.
        finally:
            exclusive.close()
        manager.set_enabled('hello', True)

        # Changed pointers, authored/development references, duplicate canonical roots and no adoption.
        link = manager.root / 'hello'
        target = os.readlink(link)
        link.unlink()
        link.symlink_to(external)
        rejects(lambda: manager.uninstall('hello'))
        assert (external / 'keep').read_text() == 'inert external data'
        link.unlink(); link.symlink_to(target)
        manager.authored.mkdir(parents=True)
        development = manager.authored / 'hello'
        development.symlink_to(target)
        assert len(pm.discover_plugins(roots=(manager.authored, manager.root))) == 1
        rejects(lambda: install(publish('1.0.1'), update=True))
        rejects(lambda: manager.uninstall('hello'))
        assert development.is_symlink() and os.readlink(development) == target
        development.unlink()
        manager.uninstall('hello')
        development.mkdir()
        (development / 'plugin.json').write_text(json.dumps({'id': 'hello', 'name': 'Hello', 'api_version': 2}))
        (development / '__init__.py').write_text('raise AssertionError("authored import")')
        rejects(lambda: install(publish()))
        rejects(lambda: manager.uninstall('hello'))
        manager.set_enabled('hello', False)
        assert not manager.registry()['installed']  # Disable never grants ownership.
        shutil.rmtree(development)
        legacy = manager.root / 'hello'
        legacy.mkdir()
        (legacy / 'plugin.json').write_text(json.dumps({'id': 'hello', 'name': 'Hello', 'api_version': 2}))
        (legacy / '__init__.py').write_text('raise AssertionError("legacy import")')
        (legacy / 'plugin.json').write_text(json.dumps({'id': 'hello', 'name': 'Hello', 'api_version': 1}))
        assert manager.inventory()[0]['status'] == 'Incompatible'
        rejects(lambda: install(publish()))
        rejects(lambda: manager.uninstall('hello'))
        shutil.rmtree(legacy)

        # Hash/API/ID mismatch, corrupt ZIPs, traversal, links, special files, conflicts and bombs.
        entry = publish()
        resources[entry['url']] = b'changed bytes'
        rejects(lambda: manager.prepare(entry))
        candidates = [b'not a zip', archive(identifier='other'), archive(api=1)]
        for name in ('hello/../escape', '/hello/escape', 'C:/escape', 'hello\\escape', 'hello//escape', 'other/file'):
            candidates.append(archive(extra=[(name, b'unsafe')]))
        candidates += [archive(extra=[('hello/HOWTO.md', b'duplicate')]),
                       archive(extra=[('hello/a', b'file'), ('hello/a/b', b'conflict')])]
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR):
            info = zipfile.ZipInfo('hello/link')
            info.create_system, info.external_attr = 3, (kind | 0o600) << 16
            candidates.append(archive(extra=[(info, b'../external-store')]))
        hardlink = zipfile.ZipInfo('hello/hardlink')
        hardlink.extra = b'\x0d\x00\x00\x00'
        candidates.append(archive(extra=[(hardlink, b'link')]))
        for payload in candidates:
            rejects(lambda: manager.prepare(publish(payload=payload)))
            assert not marker.exists() and not link.exists()
        rejects(lambda: manager.prepare(publish(api=3)))
        with patch.object(pm, 'MAX_EXTRACTED', 1024):
            rejects(lambda: manager.prepare(publish(payload=archive(extra=[('hello/bomb', b'0' * 2048)]))))
        with patch.object(pm, 'MAX_MEMBERS', 8):
            rejects(lambda: manager.prepare(publish(payload=archive(extra=[('hello/' + 'a/' * 20 + 'file', b'x')]))))
        with patch.object(pm, 'MAX_ARCHIVE', 8):
            rejects(lambda: manager.prepare(publish()))
        assert manager.config_path.read_bytes() == config

        # A pre-journal orphan is not adopted/reused just because its bytes match a release.
        prepared = manager.prepare(publish())
        orphan = manager.payload('hello', prepared['record'])
        orphan.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(prepared['directory'], orphan)
        rejects(lambda: manager.install(prepared))
        assert orphan.exists() and not manager.registry()['installed']
        shutil.rmtree(orphan.parent)
        manager.discard(prepared)

        # Updates preserve settings/flags; same-version replacement and origin migration are refused.
        install(publish())
        manager.set_enabled('hello', False)
        old = manager.registry()['installed']['hello']
        install(publish('1.0.1'), update=True)
        assert manager.registry()['enabled']['hello'] is False
        assert not manager.payload('hello', old).exists()
        rejects(lambda: install(publish('1.0.1', archive('different bytes')), update=True))
        rejects(lambda: install(publish('1.0.2', repo=REPO + '-other'), update=True))
        manager.uninstall('hello')

        # Each durable crash boundary: intent/pointer/registry roll back, committed marker rolls forward.
        class PowerLoss(BaseException):
            pass
        for phase in ('intent', 'pointer', 'registry', 'committed'):
            install(publish())
            before = manager.registry()['installed']['hello']
            prepared = manager.prepare(publish('1.0.1'))
            real_point = manager._point
            def atomic(path, value):
                real_atomic(path, value)
                if ((phase in ('intent', 'committed') and path.name == 'journal.json' and value['phase'] == phase)
                        or phase == 'registry' and path.name == 'registry.json'):
                    raise PowerLoss()
            def point(identifier, record):
                real_point(identifier, record)
                if phase == 'pointer':
                    raise PowerLoss()
            try:
                with patch.object(pm, '_atomic_json', side_effect=atomic), patch.object(manager, '_point', side_effect=point):
                    try:
                        manager.install(prepared, update=True)
                    except PowerLoss:
                        pass
                    else:
                        raise AssertionError('Crash injection did not fire')
                assert manager._has_journal()
                rejects(manager.inventory)
                manager.recover()
                current = manager.registry()['installed']['hello']
                assert current['version'] == ('1.0.1' if phase == 'committed' else '1.0.0')
                assert manager.owns('hello', current) and not manager._has_journal()
                if phase != 'committed':
                    assert current == before
                assert manager.config_path.read_bytes() == config
            finally:
                manager.discard(prepared)
            manager.uninstall('hello')

        # Unknown extras (even FIFOs/symlinks) aren't opened; edits and external stores survive removal.
        install(publish())
        payload = link.resolve()
        (payload / '__init__.py').write_text('# deliberate user edit\n')
        (payload / 'external-link').symlink_to(external, target_is_directory=True)
        os.mkfifo(payload / 'unknown-fifo')
        real_hash = pm._file_hash
        def known_only(path):
            assert path.name not in ('unknown-fifo', 'external-link', 'keep')
            return real_hash(path)
        with patch.object(pm, '_file_hash', side_effect=known_only):
            result = manager.uninstall('hello')
        assert result['retained'] and not os.path.lexists(link)
        backup = next(manager.backups.glob('hello-*'))
        assert (backup / '__init__.py').read_text() == '# deliberate user edit\n'
        assert (backup / 'external-link').is_symlink() and (external / 'keep').read_text() == 'inert external data'
        assert stat.S_ISFIFO((backup / 'unknown-fifo').lstat().st_mode)

        # Corrupt/null/unsafe metadata remains unchanged and cannot enable unknown managed code.
        registry_path = manager.state / 'registry.json'
        saved = registry_path.read_bytes()
        for contents in (b'null', b'{broken', b'{"schema":1}'):
            registry_path.write_bytes(contents)
            rejects(manager.registry)
            rejects(lambda: manager.set_source(SOURCE))
            assert registry_path.read_bytes() == contents
        registry_path.write_bytes(saved)
        registry_path.unlink(); os.mkfifo(registry_path)
        rejects(manager.registry)
        registry_path.unlink(); registry_path.write_bytes(saved)
        journal_path = manager.state / 'journal.json'
        journal_path.write_bytes(b'null')
        rejects(manager.recover)
        assert journal_path.read_bytes() == b'null'
        journal_path.unlink()
        unknown = manager.store / ('0' * 64) / 'hello'
        unknown.mkdir(parents=True)
        (unknown / 'plugin.json').write_text(json.dumps({'id': 'hello', 'name': 'Hello', 'api_version': 2}))
        (unknown / '__init__.py').write_text('raise AssertionError("untracked import")')
        link.symlink_to(unknown)
        manager.config_path.write_bytes(config.replace(b'enabled: false', b'enabled: true'))
        assert not manager.discover({}, lambda *_: None)
        rejects(lambda: manager.activation_directory('hello', link))
        redirected = home / 'redirected-store'
        manager.store.rename(redirected)
        manager.store.symlink_to(redirected, target_is_directory=True)
        assert not manager.discover({}, lambda *_: None)
        rejects(lambda: manager.activation_directory('hello', link))
        manager.store.unlink(); redirected.rename(manager.store)
        manager.config_path.write_bytes(config)
        link.unlink()

        # Actual Qt-free entrypoint dispatch and noninteractive approval behavior.
        publish()
        assert cli('install', 'hello') == 1
        assert cli('install', 'hello', '--yes') == 0
        assert cli('disable', 'hello') == 0 and cli('enable', 'hello') == 0
        assert cli('info', 'hello') == 0 and cli('search', 'hello') == 0
        publish('1.0.1')
        assert cli('update', 'hello', '--yes') == 0
        assert manager.registry()['installed']['hello']['version'] == '1.0.1'
        with patch.object(sys, 'argv', [str(APP / 'main.py'), 'plugins', 'list']), redirect_stdout(io.StringIO()):
            try:
                runpy.run_path(str(APP / 'main.py'), run_name='__main__')
            except SystemExit as outcome:
                assert outcome.code == 0
        assert cli('uninstall', 'hello', '--yes') == 0

        # Real supervisor control flow with fake pidfds/processes/units, including owner death and exit 78.
        for clean in (True, False):
            leases, descriptors, stops = [], [], []
            real_lease = pm.UseLease
            class CapturedLease(real_lease):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    leases.append(self)
            def pidfd(_):
                fd = os.open(home / 'fake-pidfd', os.O_RDWR | os.O_CREAT, 0o600)
                descriptors.append(fd)
                return fd
            class Process:
                pid = 5678
                def wait(self, **kwargs):
                    return 0
                def kill(self):
                    raise AssertionError('Unexpected supervisor kill')
            def start(command):
                assert '--property=KillMode=control-group' in command
                rejects(lambda: real_lease(manager.store, shared=False))
                return Process()
            def stop(unit):
                rejects(lambda: real_lease(manager.store, shared=False))
                stops.append(unit)
                return clean
            def exit_uncertain(code):
                assert code == 78
                rejects(lambda: real_lease(manager.store, shared=False))
                raise PowerLoss()
            try:
                with patch.object(pm, 'UseLease', CapturedLease), patch.object(worker.os, 'getppid', return_value=1234), \
                     patch.object(worker.os, 'pidfd_open', side_effect=pidfd), patch.object(Path, 'is_file', return_value=True), \
                     patch.object(worker.shutil, 'which', return_value='/mock'), patch.object(worker.signal, 'signal'), \
                     patch.object(worker.subprocess, 'Popen', side_effect=start), patch.object(worker, 'stop_unit', side_effect=stop), \
                     patch.object(worker.os, '_exit', side_effect=exit_uncertain), \
                     patch.object(worker.select, 'select', side_effect=lambda *args: ([], [], []) if len(descriptors) == 1 else ([descriptors[0]], [], [])):
                    try:
                        assert worker.supervise(1234, 'kite-plugin-1234-' + 'a' * 32) == 0
                        assert clean
                    except PowerLoss:
                        assert not clean
                assert len(stops) == 1
            finally:
                for lease in leases:
                    lease.close()
                for descriptor in descriptors:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
        # A stop received during preflight must not create a unit after the host timed out.
        with patch.object(worker.os, 'getppid', return_value=1234), patch.object(Path, 'is_file', return_value=True), \
             patch.object(worker.shutil, 'which', return_value='/mock'), patch.object(worker.os, 'pidfd_open', side_effect=blocked), \
             patch.object(worker.signal, 'signal', side_effect=lambda _, handler: handler(None, None)):
            assert worker.supervise(1234, 'kite-plugin-1234-' + 'b' * 32) == 0
        # Host-service primary processes hold their own lease, not just a Qt object's reference.
        def service_module(*args, **kwargs):
            rejects(lambda: pm.UseLease(manager.store, shared=False))
        with patch.object(sys, 'argv', ['worker', '--service']), patch.object(worker.runpy, 'run_module', side_effect=service_module):
            worker.main()
        assert not marker.exists() and manager.config_path.read_bytes() == config
        assert not any(name.startswith(('PySide6', 'PyQt')) for name in sys.modules)
        installer = (APP / 'install.sh').read_text()
        for directory in ('plugins', 'plugin-packages', '.plugin-staging', 'plugin-backups'):
            assert f"--exclude='./{directory}'" in installer
        assert stat.S_IMODE(manager.state.stat().st_mode) == 0o700
        assert stat.S_IMODE(registry_path.stat().st_mode) == 0o600
        print('Plugin manager: isolated catalog, archive, ownership, lifecycle, crash recovery, leases, supervisor and CLI checks passed; no GUI/network/live units/stores used.')


if __name__ == '__main__':
    check()
