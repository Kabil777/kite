"""Trusted marketplace controller using the launcher's lists, filters, forms and Actions."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
import tempfile
from threading import Event
import time
import yaml

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from .core import Result
from .frontend import input_values, render_nodes
from .frontend.widgets import plugin_icon
from .plugin_api import API_VERSION, Catalog, Node, Toast
from .plugin_manager import Manager, ManagerError, read_guide

WARNING = ('Plugins run with your full user permissions, including access to files, secrets and network. '
           'Systemd contains failures, not permissions. A checksum is not a safety review. '
           'Dependencies are never installed automatically.')
FILTERS = ('Installed', 'Browse', 'Configure')


class Marketplace(QObject):
    finished = Signal(object, str)

    def __init__(self, launcher):
        super().__init__(launcher)
        self.launcher = launcher
        self.cancelled = Event()
        self.manager = Manager(launcher.config_path, self.cancelled.is_set)
        self.busy = self.committing = self.quiescing = self.want_leave = False
        self.leave_callback = self.job = self.success = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='kite-plugin-manager')
        self.prepared = self.catalog = self.nodes = self.target = None
        self.records, self.installed_rows, self.rows = {}, [], {}
        self.tab, self.page = 'Installed', 'list'
        self.source_url, self.source_draft = '', None
        self.status, self.error = 'Installed packages are inspected without executing them.', ''
        self.manual_key, self.manual_text = '', ''
        self.config_fields = []
        self.finished.connect(self.completed, Qt.ConnectionType.QueuedConnection)
        self.cleanup_timer = QTimer(self)
        self.cleanup_timer.setInterval(25)
        self.cleanup_timer.timeout.connect(self.wait_cleanup)
        self.cleanup_action = None

    def presentation(self):
        return Catalog(placeholder='Search plugins…', heading=self.tab, filters=FILTERS,
                       footer=self.status[:256], empty='No matching plugins')

    def enter(self):
        self.page, self.nodes = 'list', None
        self.show_installed()

    def selected(self):
        if self.page == 'uninstall':
            return self.target
        result = self.launcher.selected_result()
        return self.rows.get(result.value) if result and result.kind == 'marketplace_item' else None

    def entry(self, identifier):
        if self.catalog is None:
            return None
        try:
            return self.manager.release(identifier, self.catalog)
        except ManagerError:
            return None

    def preview_matches(self, row):
        if not row or not self.prepared or self.prepared['entry']['id'] != row['id']:
            return False
        entry = self.entry(row['id'])
        return entry is not None and all(entry[k] == self.prepared['entry'][k] for k in ('version', 'sha256', 'catalog_url', 'repository'))

    def publish(self):
        host = self.launcher
        if host.mode != 'marketplace':
            return
        host.mode_loading, host.mode_error = self.busy, self.error
        host.catalogs['marketplace'] = self.presentation()
        host.configure_catalog(host.catalogs['marketplace'])
        host.clip_filter.blockSignals(True)
        host.clip_filter.setCurrentText('Configure' if self.page == 'source' else self.tab)
        host.clip_filter.blockSignals(False)
        host.clip_filter.setEnabled(not self.busy)
        host.plugin_detail_panel.setVisible(self.page != 'list')
        host.clip_filter.setAccessibleName('Marketplace view: ' + host.clip_filter.currentText())
        source = self.installed_rows if self.tab == 'Installed' else self.catalog['plugins'] if self.catalog else []
        self.rows, host.mode_rows = {}, []
        if self.page == 'list':
            for index, row in enumerate(source):
                key = f'{row["id"]}:{index}'
                self.rows[key] = row
                if self.tab == 'Installed':
                    subtitle = ''
                    tag = 'Installed · Marketplace' if row['managed'] else 'Installed · Local'
                else:
                    entry = self.entry(row['id'])
                    installed = any(item['id'] == row['id'] for item in self.installed_rows)
                    subtitle = row['summary'] + (' · Incompatible API' if entry['api_version'] != API_VERSION else '')
                    tag = 'Installed' if installed else 'Not installed'
                icon, glyph = plugin_icon(row['id'], '\uf1b3')
                host.mode_rows.append(Result('marketplace:' + key, row['name'], subtitle,
                                             icon, 'marketplace_item', key,
                                             keywords=' '.join(str(row.get(k, '')) for k in ('id', 'author', 'summary')),
                                             glyph=glyph, tag=tag))
        host.render()
        host.list.setEnabled(not self.busy)

    def show_details(self):
        # Package details live in row labels and Actions; forms still use the shared panel.
        if self.page == 'list':
            self.launcher.plugin_detail_panel.hide()
            return
        host, row = self.launcher, self.selected()
        writable = host.package_lease is not None
        unavailable = '' if writable else 'Restart KITE after other package operations finish; its package-use lease is unavailable.'
        if self.page == 'source':
            nodes = (Node('form', 'marketplace-source', {
                'title': 'Configure plugin catalog', 'description': 'Use a reviewed public raw.githubusercontent.com JSON catalog. Saving does not fetch it.',
                'error': self.error or unavailable, 'submit_text': 'Save source'}, children=(
                    Node('label', 'catalog-label', {'text': 'GitHub catalog URL'}),
                    Node('text_input', 'catalog-url', {'text': self.source_draft if self.source_draft is not None else self.source_url,
                                                     'placeholder': 'Public GitHub raw JSON URL'}),
                ), events={'submit': {}} if writable else {}),)
        elif self.page == 'review' and self.prepared:
            entry = self.prepared['entry']
            update = entry['id'] in self.records
            previous = self.records.get(entry['id'], {}).get('version', 'not installed')
            fields = [Node('label', 'review-origin', {'text': f'Installed: {previous}\nAuthor: {entry["author"]}\nRepository: {entry["repository"]}', 'wrap': True}),
                      Node('label', 'review-catalog', {'text': 'Catalog: ' + entry['catalog_url'], 'wrap': True}),
                      Node('label', 'review-artifact', {'text': 'Artifact: ' + entry['url'], 'wrap': True}),
                      Node('label', 'review-sha', {'text': 'SHA-256: ' + entry['sha256'], 'wrap': True}),
                      Node('label', 'review-requirements', {'text': 'Manual requirements: ' + '; '.join(entry.get('requirements', [])), 'wrap': True}),
                      Node('markdown', 'review-manual', {'text': self.prepared['guide'] or 'No packaged manual.'})]
            nodes = (Node('form', 'marketplace-approve', {
                'title': f'{"Update" if update else "Install"} {entry["name"]} {entry["version"]}',
                'description': WARNING, 'error': self.error or unavailable, 'submit_text': 'Approve update' if update else 'Approve install'
            }, children=tuple(fields), events={'submit': {}} if writable else {}),)
        elif self.page == 'plugin_config' and row:
            values = host.cfg.get('plugins', {}).get(row['id'], {}).get('config', {})
            fields = []
            for item in self.config_fields:
                key = item['key']
                value = values.get(key, item.get('default', item.get('options', [''])[0]))
                if item['type'] == 'choice' and value not in item['options']:
                    value = item.get('default', item['options'][0])
                if item['type'] == 'text' and not isinstance(value, str):
                    value = str(value)
                fields.append(Node('label', 'config-label-' + key, {'text': item['label']}))
                if item['type'] == 'choice':
                    fields.append(Node('select', 'config-' + key, {'options': item['options'], 'value': value}))
                else:
                    fields.append(Node('text_input', 'config-' + key, {'text': value, 'placeholder': item.get('placeholder', '')}))
            nodes = (Node('form', 'marketplace-plugin-config', {
                'title': row['name'] + ' settings', 'description': '',
                'error': self.error or unavailable, 'submit_text': 'Save configuration'}, children=tuple(fields),
                events={'submit': {}} if writable else {}),)
        elif self.page == 'uninstall' and row:
            nodes = (Node('form', 'marketplace-uninstall', {
                'title': 'Uninstall ' + row['name'], 'description': 'Remove only manager-owned code. YAML, authored source and external stores remain intact. Edited/extra files are retained privately.',
                'error': self.error or unavailable, 'submit_text': 'Uninstall'}, events={'submit': {}} if writable else {}),)
        elif row:
            entry, record = self.entry(row['id']), self.records.get(row['id'])
            details = [Node('label', 'package-name', {'text': row['name'], 'wrap': True}),
                       Node('type_label', 'package-status', {'text': row.get('status', 'Available')})]
            if record:
                details.append(Node('label', 'package-installed', {'text': f'Installed {record["version"]}\nSource: {record["repository"]}', 'wrap': True}))
            if entry:
                details.append(Node('label', 'package-available', {'text': f'{entry["summary"]}\nLatest {entry["version"]} · API {entry["api_version"]}\nAuthor: {entry["author"]}\nSource: {entry["repository"]}', 'wrap': True}))
                details.append(Node('label', 'package-requirements', {'text': 'Manual requirements: ' + '; '.join(entry.get('requirements', [])), 'wrap': True}))
            if row.get('error') or row.get('missing'):
                details.append(Node('label', 'package-error', {'text': row.get('error', '') + '\nMissing commands: ' + ', '.join(row.get('missing', [])), 'wrap': True}))
            if self.manual_key == row['id']:
                details.append(Node('markdown', 'package-manual', {'text': self.manual_text or 'No HOWTO.md or README.md packaged.'}))
            else:
                details.append(Node('label', 'package-actions-hint', {'text': 'Use Actions (Ctrl+K) to manage this package or read its manual.', 'wrap': True}))
            nodes = tuple(details)
        else:
            nodes = (Node('label', 'marketplace-empty', {'text': self.error or self.status, 'wrap': True}),
                     Node('label', 'marketplace-help', {'text': 'Choose Installed, Browse or Configure from the dropdown. Actions (Ctrl+K) includes source setup and recovery.', 'wrap': True}))
        if nodes != self.nodes:
            form = host.active_form()
            dirty, form_id = bool(form and form.dirty), form.objectName() if form else ''
            self.nodes = nodes
            render_nodes(host.plugin_detail_layout, nodes, lambda event: host.plugin_event('marketplace', event), host.cfg)
            form = host.active_form()
            if form and form.objectName() == form_id:
                form.dirty = dirty
        host.plugin_detail_panel.show()

    def primary_title(self):
        return 'Save' if self.page == 'plugin_config' else 'Options'

    def primary(self):
        if self.busy:
            return
        if self.page == 'plugin_config':
            form = self.launcher.active_form()
            if form:
                form.submit_button.click()
        elif self.page == 'list':
            self.launcher.show_actions()
        else:
            self.launcher.back()

    def actions(self):
        """Native Actions items, including useful commands when no package is selected."""
        row = self.selected()
        writable = self.launcher.package_lease is not None
        actions = []
        if self.busy:
            if not self.committing:
                actions.append(('Cancel operation', self.cancelled.set, '\uf05e'))
            return actions
        if self.page != 'list':
            form = self.launcher.active_form()
            if form and form.can_submit:
                actions.append((form.submit_button.text(), self.launcher.activate, '\uf00c'))
            actions.append(('Back to packages', self.launcher.back, '\uf060'))
        elif row:
            if self.tab == 'Browse' or row.get('managed'):
                entry = self.entry(row['id'])
                if entry is None or entry['api_version'] == API_VERSION:
                    actions.append((self.primary_title(), self.review, '\uf019'))
            if self.tab == 'Installed':
                actions.append(('Settings', self.configure_plugin, '\uf013'))
                actions.append(('Read manual', self.read_manual, '\uf02d'))
                if writable:
                    actions.append(('Allow in KITE', lambda: self.set_enabled(True), '\uf205'))
                    actions.append(('Disallow in KITE', lambda: self.set_enabled(False), '\uf204'))
                    if row.get('managed'):
                        actions.append(('Uninstall', self.uninstall, '\uf1f8'))
        actions.extend([('Refresh ' + self.tab.lower(), self.show_browse if self.tab == 'Browse' else self.show_installed, '\uf021'),
                        ('Configure catalog', self.configure_source, '\uf013')])
        if writable:
            actions.append(('Check cleanup / recover', self.recover, '\uf0ad'))
        return actions

    def filter_changed(self, value):
        if self.busy:
            return
        if not self.launcher.confirm_form_leave(lambda: self.filter_changed(value)):
            self.publish()
            return
        if value == 'Configure':
            self.configure_source()
        elif value == 'Browse':
            self.show_browse()
        else:
            self.show_installed()

    def configure_plugin(self):
        row = self.selected()
        if not row or self.tab != 'Installed' or self.busy:
            return
        if not self.launcher.confirm_form_leave(self.configure_plugin):
            return
        try:
            from .plugin_api import _read_manifest
            manifest = _read_manifest(Path(row['directory']))
            self.config_fields = manifest.get('configuration', [])
            if not self.config_fields:
                self.status = 'This plugin declares no settings in its contract.'
                self.launcher.show_toast(Toast('No plugin settings', self.status))
                return
            self.error = ''
            self.page = 'plugin_config'
            self.publish()
        except (OSError, ValueError) as error:
            self.error = str(error)[:512]
            self.publish()

    def configure_source(self):
        if not self.launcher.confirm_form_leave(self.configure_source):
            return
        self.page, self.error, self.source_draft = 'source', '', None
        self.publish()

    def handle_event(self, event):
        if event.name != 'submit' or self.busy or self.launcher.package_lease is None:
            return
        if event.node_id == 'marketplace-plugin-config':
            row = self.selected()
            if row:
                configured = input_values(self.launcher.plugin_detail_panel)
                values = {item['key']: configured['config-' + item['key']] for item in self.config_fields}
                def save():
                    path = self.launcher.config_path
                    if path.is_symlink():
                        raise ValueError('Refusing to replace a symlinked config.yaml')
                    current = yaml.safe_load(path.read_text()) if path.exists() else {}
                    current = current or {}
                    if not isinstance(current, dict):
                        raise ValueError('KITE configuration must be a mapping')
                    plugins = current.setdefault('plugins', {})
                    if not isinstance(plugins, dict):
                        raise ValueError('plugins configuration must be a mapping')
                    plugin = plugins.setdefault(row['id'], {})
                    if not isinstance(plugin, dict):
                        raise ValueError('plugin configuration must be a mapping')
                    config = plugin.setdefault('config', {})
                    if not isinstance(config, dict):
                        raise ValueError('plugin config must be a mapping')
                    config.update(values)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
                    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.config-')
                    try:
                        with os.fdopen(fd, 'w') as stream:
                            yaml.safe_dump(current, stream, sort_keys=False, allow_unicode=True)
                        os.chmod(temporary, mode)
                        os.replace(temporary, path)
                    finally:
                        if os.path.exists(temporary):
                            os.unlink(temporary)
                    return values
                def saved(_):
                    self.launcher.cfg['plugins'].setdefault(row['id'], {}).setdefault('config', {}).update(values)
                    self.error = ''
                    self.list_page('Saved plugin configuration.')
                self.run('Saving plugin configuration…', save, saved, committing=True)
        elif event.node_id == 'marketplace-source':
            source = input_values(self.launcher.plugin_detail_panel).get('catalog-url', '').strip()
            self.source_draft = source
            def saved(message):
                self.source_url, self.source_draft = source, None
                self.catalog = None
                self.list_page(message)
            self.run('Saving catalog source…', lambda: self.manager.set_source(source, self.launcher.package_lease),
                     saved, committing=True, discard_preview=True)
        elif event.node_id == 'marketplace-approve' and self.prepared:
            prepared = self.prepared
            identifier = prepared['entry']['id']
            update = identifier in self.records
            def commit():
                try:
                    return self.manager.install(prepared, update=update, lease=self.launcher.package_lease)
                finally:
                    self.manager.discard(prepared)
            self.quiesce(commit, lambda result: self.mutated(identifier, result))
        elif event.node_id == 'marketplace-uninstall':
            row = self.selected()
            if row:
                identifier = row['id']
                self.quiesce(lambda: self.manager.uninstall(identifier, self.launcher.package_lease),
                             lambda result: self.mutated(identifier, result))

    def run(self, message, work, success=None, committing=False, discard_preview=False):
        if self.busy:
            return
        previous = self.prepared if discard_preview else None
        if discard_preview:
            self.prepared = None
        self.busy, self.committing, self.success = True, committing, success
        self.cancelled.clear()
        self.status, self.error = message, ''
        self.publish()
        def execute():
            try:
                if previous is not None:
                    self.manager.discard(previous)
                result, error = work(), ''
            except Exception as exc:
                result, error = None, str(exc)[:512]
            self.finished.emit(result, error)
        self.job = self.pool.submit(execute)

    @Slot(object, str)
    def completed(self, result, error):
        self.job = None
        success, self.success = self.success, None
        if self.prepared is not None and not Path(self.prepared['directory']).exists():
            self.prepared = None
        if self.page == 'review' and self.prepared is None:
            self.page = 'list'
        self.busy = self.committing = False
        if self.quiescing:
            self.quiescing = self.launcher.plugin_runtime.suspended = False
        if error:
            self.launcher.plugin_runtime.suspended = False
            self.status = self.error = error
            self.launcher.show_toast(Toast('Plugin operation failed', error, 'error'))
        elif success is not None:
            success(result)
        if self.want_leave:
            callback = self.leave_callback
            if self.leave(callback):
                self.want_leave, self.leave_callback = False, None
                self.nodes = None
                if callback:
                    QTimer.singleShot(0, callback)
        self.publish()

    def list_page(self, message=''):
        self.page, self.source_draft, self.target = 'list', None, None
        if message:
            self.status = message
        self.publish()

    def show_installed(self):
        self.tab, self.page = 'Installed', 'list'
        def loaded(result):
            self.installed_rows, registry = result
            self.records, self.source_url = registry['installed'], registry['catalog_url']
            self.tab = 'Installed'
            message = ('Cleanup unconfirmed. Use Actions → Check cleanup / recover.' if self.launcher.plugin_runtime.blocked else
                       'Installed packages. Enter reviews a managed update; Actions includes enable, disable and uninstall.' if self.installed_rows else
                       'No external packages installed. Choose Configure to set a reviewed catalog source.')
            self.list_page(message)
        self.run('Reading installed metadata…', lambda: (self.manager.inventory(), self.manager.registry()), loaded)

    def show_browse(self):
        self.tab, self.page = 'Browse', 'list'
        def loaded(result):
            self.catalog, offline = result
            self.tab = 'Browse'
            self.list_page('Using cached catalog; refresh failed.' if offline else
                           'Select a plugin, then Enter to review its pinned package.' if self.catalog['plugins'] else
                           'This catalog is empty. Genuine reviewed Release assets must be published first.')
        self.run('Fetching reviewed catalog…', lambda: self.manager.catalog(refresh=True), loaded, discard_preview=True)

    def review(self):
        row = self.selected()
        if not row or self.busy:
            return
        identifier = row['id']
        def work():
            catalog, offline = self.manager.catalog(refresh=True)
            return catalog, self.manager.prepare(self.manager.release(identifier, catalog)), offline
        def previewed(result):
            self.catalog, self.prepared, offline = result
            self.page = 'review'
            self.status = ('Using cached catalog. ' if offline else '') + 'Verified, NOT installed. Read the manual and origin before approving.'
        self.run('Downloading and verifying for review (no code execution)…', work, previewed, discard_preview=True)

    def read_manual(self):
        row = self.selected()
        if row and self.tab == 'Installed':
            identifier = row['id']
            def loaded(text):
                self.manual_key, self.manual_text = identifier, text
                self.status = 'Installed manual. No plugin code was imported.'
            self.run('Reading manual…', lambda: read_guide(row['directory']), loaded)

    def uninstall(self):
        row = self.selected()
        if row and row.get('managed') and self.launcher.package_lease is not None:
            self.target = dict(row)
            self.page, self.error = 'uninstall', ''
            self.publish()

    def set_enabled(self, enabled):
        row = self.selected()
        if not row or self.busy or self.launcher.package_lease is None:
            return
        identifier = row['id']
        if not enabled:
            self.launcher.plugin_runtime.suspended = True
        def changed(message):
            for item in self.installed_rows:
                if item['id'] == identifier:
                    item.update(enabled=enabled, status='Enabled' if enabled else 'Disabled; cleanup pending')
            self.launcher.refresh_plugins(identifier)
            if enabled:
                self.launcher.show_toast(Toast('Plugin enabled', message[:512], 'success'))
                self.show_installed()
            else:
                self.quiesce(lambda: self.manager.recover(self.launcher.package_lease),
                             lambda _: self.mutated(identifier, {'message': 'Disabled; worker and host-service cleanup confirmed.', 'retained': []}))
        self.run('Saving enablement (no code activation)…', lambda: self.manager.set_enabled(identifier, enabled, self.launcher.package_lease),
                 changed, committing=True)

    def mutated(self, identifier, result):
        self.launcher.plugin_runtime.confirm_cleanup()
        self.launcher.refresh_plugins(identifier)
        message = result['message'] if isinstance(result, dict) else str(result)
        notes = '\n'.join(result.get('retained', [])) if isinstance(result, dict) else ''
        self.launcher.show_toast(Toast(message[:128], notes[:512], 'success'))
        self.status = message + ('\n' + notes if notes else '')
        self.page = 'list'
        if not self.want_leave:
            self.show_installed()

    def recover(self):
        self.quiesce(lambda: self.manager.recover(self.launcher.package_lease),
                     lambda _: self.mutated(None, {'message': 'Recovery complete; plugin units confirmed empty.', 'retained': []}))

    def quiesce(self, work, success):
        if self.busy or self.launcher.package_lease is None or not self.launcher.confirm_form_leave(lambda: self.quiesce(work, success)):
            return
        self.busy = self.quiescing = self.launcher.plugin_runtime.suspended = True
        self.committing = False
        self.cancelled.clear()
        self.cleanup_action = (work, success)
        self.cleanup_deadline = time.monotonic() + 8
        self.launcher.plugin_timer.stop()
        for token in self.launcher.plugin_tokens.values():
            token.set()
        self.launcher.plugin_calls.clear()
        self.launcher.wipe_form_passwords()
        self.status = 'Waiting for plugin and host-service cleanup…'
        self.publish()
        self.launcher.plugin_runtime.deactivate()
        self.cleanup_timer.start()
        self.wait_cleanup()

    def wait_cleanup(self):
        if self.cancelled.is_set() or time.monotonic() > self.cleanup_deadline:
            self.cleanup_timer.stop()
            self.busy = self.quiescing = self.launcher.plugin_runtime.suspended = False
            self.cleanup_action = None
            self.status = 'No payload commit. Saved disable flags remain disabled. Cleanup may still be pending.'
            if self.want_leave:
                callback = self.leave_callback
                if self.leave(callback) and callback:
                    self.want_leave, self.leave_callback = False, None
                    QTimer.singleShot(0, callback)
            self.publish()
        elif self.launcher.plugin_runtime.quiescent:
            self.cleanup_timer.stop()
            work, success = self.cleanup_action
            self.cleanup_action = None
            self.busy = False
            self.run('Committing package state (cannot cancel)…', work, success, committing=True)

    def leave(self, callback):
        if self.busy:
            self.want_leave, self.leave_callback = True, callback
            if not self.committing:
                self.cancelled.set()
            return False
        if self.prepared is not None:
            prepared, self.prepared = self.prepared, None
            self.want_leave, self.leave_callback = True, callback
            self.run('Discarding uninstalled preview…', lambda: self.manager.discard(prepared))
            return False
        self.nodes = None
        return True

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

    def back(self):
        callback = self.list_page if self.page != 'list' else self.launcher.pop_navigation
        if self.leave(callback):
            callback()
