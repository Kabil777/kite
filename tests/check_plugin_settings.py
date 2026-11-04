"""Regression: selected plugin survives list → Settings → Save, without executing any plugin."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch


def check():
    with tempfile.TemporaryDirectory(prefix='kite-plugin-settings-') as temporary, ExitStack() as guards:
        root = Path(temporary)
        environment = {'HOME': str(root), 'QT_QPA_PLATFORM': 'offscreen'}
        for name in ('CONFIG', 'DATA', 'CACHE', 'STATE', 'RUNTIME'):
            path = root / name.lower()
            path.mkdir(mode=0o700)
            environment['XDG_' + name + ('_DIR' if name == 'RUNTIME' else '_HOME')] = str(path)
        guards.enter_context(patch.dict(os.environ, environment))
        from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QListWidget, QVBoxLayout, QWidget
        import yaml
        from main.core import load_config
        from main.frontend.form import Form
        from main.plugin_manager import Manager
        from main.plugin_manager_ui import Marketplace

        app = QApplication.instance() or QApplication([])
        manager = Manager()
        package = manager.authored / 'settings-tool'
        package.mkdir(parents=True)
        manifest = {'id': 'settings-tool', 'name': 'Settings fixture', 'api_version': 2,
                    'configuration': [{'key': 'directories', 'label': 'Folders (JSON list)', 'type': 'text', 'default': '[]'}]}
        (package / 'plugin.json').write_text(json.dumps(manifest))
        (package / '__init__.py').write_text('raise AssertionError("Settings must never execute the plugin")\n')
        original = {'web': {'enabled': False}, 'plugins': {'settings-tool': {'enabled': False}}}
        manager.config_path.write_text(yaml.safe_dump(original))
        manager.config_path.chmod(0o640)

        class Host(QWidget):
            def __init__(self):
                super().__init__()
                self.config_path = manager.config_path
                self.cfg = load_config(self.config_path)
                self.package_lease = object()
                self.mode = 'marketplace'
                self.mode_rows = []
                self.catalogs = {}
                self.clip_filter = QComboBox()
                self.clip_filter.addItems(['Installed', 'Browse', 'Configure'])
                self.list = QListWidget()
                self.plugin_detail_panel = QWidget(self)
                self.plugin_detail_layout = QVBoxLayout(self.plugin_detail_panel)
                self.marketplace = Marketplace(self)
                self.toasts = []
            def selected_result(self):
                return self.mode_rows[0] if self.mode_rows else None
            def configure_catalog(self, catalog):
                pass
            def render(self):
                self.marketplace.show_details()
            def active_form(self):
                return self.plugin_detail_panel.findChild(Form)
            def confirm_form_leave(self, callback):
                return True
            def plugin_event(self, identifier, event):
                assert identifier == 'marketplace'
                self.marketplace.handle_event(event)
            def show_toast(self, toast):
                self.toasts.append(toast)

        host = Host()
        controller = host.marketplace
        try:
            controller.installed_rows = manager.inventory()
            controller.publish()
            assert controller.selected()['id'] == 'settings-tool'
            controller.configure_plugin()  # Must go through publish(), which clears the result selection.
            assert controller.page == 'plugin_config' and not controller.rows and not host.mode_rows
            assert controller.selected()['id'] == 'settings-tool'
            form = host.active_form()
            assert form is not None, 'Settings lost its selected plugin while opening the full-width form'
            field = host.plugin_detail_panel.findChild(QLineEdit, 'config-directories')
            assert field is not None and field.text() == '[]'
            value = json.dumps([str(root / 'pictures with spaces; commas, too')])
            field.setText(value)
            # Keep the real save operation; only bypass the unrelated runtime-quiescence scheduler.
            def inline_run(message, operation, success, **options):
                success(operation())
            with patch.object(controller, 'run', side_effect=inline_run):
                form.submit_button.click()
            saved = yaml.safe_load(manager.config_path.read_text())
            assert saved['plugins']['settings-tool']['config']['directories'] == value
            assert saved['plugins']['settings-tool']['enabled'] is False and saved['web']['enabled'] is False
            assert stat.S_IMODE(manager.config_path.stat().st_mode) == 0o640
            assert controller.page == 'list' and controller.target is None
            controller.configure_plugin()
            field = host.plugin_detail_panel.findChild(QLineEdit, 'config-directories')
            assert field is not None and field.text() == value
            # The same snapshot also keeps the destination stable if a stale list selection disappears.
            controller.rows.clear()
            assert controller.selected()['id'] == 'settings-tool'
        finally:
            controller.pool.shutdown(wait=True, cancel_futures=True)
            host.close()
            app.processEvents()
    print('Plugin Settings: real list/form transition, save, reopen and config/mode preservation passed')


if __name__ == '__main__':
    check()
