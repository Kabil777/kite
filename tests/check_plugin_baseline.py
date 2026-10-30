"""Foreground-only baseline checks; fake plugins, temporary XDG paths, no OS actions."""
import ast
import importlib
import os
from pathlib import Path
import re
import tempfile
import threading
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from unittest import SkipTest
from tests.plugin_sources import gopass_source

ROOT = Path(__file__).resolve().parents[1]


def python_blocks(text):
    fence, language, body = '', '', []
    for line in text.splitlines():
        match = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if not fence:
            if match:
                fence, language, body = match[1], match[2].strip(), []
        elif match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
            if language == 'python':
                yield '\n'.join(body)
            fence = ''
        else:
            body.append(line)
    assert not fence, 'Unclosed Markdown fence'


def public_imports():
    facade = importlib.import_module('plugin_api')
    names = {'Toast', 'validate_toast'}
    for path in [ROOT / 'README.md', *(ROOT / 'docs').glob('*.md')]:
        for block in python_blocks(path.read_text()):
            for node in ast.walk(ast.parse(block)):
                if isinstance(node, ast.ImportFrom) and node.module == 'plugin_api':
                    names.update(item.name for item in node.names)
    missing = sorted(name for name in names if not hasattr(facade, name) or name not in facade.__all__)
    assert not missing, 'Missing facade exports: ' + ', '.join(missing)


def installer_link():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        app = root / 'data/kite'
        (app / '.venv/bin').mkdir(parents=True)
        python = app / '.venv/bin/python'
        python.write_text('#!/bin/sh\nexec ' + str(ROOT / '.venv/bin/python') + ' "$@"\n')
        python.chmod(0o700)
        tools = root / 'tools'; tools.mkdir()
        for name in ('uv', 'update-desktop-database', 'gtk-update-icon-cache'):
            path = tools / name; path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o700)
        config = root / 'config/kite/config.yaml'; config.parent.mkdir(parents=True)
        config.write_text('# preserve fixture settings\n')
        env = dict(os.environ, HOME=str(root / 'home'), XDG_DATA_HOME=str(root / 'data'),
                   XDG_CONFIG_HOME=str(root / 'config'), PATH=str(tools) + ':' + os.environ['PATH'])
        result = subprocess.run(['bash', str(ROOT / 'install.sh')],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert not list((app / 'plugins').iterdir()), 'Default installation installed an external plugin'
        assert not (app / 'examples/gopass').exists(), 'Default installation bundled the Gopass package'
        installed = app / 'plugins/node-tool'; installed.mkdir()
        (installed / 'custom.py').write_text('fixture-owned plugin change')
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--link-example', 'node-tool'],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert installed.is_symlink() and installed.resolve() == ROOT / 'examples/node-tool'
        for name in ('backend.mjs', 'plugin.json'):
            assert (installed / name).is_file(), 'Installed example missing ' + name
        backups = list(app.glob('.plugin-backup.*/node-tool/custom.py'))
        assert len(backups) == 1 and backups[0].read_text() == 'fixture-owned plugin change'
        assert config.read_text() == '# preserve fixture settings\n'
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--repo', 'https://example.invalid/kite',
                                 '--link-example', 'node-tool'], env=env, capture_output=True, timeout=5)
        assert result.returncode != 0 and b'persistent local checkout' in result.stderr


def gopass_responses():
    from main.plugin_api import Event, HostServices, Toast, _load_plugin
    _, plugin = _load_plugin(gopass_source())
    host = HostServices({'gopass': {'store': ''}, 'plugins': {}})
    with patch.dict(plugin.sync.__globals__, {'_run': lambda *a, **kw: ''}):
        view = plugin.search('', host)
        assert {item.shortcut for item in view.actions} == {'Alt+I', 'Ctrl+S'}
        response = plugin.handle_event(Event('sync', 'activate'), host)
        assert isinstance(response, Toast) and response.tone == 'error' and 'remote' in response.message
        form = plugin.handle_event(Event('create', 'activate', {'query': 'example.com/'}), host)
        assert form.nodes[0].type == 'form' and form.actions[0].shortcut == 'Ctrl+G'


def frontend():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from main.core import DATA, Result, load_config
    from main.launcher import Jobs, Launcher
    from main.plugin_api import Action, MenuAction, Result as PluginResult, Toast, View
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {
        'XDG_CONFIG_HOME': temporary + '/config', 'XDG_DATA_HOME': temporary + '/data',
        'XDG_STATE_HOME': temporary + '/state', 'XDG_CACHE_HOME': temporary + '/cache',
    }), patch.object(Jobs, 'builtin', lambda self, key, request, callback, timeout=2500: callback({'results': []})), \
            patch('main.applications.Applications.start', lambda self: None):
        window = Launcher(load_config(DATA / 'defaults.yaml'), favorites_path=Path(temporary) / 'favorites.sqlite')
        guide = Path(temporary) / 'fixture-guide'
        guide.mkdir()
        (guide / 'HOWTO.md').write_text('# Passwords fixture\n\n## Copy a password\nSynthetic guide; no backend or secret store.')
        window.plugins['gopass'] = ({'name': 'Passwords'}, guide)
        window.commands.append(Result('plugin:gopass', 'Passwords', kind='plugin', value='gopass'))
        window.show(); window.activateWindow(); app.processEvents()
        try:
            window.search.setText('Passwords')
            window.toggle_help()
            assert window.plugin_help_title.text() == 'KITE'
            assert 'Open an app' in window.plugin_help_browser.toPlainText()
            window.back()
            window.set_mode('plugin:gopass'); window.plugin_timer.stop()
            view = View(results=(PluginResult('fixture', 'Fixture'),), actions=(
                MenuAction('sync', 'Sync passwords', Action('activate', 'sync'), shortcut='Ctrl+S'),))
            window.update_plugin_view('gopass', view)
            captured = []
            with patch.object(window, 'activate_result', captured.append):
                window.search.setFocus()
                QTest.keyClick(window.search, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
                app.processEvents()
            assert len(captured) == 1 and captured[0].value['result_id'] == 'sync'
            state = (window.search.text(), window.selected_result().id, tuple(window.navigation_stack))
            call = SimpleNamespace(submitted=False, navigation=None)
            window.plugin_calls['gopass'] = (call, threading.Event(), window.revision)
            window.plugin_call_finished('gopass', window.revision, Toast('No remote', 'Configure a Git remote.', 'error'), None)
            assert window.plugin_views['gopass'] is view and window.toast.isVisible()
            assert state == (window.search.text(), window.selected_result().id, tuple(window.navigation_stack))
            assert not window.toast.timer.isActive()
            window.toast.close_button.click(); assert not window.toast.isVisible()
            window.show_copy_toast(); assert window.toast.text() == 'Copied to clipboard'
            assert window.toast.timer.isActive()
            window.toggle_help(); assert 'Copy a password' in window.plugin_help_browser.toPlainText()
            window.back(); window.set_mode('root')
            assert not window.plugin_shortcuts
            window.show_actions()
            assert all(action.property('keys') for action in window.action_menu.actions() if not action.isSeparator())
        finally:
            window.jobs.stop(); window.close(); app.processEvents()


if __name__ == '__main__':
    failures = []
    for check in (public_imports, installer_link, gopass_responses, frontend):
        try:
            check()
            print(check.__name__ + ': OK')
        except SkipTest as reason:
            print(check.__name__ + ': SKIP: ' + str(reason))
        except Exception as error:
            failures.append(check.__name__)
            print(check.__name__ + ': FAIL: ' + str(error))
    raise SystemExit(bool(failures))
