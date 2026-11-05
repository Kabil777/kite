"""Real Launcher/event-loop Save regression; no live providers, settings or plugin execution."""
from contextlib import ExitStack
import faulthandler
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from unittest.mock import patch


def check():
    with tempfile.TemporaryDirectory(prefix='kite-settings-loop-') as temporary, ExitStack() as guards:
        root = Path(temporary)
        env = {'HOME': str(root), 'QT_QPA_PLATFORM': 'offscreen'}
        for name in ('CONFIG', 'DATA', 'CACHE', 'STATE', 'RUNTIME'):
            path = root / name.lower()
            path.mkdir(mode=0o700)
            env['XDG_' + name + ('_DIR' if name == 'RUNTIME' else '_HOME')] = str(path)
        guards.enter_context(patch.dict(os.environ, env))
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QLineEdit
        import yaml
        from main.core import load_config
        from main.launcher import Jobs, Launcher
        from main.plugin_manager import Manager
        manager = Manager()
        package = manager.authored / 'settings-tool'
        package.mkdir(parents=True)
        (package / 'plugin.json').write_text(json.dumps({
            'id': 'settings-tool', 'name': 'Settings fixture', 'api_version': 2,
            'configuration': [{'key': 'directories', 'label': 'Folders', 'type': 'text', 'default': '[]'}]}))
        (package / '__init__.py').write_text('raise AssertionError("Metadata must not execute")\n')
        manager.config_path.write_text(yaml.safe_dump({'web': {'enabled': False},
                                                     'plugins': {'settings-tool': {'enabled': False}}}))
        manager.config_path.chmod(0o640)
        def provider(self, key, request, callback, timeout=2500):
            assert request['kind'] == 'apps'
            QTimer.singleShot(0, lambda: callback({'results': []}))
        guards.enter_context(patch.object(Jobs, 'builtin', provider))
        def forbidden(*args, **kwargs):
            raise AssertionError('No external processes/network in Settings regression')
        for name in ('subprocess.Popen', 'subprocess.run', 'socket.create_connection'):
            guards.enter_context(patch(name, side_effect=forbidden))
        app = QApplication.instance() or QApplication([])
        cfg = load_config(manager.config_path)
        cfg['math']['enabled'] = False
        window = Launcher(cfg, config_path=manager.config_path, favorites_path=root / 'favorites.sqlite')
        state = {'error': None, 'done': False, 'stage': 'startup', 'until': lambda: True,
                 'deadline': time.monotonic() + 4, 'ticks': 0, 'last': time.monotonic(), 'gap': 0.0}
        def scenario():
            yield 'index', lambda: not window.apps_loading
            window.set_mode('marketplace')
            yield 'inventory', lambda: window.marketplace is not None and not window.marketplace.busy
            controller = window.marketplace
            for count in range(20):
                controller.configure_plugin()
                field = window.plugin_detail_panel.findChild(QLineEdit, 'config-directories')
                assert field is not None and window.active_form() is not None
                value = json.dumps([str(root / f'pictures {count}')])
                field.setText(value)
                field.textEdited.emit(value)
                form = window.active_form()
                assert form.dirty
                if count == 0:
                    before = manager.config_path.read_bytes()
                    with patch('main.plugin_settings_store.os.replace', side_effect=OSError('fixture write failure')):
                        form.submit_button.click()
                        yield 'failed save', lambda: not controller.busy
                    assert controller.error and manager.config_path.read_bytes() == before
                    assert window.active_form() is form and field.text() == value and form.dirty
                if count == 1:
                    from main import plugin_settings_store
                    original_save = plugin_settings_store.save_settings
                    def slow_save(*args):
                        time.sleep(.15)
                        return original_save(*args)
                    deferred = []
                    with patch.object(plugin_settings_store, 'save_settings', side_effect=slow_save):
                        form.submit_button.click()
                        assert not controller.leave(lambda: deferred.append(True))
                        yield 'pending leave/save', lambda: not controller.busy and bool(deferred)
                else:
                    form.submit_button.click()
                    form.submit_button.click()  # Busy/duplicate clicks must not queue another write.
                    yield 'save ' + str(count), lambda: not controller.busy
                assert not controller.error, controller.error
                saved = yaml.safe_load(manager.config_path.read_text())
                assert saved['plugins']['settings-tool']['config']['directories'] == value
                assert saved['plugins']['settings-tool']['enabled'] is False and saved['web']['enabled'] is False
                assert stat.S_IMODE(manager.config_path.stat().st_mode) == 0o640
                assert controller.page == 'list'
                assert window.active_form() is None, 'Saved dirty form remains active but hidden; navigation cannot leave'
            window.back()
            yield 'back after save', lambda: window.mode == 'root'
            assert state['ticks'] >= 20 and state['gap'] < 1.0, state
        steps = scenario()
        timer = QTimer()
        timer.setInterval(10)
        def advance():
            now = time.monotonic()
            state['gap'] = max(state['gap'], now - state['last'])
            state['last'], state['ticks'] = now, state['ticks'] + 1
            try:
                assert now < state['deadline'], 'Timeout during ' + state['stage']
                if state['until']():
                    state['stage'], state['until'] = next(steps)
                    state['deadline'] = time.monotonic() + 4
            except StopIteration:
                state['done'] = True
                timer.stop()
                app.exit(0)
            except BaseException as error:
                state['error'] = error
                timer.stop()
                app.exit(1)  # quit() can itself be vetoed by the stale dirty form under test.
        timer.timeout.connect(advance)
        window.show()
        timer.start()
        faulthandler.dump_traceback_later(12, exit=True)
        try:
            app.exec()
            if state['error']:
                raise state['error']
            assert state['done'], state
        finally:
            faulthandler.cancel_dump_traceback_later()
            timer.stop()
            if window.active_form():
                window.active_form().dirty = False
            if window.marketplace:
                window.marketplace.pool.shutdown(wait=True, cancel_futures=True)
                window.marketplace.busy = False
            window.close()
            app.processEvents()
    print('Settings: real Launcher, executor/queued Save, 20 cycles, heartbeat, disk/mode preservation and Back passed')


if __name__ == '__main__':
    check()
