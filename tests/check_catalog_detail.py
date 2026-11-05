"""Real Qt split detail surface with synthetic images and no plugin/process execution."""
from contextlib import ExitStack
import os
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch


def check():
    with tempfile.TemporaryDirectory(prefix='kite-detail-') as temporary, ExitStack() as guards:
        root = Path(temporary)
        env = {'HOME': str(root), 'QT_QPA_PLATFORM': 'offscreen'}
        for name in ('CONFIG', 'DATA', 'CACHE', 'STATE', 'RUNTIME'):
            path = root / name.lower()
            path.mkdir(mode=0o700)
            env['XDG_' + name + ('_DIR' if name == 'RUNTIME' else '_HOME')] = str(path)
        guards.enter_context(patch.dict(os.environ, env))
        from PySide6.QtCore import QPoint, QTimer, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPolygon
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QListWidget
        from main.core import load_config
        from main.launcher import Jobs, Launcher
        from main.plugin_api import Action, Catalog, Node, Result, View
        from main.plugin_protocol import from_wire, to_wire
        from main.host_services import plugin_row
        app = QApplication.instance() or QApplication([])
        fields = [{'key': 'duration', 'label': 'Duration', 'type': 'number', 'default': .5, 'min': 0, 'max': 60, 'step': .1},
                  {'key': 'mode', 'label': 'Fit', 'type': 'choice', 'options': ['crop', 'fit'], 'default': 'crop'}]
        results = []
        for name, width, height, color in [('Forest', 1280, 720, '#497850'), ('Coast', 480, 900, '#547ca6')]:
            path = root / (name + '.png')
            image = QImage(width, height, QImage.Format.Format_RGB32)
            image.fill(QColor(color))
            painter = QPainter(image)
            painter.setBrush(QColor('#c0d5bf' if name == 'Forest' else '#bdd5e7'))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(0, 0, width, height // 2)
            painter.setBrush(QColor('#d8b779'))
            painter.drawEllipse(width * 3 // 4, height // 8, height // 8, height // 8)
            painter.setBrush(QColor('#315c49' if name == 'Forest' else '#244f72'))
            painter.drawPolygon(QPolygon([QPoint(0, height), QPoint(0, height // 2), QPoint(width // 3, height // 4),
                                          QPoint(width * 2 // 3, height * 2 // 3), QPoint(width, height // 3), QPoint(width, height)]))
            painter.end()
            assert image.save(str(path))
            results.append(Result(name, name, 'Fixture images', action=Action('activate', name),
                                  preview=Node('image_preview', name + '-image', {'source': str(path), 'alt': name})))
        view = View(results=tuple(results), catalog=Catalog(layout='list_detail', preserve_order=True, settings_keys=('mode', 'duration'),
                                                           option_keys=('mode', 'duration'), primary_label='Apply to all displays'))
        assert from_wire(json.loads(json.dumps(to_wire(view)))) == view
        def provider(self, key, request, callback, timeout=2500):
            assert request['kind'] == 'apps'
            QTimer.singleShot(0, lambda: callback({'results': []}))
        guards.enter_context(patch.object(Jobs, 'builtin', provider))
        for target in ('subprocess.Popen', 'subprocess.run', 'socket.create_connection'):
            guards.enter_context(patch(target, side_effect=AssertionError('No backend calls during selection/options')))
        config = load_config(root / 'config.yaml')
        config['web']['enabled'] = config['math']['enabled'] = False
        window = Launcher(config, config_path=root / 'config.yaml', favorites_path=root / 'favorites.sqlite')
        state = {'stage': 'index', 'until': lambda: True, 'deadline': time.monotonic() + 4, 'error': None, 'done': False}
        def scenario():
            yield 'index', lambda: not window.apps_loading
            window.plugins['gallery'] = ({'id': 'gallery', 'name': 'Fixture gallery', 'configuration': fields}, root)
            window.mode = 'plugin:gallery'
            window.search.clear()
            window.plugin_timer.stop()
            window.plugin_views['gallery'] = view
            window.catalogs[window.mode] = view.catalog
            window.mode_rows = [plugin_row('gallery', row) for row in view.results]
            window.render()
            yield 'first preview', lambda: window.catalog_preview_ready == window.catalog_preview_key and not window.catalog_detail.image._source.isNull()
            assert window.catalog_detail.isVisible() and not window.plugin_detail_panel.isVisible()
            assert window.primary_label.text() == 'Apply to all displays'
            shots = os.environ.get('KITE_UI_SHOTS')
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                window.grab().save(str(Path(shots) / 'landscape.png'))
            window.list.setCurrentRow(1)
            yield 'portrait preview', lambda: window.catalog_preview_ready == window.catalog_preview_key and '480 × 900' in window.catalog_detail.caption.text()
            spin = window.catalog_detail.quick.editors['duration']
            spin.setFocus()
            selected = window.selected_result().id
            QTest.keyClick(spin, Qt.Key.Key_Up)
            assert window.selected_result().id == selected
            assert window.catalog_option_values()['duration'] > .5
            assert not (root / 'config.yaml').exists(), 'Session editing wrote YAML'
            window.catalog_detail.toggle_options()
            assert window.catalog_detail.options.isVisible()
            window.catalog_detail.toggle_options()
            # Stress latest-selection scheduling; no stale image may win.
            for index in range(30):
                window.list.setCurrentRow(index % 2)
            yield 'latest selection', lambda: window.catalog_preview_ready == window.catalog_preview_key
            assert 'Coast' in window.catalog_detail.caption.text()
            assert len(window.image_loader.pending) <= 64 and len(window.image_loader.running) <= 2
            assert window.image_loader.cache_bytes <= 32 * 1024 * 1024
            shots = os.environ.get('KITE_UI_SHOTS')
            if shots:
                target = Path(shots)
                target.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(target / 'split-preview.png'))
                window.catalog_detail.toggle_options()
                window.grab().save(str(target / 'options.png'))
            # Exercise the actual optional wallpaper contract and view in the same native browser.
            source = Path(__file__).resolve().parents[2] / 'kite_plugins/plugins/hyprland-wallpapers'
            if source.exists():
                from main.plugin_api import HostServices, _load_plugin
                manifest, plugin = _load_plugin(source)
                identifier = manifest['id']
                window.cfg['plugins'][identifier] = {'config': {'directories': [str(root)]}}
                import yaml
                (root / 'config.yaml').write_text(yaml.safe_dump({'plugins': {identifier: window.cfg['plugins'][identifier]}}))
                actual = plugin.search('', HostServices(window.cfg))
                window.mode = 'plugin:' + identifier
                window.plugins[identifier] = manifest, source
                window.plugin_views[identifier] = actual
                window.catalogs[window.mode] = actual.catalog
                window.mode_rows = [plugin_row(identifier, row) for row in actual.results]
                window.configure_catalog(actual.catalog)
                window.render()
                yield 'wallpaper preview', lambda: window.catalog_preview_ready == window.catalog_preview_key
                assert window.catalog_detail.quick.fields[0]['key'] == 'resize'
                window.list.setCurrentRow(1)
                yield 'wallpaper landscape', lambda: window.catalog_preview_ready == window.catalog_preview_key and 'Forest.png' in window.catalog_detail.caption.text()
                if shots:
                    window.grab().save(str(Path(shots) / 'wallpaper.png'))
                # Persist defaults through the real executor, but do not start a worker/provider as a test side effect.
                with patch.object(window, 'request_plugin'):
                    window.save_catalog_defaults()
                    yield 'save defaults', lambda: not window.marketplace.busy
                assert not window.marketplace.error
                import yaml
                saved = yaml.safe_load((root / 'config.yaml').read_text())
                assert saved['plugins'][identifier]['config']['directories'] == [str(root)]
                if shots:
                    window.toast.dismiss()
                    window.setFixedSize(560, 500)
                    window.render()
                    yield 'small layout', lambda: window.content_splitter.orientation() == Qt.Orientation.Vertical and window.catalog_preview_ready == window.catalog_preview_key
                    window.grab().save(str(Path(shots) / 'small.png'))
                authored = root / 'config/kite/plugin' / identifier
                authored.parent.mkdir(parents=True, exist_ok=True)
                authored.symlink_to(source, target_is_directory=True)
                window.setFixedSize(750, 520)
                window.open_plugin_settings(identifier)
                yield 'native wallpaper Settings', lambda: not window.marketplace.busy and window.marketplace.page == 'plugin_config'
                folders = window.plugin_detail_panel.findChild(QListWidget, 'config-directories')
                assert folders is not None and folders.item(0).text() == str(root)
                if shots:
                    window.grab().save(str(Path(shots) / 'settings.png'))
                window.back()  # Retire the form without starting any wallpaper backend.
                assert window.active_form() is None
            from main.frontend import clear_layout, render_nodes
            from main.frontend.image_preview import ImagePreview
            render_nodes(window.plugin_detail_layout,
                         (Node('image_preview', 'async-node', {'source': str(root / 'Forest.png')}),),
                         lambda event: None, window.cfg, window.image_loader)
            node_image = window.plugin_detail_panel.findChild(ImagePreview, 'async-node')
            yield 'async declarative image', lambda: not node_image._source.isNull()
            clear_layout(window.plugin_detail_layout)
            assert node_image.property('retired')
            state['done'] = True
        sequence = scenario()
        timer = QTimer()
        timer.setInterval(10)
        def advance():
            try:
                assert time.monotonic() < state['deadline'], state['stage']
                if state['until']():
                    state['stage'], state['until'] = next(sequence)
                    state['deadline'] = time.monotonic() + 4
            except StopIteration:
                timer.stop()
                app.exit(0)
            except BaseException as error:
                state['error'] = error
                timer.stop()
                app.exit(1)
        timer.timeout.connect(advance)
        window.show()
        timer.start()
        try:
            app.exec()
            if state['error']:
                raise state['error']
            assert state['done'], state
        finally:
            timer.stop()
            window.close()
            app.processEvents()
    print('Catalog detail: real split preview, wire roundtrip, session controls, focus routing, stale selection and image bounds passed')


if __name__ == '__main__':
    check()
