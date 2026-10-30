"""Application cache/refresh checks on temporary XDG roots; never launch an app."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from main.applications import Applications, MAX_CACHE
from main.core import Result, load_config


def until(app, test, seconds=5):
    end = time.monotonic() + seconds
    while not test() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.01)
    assert test(), 'Application refresh/notification timed out'


def cache_checks():
    cache = Applications()
    alpha = Result('app:alpha.desktop', 'Alpha', kind='app', value='/fixture/alpha.desktop')
    assert cache.load() is None
    assert cache.save([alpha]) and cache.load() == [alpha]
    valid = cache.path.read_text()
    data = json.loads(valid)
    invalid = [dict(data, version=True), dict(data, version=999), dict(data, context={}),
               dict(data, applications=data['applications'] * 2),
               dict(data, applications=[dict(data['applications'][0], kind='exec')]),
               dict(data, applications=[dict(data['applications'][0], value=['/bin/true'])]),
               dict(data, applications=[dict(data['applications'][0], value='relative.desktop')])]
    for value in invalid:
        cache.path.write_text(json.dumps(value))
        assert cache.load() is None
    for raw in ('{broken', 'x' * (MAX_CACHE + 1)):
        cache.path.write_text(raw)
        assert cache.load() is None
    target = cache.path.parent / 'untouched.json'
    target.write_text(valid)
    cache.path.unlink()
    cache.path.symlink_to(target)
    assert cache.load() is None and not cache.save([alpha])
    assert target.read_text() == valid
    cache.path.unlink()
    assert cache.save([]) and cache.load() == [], 'A valid empty cache became a cache miss'
    assert cache.save([alpha])
    with patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': 'a-different-desktop'}):
        assert Applications().load() is None


def launcher_checks(root):
    from PySide6.QtWidgets import QApplication
    from main import launcher
    app = QApplication.instance() or QApplication([])
    cache = Applications()
    alpha = Result('app:alpha.desktop', 'Alpha', kind='app', value='/fixture/alpha.desktop')
    beta = Result('app:beta.desktop', 'Beta', kind='app', value='/fixture/beta.desktop')
    assert cache.save([alpha])
    cfg = load_config(root / 'config/kite/config.yaml')
    cfg['web']['enabled'] = cfg['math']['enabled'] = False
    callbacks = []
    original = launcher.Jobs.builtin
    def builtin(self, key, params, callback, timeout=2500):
        if key == 'apps':
            callbacks.append(callback)
        else:
            original(self, key, params, callback, timeout)

    with patch.object(launcher.Jobs, 'builtin', builtin), patch.object(Applications, 'start', lambda self: None), \
            patch.object(launcher, 'STATE', root / 'usage.json'):
        window = launcher.Launcher(cfg, config_path=root / 'config/kite/config.yaml',
                                   favorites_path=root / 'favorites.sqlite')
        try:
            window.show()
            assert window.apps == [alpha] and not window.apps_loading and window.apps_refreshing
            assert any(row.id == alpha.id for row in window.rows), 'Cached app was not displayed before refresh'
            assert window.loading_bar.isHidden()
            with patch.object(window.root_ranking, 'invalidate', wraps=window.root_ranking.invalidate) as invalidate:
                callbacks.pop(0)({'results': [alpha.json()]})
                assert invalidate.call_count == 0, 'Unchanged data invalidated the ranking catalog'
                for _ in range(10):
                    window.applications.changed.emit()
                until(app, lambda: bool(callbacks))
                assert len(callbacks) == 1 and window.apps == [alpha] and not window.apps_loading
                callbacks.pop(0)({'results': [alpha.json(), beta.json()]})
                assert window.apps == [alpha, beta] and cache.load() == [alpha, beta]
                assert invalidate.call_count == 1
                window.refresh_apps()
                callbacks.pop(0)({'results': [beta.json(), alpha.json()]})
                assert invalidate.call_count == 1, 'A provider-order-only change invalidated ranking'
                window.refresh_apps()
                window.applications.changed.emit()
                until(app, lambda: window.apps_dirty)
                assert len(callbacks) == 1, 'A new event cancelled the in-flight scan'
                callbacks.pop(0)({'results': [beta.json()]})
                until(app, lambda: bool(callbacks))
                assert len(callbacks) == 1, 'Changes during a scan did not schedule a follow-up'
                callbacks.pop(0)({'results': [beta.json()]})
                assert window.apps == [beta] and cache.load() == [beta] and invalidate.call_count == 2
                before = cache.path.read_bytes()
                for failure in ({'error': 'synthetic failure'}, {'results': ['malformed']}, {}):
                    window.refresh_apps()
                    callbacks.pop(0)(failure)
                    assert window.apps == [beta] and cache.path.read_bytes() == before
                    assert not window.apps_loading and not window.apps_refreshing
                assert invalidate.call_count == 2
            window.refresh_apps()
            late_callback = callbacks.pop(0)
            window.close()
            late_callback({'results': []})
            assert cache.load() == [beta], 'A shutdown callback overwrote the cache'
            assert window.applications.stopped.is_set() and not window.apps_refresh_timer.isActive()
        finally:
            window.close()


def monitor_checks():
    app = QCoreApplication([])
    cache = Applications()
    events = []
    cache.changed.connect(lambda: events.append(time.monotonic()))
    cache.start()
    try:
        until(app, lambda: bool(events))  # Initial reconciliation means monitoring is armed.
        path = Path(os.environ['XDG_DATA_HOME']) / 'applications/cache-fixture.desktop'
        for contents in ('[Desktop Entry]\nType=Application\nName=Cache Fixture\nExec=/bin/true\n',
                         '[Desktop Entry]\nType=Application\nName=Renamed Fixture\nExec=/bin/true\n', None):
            # Drain batched notifications before testing the next independent database change.
            stable = time.monotonic()
            while time.monotonic() - max(stable, events[-1]) < .6:
                app.processEvents()
                time.sleep(.01)
            count = len(events)
            if contents is None:
                path.unlink()
            else:
                path.write_text(contents)
            until(app, lambda: len(events) > count)
    finally:
        cache.stop()
        cache.thread.join(2)
        assert not cache.thread.is_alive(), 'Application monitor survived shutdown'
    print('Gio application monitor OK: create, rename, remove and clean stop')


def main():
    if '--monitor' in sys.argv:
        monitor_checks()
        return
    with tempfile.TemporaryDirectory(prefix='kite-app-cache-') as name, patch.dict(os.environ, {
        'XDG_CONFIG_HOME': name + '/config', 'XDG_DATA_HOME': name + '/data',
        'XDG_DATA_DIRS': name + '/system-data', 'XDG_STATE_HOME': name + '/state',
        'XDG_CACHE_HOME': name + '/cache', 'QT_QPA_PLATFORM': 'offscreen',
    }):
        for base in ('data', 'system-data'):
            (Path(name) / base / 'applications').mkdir(parents=True)
        result = subprocess.run([sys.executable, '-B', '-m', 'tests.check_applications', '--monitor'],
                                capture_output=True, timeout=20)
        assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
        cache_checks()
        launcher_checks(Path(name))
    print('Applications OK: bounded cache, startup reuse, no-op/add/remove refresh, Gio changes and failure preservation')


if __name__ == '__main__':
    main()
