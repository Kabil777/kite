"""Real headless Qt event-loop check with temporary metadata and no real providers/stores."""
from contextlib import ExitStack
import ast
import faulthandler
import json
import os
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch


def forbidden(*args, **kwargs):
    raise AssertionError('Unexpected process/network operation in isolated UI check')


def check():
    with tempfile.TemporaryDirectory(prefix='kite-marketplace-ui-') as temporary, ExitStack() as guards:
        root = Path(temporary)
        guards.enter_context(patch.dict(os.environ, {
            'HOME': str(root), 'XDG_DATA_HOME': str(root / 'data'), 'XDG_CONFIG_HOME': str(root / 'config'),
            'XDG_STATE_HOME': str(root / 'state'), 'XDG_CACHE_HOME': str(root / 'cache'),
            'XDG_RUNTIME_DIR': str(root / 'runtime'), 'QT_QPA_PLATFORM': 'offscreen',
        }))
        (root / 'runtime').mkdir(mode=0o700)
        # Redirect paths before importing the GUI. Never call launcher.main()/IPC.
        from PySide6.QtCore import QRect, QTimer, Qt
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QLineEdit
        from main.core import load_config
        from main import plugin_manager as pm
        from main.launcher import Jobs, Launcher
        from main.plugin_runtime import LIVE_UNITS
        from main.frontend import widgets as icons

        app = QApplication.instance() or QApplication([])
        manager = pm.Manager()
        package = manager.root / 'gopass'
        package.mkdir(parents=True)
        (package / 'plugin.json').write_text(json.dumps({'id': 'gopass', 'name': 'Passwords', 'api_version': 2}))
        (package / '__init__.py').write_text('raise AssertionError("Fixture plugin must never execute")\n')
        pm._atomic_json(manager.state / 'registry.json', {'schema': 1, 'catalog_url': '', 'enabled': {'gopass': False}, 'installed': {}})
        for target in ('subprocess.Popen', 'subprocess.run', 'socket.create_connection', 'socket.socket.connect', 'socket.getaddrinfo'):
            guards.enter_context(patch(target, side_effect=forbidden))
        guards.enter_context(patch.object(pm, 'units_clear', return_value=True))
        guards.enter_context(patch.object(pm, 'download', return_value=b'{"schema":1,"plugins":[]}'))
        # Test name matching without importing/executing a plugin or its backend.
        from tests.plugin_sources import gopass_source
        source = gopass_source() / '__init__.py'
        tree = ast.parse(source.read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_name_icon'
                 or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '_ICON_RULES' for t in node.targets)]
        names = {'re': re}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<offline-icons>', 'exec'), names)
        choose = names['_name_icon']
        assert choose('GitHub.com/account')[1] == '\uf09b'
        assert choose('github.com/admin')[1] == '\uf132'
        assert choose('personal/Gmail')[1] == '\uf0e0'
        assert choose('work/ssh')[1] == '\uf120'
        assert choose('notgithub.com/account')[1] == '\uf0ac'
        assert choose('unrecognized', vault=True) == ('folder', '\uf187')
        assert choose('unrecognized') == ('dialog-password', '\uf084')
        with patch.object(icons.QFontDatabase, 'families', return_value=[]):
            icons.glyph_font.cache_clear()
            assert icons.glyph_font('\uf084', 19) is None
            assert icons.glyph_font('+', 19) is not None
            canvas = QPixmap(32, 32)
            canvas.fill(Qt.GlobalColor.transparent)
            painter = QPainter(canvas)
            try:
                with patch.object(icons, 'themed_icon', wraps=icons.themed_icon) as fallback:
                    icons.paint_icon(painter, QRect(0, 0, 32, 32), '\uf0c5', 'edit-copy')
                    fallback.assert_called_once_with('edit-copy')
            finally:
                painter.end()
        raw = SimpleNamespace(isValid=lambda: True, supportsCharacter=lambda _: True)
        with patch.object(icons.QFontDatabase, 'families', return_value=['Fixture Nerd Font']), patch.object(icons.QRawFont, 'fromFont', return_value=raw):
            icons.glyph_font.cache_clear()
            assert icons.glyph_font('\uf084', 19).family() == 'Fixture Nerd Font'
        icons.glyph_font.cache_clear()
        mascot, glyph = icons.plugin_icon('gopass', '\uf084')
        assert not glyph and Path(mascot).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        assert not icons.QIcon(mascot).pixmap(28, 28).isNull()
        requests = []
        def provider(self, key, request, callback, timeout=2500):
            requests.append(request)
            assert request['kind'] == 'apps'
            QTimer.singleShot(0, lambda: callback({'results': []}))
        guards.enter_context(patch.object(Jobs, 'builtin', provider))
        config = load_config(manager.config_path)
        config['web']['enabled'] = config['math']['enabled'] = False
        config['icons']['theme'] = 'Adwaita'
        guards.enter_context(patch.object(icons.QFontDatabase, 'families', return_value=[]))
        icons.glyph_font.cache_clear()  # Exercise the complete window without Nerd Font availability.
        window = Launcher(config, config_path=manager.config_path, favorites_path=root / 'favorites.sqlite')
        state = {'stage': 'startup', 'until': lambda: True, 'deadline': time.monotonic() + 3, 'done': False, 'error': None}
        quits = []
        app.aboutToQuit.connect(lambda: quits.append(state['stage']))

        def scenario():
            yield 'app indexing', lambda: not window.apps_loading
            assert 'gopass' not in window.plugins
            assert not window.lead.icon().isNull()
            window.search.setText('marketplace')
            item = next(window.list.item(i) for i in range(window.list.count())
                        if window.list.item(i).data(Qt.ItemDataRole.UserRole) == 'cmd:marketplace')
            QTest.mouseClick(window.list.viewport(), Qt.MouseButton.LeftButton,
                             pos=window.list.visualItemRect(item).center())
            yield 'opening marketplace', lambda: window.marketplace is not None and not window.marketplace.busy
            assert window.isVisible() and window.mode == 'marketplace'
            assert window.marketplace.selected()['id'] == 'gopass'
            assert window.marketplace.selected()['status'] == 'Disabled'
            assert window.selected_result().tag == 'Installed · Local'
            assert window.marketplace.primary_title() == 'Options'
            assert 'Allow in KITE' in [action[0] for action in window.marketplace.actions()]
            assert not window.plugin_detail_panel.isVisible()
            # Give Qt normal event-loop turns to execute deferred widget deletions.
            ready = time.monotonic() + .1
            yield 'marketplace widget lifetime', lambda: time.monotonic() >= ready
            assert window.isVisible()

            window.clip_filter.setCurrentText('Browse')
            yield 'unconfigured Browse', lambda: not window.marketplace.busy
            assert window.marketplace.tab == 'Browse' and 'catalog' in window.marketplace.error.lower()
            window.clip_filter.setCurrentText('Configure')
            assert window.active_form() is not None
            field = window.plugin_detail_panel.findChild(QLineEdit, 'catalog-url')
            url = 'https://raw.githubusercontent.com/fixture/catalog/main/index.json'
            field.setText(url)
            field.textEdited.emit(url)
            assert window.active_form().dirty
            window.active_form().submit_button.click()
            yield 'source save', lambda: not window.marketplace.busy
            assert window.marketplace.page == 'list' and manager.registry()['catalog_url'] == url

            window.marketplace.show_browse()
            yield 'empty catalog', lambda: not window.marketplace.busy
            assert window.marketplace.tab == 'Browse' and not window.marketplace.error
            assert 'empty' in window.marketplace.status.lower()
            window.back()
            yield 'back navigation', lambda: window.mode == 'root'
            assert window.search.text() == 'marketplace'
            password_row = next(row for row in window.commands if row.id == 'plugin:gopass')
            assert password_row.icon == mascot and not password_row.glyph
            assert all(request['kind'] == 'apps' for request in requests)

        steps = scenario()
        driver = QTimer()
        driver.setInterval(5)
        def advance():
            try:
                assert time.monotonic() < state['deadline'], 'Timed out during ' + state['stage']
                if not state['until']():
                    return
                state['stage'], state['until'] = next(steps)
                state['deadline'] = time.monotonic() + 3
            except StopIteration:
                state['done'] = True
                driver.stop()
                window.close()
                app.quit()
            except BaseException as error:
                state['error'] = error
                driver.stop()
                app.quit()
        driver.timeout.connect(advance)
        window.show()  # Offscreen only: no desktop window or display connection.
        driver.start()
        faulthandler.dump_traceback_later(10, exit=True)
        try:
            app.exec()  # processEvents-only tests miss real Qt shutdown/deferred-delete behavior.
            if state['error'] is not None:
                raise state['error']
            assert state['done'], f'Application quit unexpectedly during {state["stage"]}; quit signals: {quits}'
        finally:
            driver.stop()
            faulthandler.cancel_dump_traceback_later()
            if window.marketplace:
                window.marketplace.cancelled.set()
                window.marketplace.pool.shutdown(wait=True, cancel_futures=True)
                window.marketplace.busy = False
            form = window.active_form()
            if form:
                form.dirty = False
            window.close()
            app.processEvents()
        print('Marketplace/icons: real Qt loop, native forms/Actions, Back, offline name matching, mascot and Nerd/native fallbacks passed headlessly.')


if __name__ == '__main__':
    check()
