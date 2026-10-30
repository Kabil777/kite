#!/usr/bin/env python3
"""Run with uv run python -m tests.check; fixture-only clipboard/password operations."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
# Never fall through to a real store between scoped fake-CLI patches.
os.environ['GOPASS_BIN'] = '/nonexistent/kite-test-gopass'
from main.core import APP_ROOT, DATA, Result, calculate, clipboard_type, load_config, math_like, provider, rank, score, suggestions
from . import check_ranking
from .plugin_sources import gopass_source as optional_gopass_source
from main.dmenu import options_from_bytes
from main.bundled_plugins.clipboard.backend import search as clipboard_search
from main.favorites import FavoritesStore
from main.plugin_api import (API_VERSION, Action, Catalog, Event, HostServices, Node, Result as PluginResult,
                             View, load_plugins, plugin_config, validate_view)

with tempfile.TemporaryDirectory() as directory:
    store_path = Path(directory) / 'state' / 'favorites.sqlite'
    favorites = FavoritesStore(store_path, ['app:first', 'app:second', 'app:first'])
    assert favorites.ids() == ['app:first', 'app:second']
    assert favorites.add('app:third') and not favorites.add('app:third')
    assert favorites.move('app:third', -1)
    assert favorites.ids() == ['app:first', 'app:third', 'app:second']
    assert favorites.remove('app:first') and favorites.ids() == ['app:third', 'app:second']
    assert FavoritesStore(store_path, ['ignored:seed']).ids() == ['app:third', 'app:second']
    favorites.remove('app:third')
    favorites.remove('app:second')
    assert FavoritesStore(store_path, ['must-not-reseed']).ids() == []

valid_plugin_view = View(
    nodes=(Node('column', 'root', children=(
        Node('label', 'title', {'text': 'Vault'}),
        Node('button', 'copy', {'text': 'Copy'}, events={'activate': {'action': 'copy'}}),
    )),),
    results=(PluginResult('vault:entry', 'Example login', action=Action('copy', 'not-a-real-secret')),
))
validate_view(valid_plugin_view)
assert plugin_config(HostServices({'plugins': {'sample': {'config': {'endpoint': 'local'}}}}),
                     'sample') == {'endpoint': 'local'}
assert plugin_config(HostServices({}), 'missing') == {}
assert [row.value for row in options_from_bytes(b'alpha\nbeta\n')] == ['alpha', 'beta']
assert [row.value for row in options_from_bytes('a\u2028b\n\n'.encode())] == ['a\u2028b', '']
for invalid_dmenu in [b'bad\x00line', b'\xff']:
    try:
        options_from_bytes(invalid_dmenu)
    except ValueError:
        pass
    else:
        raise AssertionError(invalid_dmenu)
for invalid_view in [View(nodes=(Node('button', 'duplicate'), Node('label', 'duplicate'))),
                     View(nodes=(Node('unknown', 'bad'),)),  # type: ignore[arg-type]
                     View(results=(PluginResult('', 'bad'),)),
                     View(nodes=(Node('label', 'bad', {'text': 123}),)),
                     View(results=(PluginResult('bad', 'Bad', action=Action('exec', [])),)),
                     View(catalog=Catalog(layout='unknown'))]:  # type: ignore[arg-type]
    try:
        validate_view(invalid_view)
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError(invalid_view)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory) / 'plugins'
    example = root / 'sample'
    example.mkdir(parents=True)
    (example / 'plugin.json').write_text(json.dumps({'id': 'sample', 'name': 'Sample', 'api_version': API_VERSION}))
    (example / '__init__.py').write_text('from plugin_api import View, Node\ndef search(query, host):\n    return View(nodes=(Node("label", "label", {"text": query}),))\n')
    loaded = load_plugins(root)
    assert len(loaded) == 1 and loaded[0][1].search('ok', HostServices({})).nodes[0].props['text'] == 'ok'
    disabled = Path(directory) / 'disabled'
    disabled.mkdir()
    (disabled / 'plugin.json').write_text(json.dumps({'id': 'disabled', 'name': 'Disabled', 'api_version': API_VERSION}))
    (disabled / '__init__.py').write_text('raise RuntimeError("disabled plugin was imported")')
    assert load_plugins(disabled.parent, config={'disabled': {'enabled': False}}) == []
    gopass_source = optional_gopass_source()
    gopass = root / 'gopass'
    gopass.mkdir()
    for filename in ('plugin.json', '__init__.py', 'backend.py'):
        (gopass / filename).write_bytes((gopass_source / filename).read_bytes())
    (gopass / 'plugin.json').write_text(json.dumps({'id': 'gopass', 'name': 'Passwords', 'api_version': API_VERSION}))
    fake = Path(directory) / 'fake-gopass'
    fake.write_text('#!/bin/sh\n[ "$1" = ls ] && { printf "mail\\nwork\\n"; exit; }\n'
                    '[ "$1 $2" = "show --password" ] && { printf "test-secret\\n"; exit; }\nexit 2\n')
    fake.chmod(0o700)
    with patch.dict(os.environ, {'GOPASS_BIN': str(fake)}):
        plugin = dict((manifest['id'], module) for manifest, module in load_plugins(root))['gopass']
        host = HostServices({'gopass': {'store': ''}})
        found = plugin.search('mail', host)
        assert found.nodes == () and [item.title for item in found.results] == ['mail']
        entry_id = found.results[0].id
        assert entry_id.startswith('entry:') and found.results[0].action.value == 'mail'
        assert found.results[0].glyph == '\uf084'
        secret = plugin.handle_event(Event(entry_id, 'activate', {'value': 'mail'}), host)
        validate_view(secret)
        assert secret.nodes == (), 'Password plugin must not render a preview panel'
        assert secret.results[0].title == 'Copy password' and secret.results[0].action.value == 'test-secret'
        assert secret.results[0].glyph == '\uf0c5'
        form = plugin.handle_event(Event('create', 'activate'), host)
        validate_view(form)
        password_field = next(node for node in form.nodes[0].children if node.id == 'password')
        assert password_field.props['password'] is True and 'text' not in password_field.props
    fake_decrypt = Path(directory) / 'fake-decrypt-gopass'
    fake_decrypt.write_text('#!/bin/sh\nexit 11\n')
    fake_decrypt.chmod(0o700)
    with patch.dict(os.environ, {'GOPASS_BIN': str(fake_decrypt)}):
        failed = plugin.handle_event(Event(entry_id, 'activate', {'value': 'mail'}), host)
        assert 'decryption failed' in failed.error
    created, captured, args_log = (Path(directory) / name for name in ('created', 'stdin', 'args'))
    fake_create = Path(directory) / 'fake-create-gopass'
    fake_create.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$GOPASS_ARGS"\n'
        'case "$1" in\n'
        'ls) printf "old/login\\n"; [ ! -f "$GOPASS_CREATED" ] || sed "s#^work/##" "$GOPASS_CREATED"; exit 0 ;;\n'
        'insert) printf "%s" "$2" > "$GOPASS_CREATED"; cat > "$GOPASS_INPUT"; exit 0 ;;\n'
        'esac\nexit 2\n')
    fake_create.chmod(0o700)
    create_host = HostServices({'gopass': {'store': ''}, 'plugins': {
        'gopass': {'enabled': True, 'config': {'store': 'work'}}}})
    with patch.dict(os.environ, {'GOPASS_BIN': str(fake_create), 'GOPASS_CREATED': str(created),
                                 'GOPASS_INPUT': str(captured), 'GOPASS_ARGS': str(args_log)}):
        saved = plugin.handle_event(Event('save', 'activate', {'inputs': {
            'entry': 'new/login', 'password': 'fixture-only-secret'}}), create_host)
    validate_view(saved)
    assert {item.title for item in saved.results} == {'old', 'new'}
    assert all(item.tag == 'Vault' for item in saved.results)
    assert captured.read_text() == 'fixture-only-secret'
    logged_args = args_log.read_text()
    assert 'work/new/login' in logged_args and '--strip-prefix\nwork\n' in logged_args
    assert 'fixture-only-secret' not in logged_args
    with patch.dict(os.environ, {'GOPASS_BIN': str(fake_create), 'GOPASS_CREATED': str(created),
                                 'GOPASS_INPUT': str(captured), 'GOPASS_ARGS': str(args_log)}):
        try:
            plugin.create_entry('old/login', 'another-fixture', 'work')
        except plugin.BackendError:
            pass
        else:
            raise AssertionError('Existing password entry was overwritten')
    assert captured.read_text() == 'fixture-only-secret'
    with patch.dict(os.environ, {'GOPASS_BIN': str(Path(directory) / 'missing-gopass')}):

        missing = plugin.search('', HostServices({'gopass': {'store': ''}}))
        assert 'gopass executable not found' in missing.error
    bad = root / 'bad'
    bad.mkdir()
    (bad / 'plugin.json').write_text(json.dumps({'id': 'bad', 'name': 'Bad', 'api_version': API_VERSION + 1}))
    failures = []
    assert len(load_plugins(root, on_error=lambda name, message: failures.append((name, message)))) == 2
    assert failures[0][0] == 'bad'

for text, expected in [('2+3*4', '14'), ('(2+3)^2', '25'), ('sqrt(81)', '9'),
                       ('=1/4', '1/4'), ('2^-3', '1/8'), ('sin(pi/2)', '1'),
                       ('solve(x^2-4,x)', '[-2, 2]'), ('diff(x^3,x)', '3*x**2'),
                       ('integrate(2*x,x)', 'x**2'), ('20% of 150', '30'),
                       ('x^2=4', '[-2, 2]'), ('round(pi,2)*100', '314'), ('log10(1000)', '3')]:
    assert calculate(text)[0] == expected, text
for text in ["__import__('os').system('false')", '(1).__class__', '[1]',
             'open(1)', 'True', '1/0', '2^10000', '9^9^9', 'factorial(10000)', '1e999']:
    try:
        calculate(text)
    except (ValueError, SyntaxError, ArithmeticError, TypeError):
        pass
    else:
        raise AssertionError(text)
assert math_like('solve(x^2-4,x)') and not math_like('firefox')
assert score('cafe', 'Café') == 1000
rows = [Result('exact', 'Code'), Result('prefix', 'Code editor'), Result('fuzzy', 'Cold tea')]
assert rank(rows, 'code', {'prefix': {'count': 100000, 'last': time.time()}})[0].id == 'exact'
assert score('zzzzz', 'Firefox') == 0
assert suggestions(['q', ['one', 'two', 'one', 2]]) == ['one', 'two']
for invalid in ({}, ['q', 'not a list']):
    try:
        suggestions(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError(invalid)
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'config.yaml'
    for invalid in ['window:\n  width: false', 'window:\n  width: 3', 'window:\n  typo: 4',
                    'colors:\n  text: red', '[]', 'extensions:\n  - command: "echo hello"',
                    'ranking:\n  sensitivity: impossible', 'results:\n  layout: bad',
                    'aliases:\n  - {id: app:x}', 'favorites: [4]',
                    'aliases:\n  - {id: app:x, alias: xx}\n  - {id: app:y, alias: XX}',
                    'web:\n  suggestion_font_size: 100', 'emoji:\n  columns: 0',
                    'gopass:\n  store: ../unsafe', 'gopass:\n  store: /absolute',
                    'plugins: []', 'plugins:\n  gopass:\n    enabled: "yes"',
                    'plugins:\n  gopass:\n    config: []']:
        path.write_text(invalid)
        try:
            load_config(path)
        except ValueError:
            pass
        else:
            raise AssertionError(invalid)
    path.write_text('window:\n  width: 840\nicons:\n  size: 40\nresults:\n  row_height: 48\n'
                    'web:\n  suggestion_font_size: 19\nemoji:\n  columns: 6\n  font_size: 36\n'
                    'plugins:\n  gopass:\n    enabled: true\n    config:\n      store: work')
    parsed = load_config(path)
    assert parsed['window']['width'] == 840 and parsed['web']['suggestion_font_size'] == 19
    assert parsed['emoji'] == {'columns': 6, 'font_size': 36}
    assert parsed['gopass']['store'] == '' and parsed['plugins']['gopass']['config']['store'] == 'work'
print('Core OK: YAML validation, ranking, symbolic math, unsafe input rejection, suggestions')

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QTextBrowser
from main.launcher import Jobs, Launcher, ResultDelegate

app = QApplication([])
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QTextCursor
from main.widgets import hex_color
assert hex_color('#FF000080').getRgb() == (255, 0, 0, 128)
for value, expected in [('https://raycast.com', 'Link'), ('www.example.com', 'Link'), ('#FF6363', 'Color'),
                        ('#abc', 'Color'), ('#12345', 'Text'), ('/tmp/demo.png', 'File'), ('hello', 'Text'),
                        ('[[ binary data 1 KiB png 32x24 ]]', 'Image')]:
    assert clipboard_type(value) == expected, value
image_item = clipboard_search('', ['42\t[[ binary data 1 KiB png 32x24 ]]'])[0]
assert image_item.icon == '' and image_item.subtitle == 'Image' and image_item.glyph == '\uf1c5'
image = QImage(32, 24, QImage.Format.Format_ARGB32)
image.fill(Qt.GlobalColor.red)
buffer = QBuffer()
buffer.open(QIODevice.OpenModeFlag.WriteOnly)
assert image.save(buffer, 'PNG')  # pyright: ignore[reportCallIssue, reportArgumentType]
image_bytes = bytes(buffer.data().data())
with patch('main.core.command') as mocked:
    mocked.return_value = subprocess.CompletedProcess([], 0, stdout=image_bytes)
    preview = provider('clip-preview', value='1')
    assert isinstance(preview, dict) and preview['type'] == 'Image' and preview['dimensions'] == '32 × 24'
    assert not QImage.fromData(base64.b64decode(preview['preview'])).isNull()
    mocked.return_value.stdout = b'#FF6363'
    preview = provider('clip-preview', value='2')
    assert isinstance(preview, dict) and preview['type'] == 'Color'
    mocked.return_value.stdout = b'<b>literal text</b>'
    preview = provider('clip-preview', value='3')
    assert isinstance(preview, dict) and preview['preview'] == '<b>literal text</b>'
    mocked.return_value.stdout = b'\xff\x00'
    preview = provider('clip-preview', value='4')
    assert isinstance(preview, dict) and preview['type'] == 'Binary'
    provider('copy', value='test-secret', copy_command=['wl-copy'])
    mocked.assert_called_with(['wl-copy'], input=b'test-secret', capture_output=False,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
test_plugin_temp = tempfile.TemporaryDirectory()
test_plugin_root = Path(test_plugin_temp.name)
os.environ['XDG_CACHE_HOME'] = str(test_plugin_root / 'cache')
test_plugin = test_plugin_root / 'sample'
test_plugin.mkdir()
(test_plugin / 'plugin.json').write_text(json.dumps({'id': 'sample', 'name': 'Sample plugin', 'api_version': API_VERSION}))
(test_plugin / '__init__.py').write_text(
    '''import time
from plugin_api import Action, Catalog, Node, Result, View

def search(query, host):
    if query in ('old', 'new'):
        host.config['search']['font_size'] = 99
        time.sleep(.15 if query == 'old' else .01)
        return View(nodes=(Node('label', 'generation', {'text': query}),))
    return View(nodes=(Node('column', 'root', children=(
        Node('label', 'label', {'text': query or 'ready'}),
        Node('text_input', 'field', {'text': ''}),
        Node('button', 'button', {'text': 'Update'}, events={'activate': {}}),
    )),), results=(Result('sample:copy', 'Copy sample', action=Action('copy', 'sample')),),
        catalog=Catalog('Plugin search…', 'Sample plugin', heading='Results'))

def handle_event(event, host):
    assert event.payload['query'] == 'query'
    assert event.payload['inputs']['field'] == 'form-value'
    return View(nodes=(Node('label', 'label', {'text': 'updated'}),))
''')
test_gopass = test_plugin_root / 'gopass'
test_gopass.mkdir()
for filename in ('plugin.json', '__init__.py', 'backend.py', 'README.md', 'HOWTO.md'):
    (test_gopass / filename).write_bytes((optional_gopass_source() / filename).read_bytes())
with (test_gopass / 'HOWTO.md').open('a') as guide:
    guide.write('''

> [!TIP]
> Test-only callout with **strong text**.
>
> Another paragraph stays in this callout.

### Smaller heading

```sh
[!NOTE] is literal code, not a callout.
second code line
```

> [!WARNING]
> Test warning.
''')
config = load_config(DATA / 'defaults.yaml')  # Deterministic checks; preserve personal appearance overrides.
with patch('main.plugin_api.plugin_root', return_value=test_plugin_root), \
        patch('main.plugin_api.user_plugin_root', return_value=test_plugin_root / 'no-user-packages'), \
        patch('main.applications.Applications.start', lambda self: None):
    favorites_path = Path(test_plugin_temp.name) / 'favorites.sqlite'
    window = Launcher(config, favorites_path=favorites_path)
assert not any(shortcut.key().toString() == 'Alt+I' for shortcut in window.shortcuts)
assert next(row for row in window.commands if row.id == 'plugin:gopass').glyph == '\uf084'
assert window.is_busy() and window.apps_loading
assert window.empty_hint.text() == 'Loading applications…' and not window.rows
window.show()

def until(check, seconds=7):
    deadline = time.monotonic() + seconds
    while not check() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert check(), 'Timed out waiting for UI state'

until(lambda: len(window.apps) > 0)
assert not window.apps_loading and not window.is_busy() and not window.loading_bar.isVisible()
assert any(row.kind == 'app' for row in window.rows)
assert any(row.kind == 'plugin' and row.value == 'sample' for row in window.commands)
root_sections = [window.list.item(i).text() for i in range(window.list.count())
                 if window.list.item(i).data(Qt.ItemDataRole.UserRole + 2)]
assert root_sections == ['Plugins', 'Applications']
for i in range(window.list.count()):
    if window.list.item(i).data(Qt.ItemDataRole.UserRole) == 'plugin:gopass':
        window.list.setCurrentRow(i)
        break
window.show_actions()
assert not any(action.text() == 'Create Password' for action in window.action_menu.actions())
assert any(action.text() == 'How to' for action in window.action_menu.actions())
window.action_menu.search.setText('How to')
launcher_size = window.size()
QTest.keyClick(window.action_menu.search, Qt.Key.Key_Return)
assert window.plugin_help_panel.isVisible() and window.plugin_help_title.text() == 'KITE'
assert 'Open an app' in window.plugin_help_browser.toPlainText()
window.back()
# Plugin manuals are contextual; constructing the menu does not access a store.
window.set_mode('plugin:gopass')
window.plugin_timer.stop()
window.update_plugin_view('gopass', View(results=(PluginResult('fixture', 'Fixture'),),
                                        actions=plugin._menu_actions(HostServices(config))))
window.show_actions()
create_action = next(action for action in window.action_menu.actions() if action.text() == 'Create Password')
assert create_action.property('keys') == 'Alt+I'
window.action_menu.search.setText('How to')
QTest.keyClick(window.action_menu.search, Qt.Key.Key_Return)
assert window.plugin_help_panel.isVisible()
assert window.plugin_help_panel.parentWidget() is window.panel
assert window.plugin_help_title.text() == 'Passwords'
assert window.plugin_help_panel.findChild(QPushButton, 'pluginHelpClose').text() == 'How to'
guide_inset = max(4, window.cfg['window']['padding'], window.cfg['window']['radius'])
assert window.plugin_help_panel.width() == window.panel.width() - 2 * guide_inset
assert window.plugin_help_panel.x() == window.plugin_help_panel.y() == guide_inset
assert window.plugin_help_panel.geometry().bottom() < window.footerbar.y()
assert window.footerbar.isVisible() and not window.primary_button.isEnabled()
assert window.size() == launcher_size and not window.isFullScreen()
app.processEvents()
browser = window.plugin_help_browser
assert browser.horizontalScrollBar().maximum() == 0
for frame in browser.document().rootFrame().childFrames():
    rect = browser.document().documentLayout().frameBoundingRect(frame)
    assert rect.left() >= 0 and rect.right() <= browser.viewport().width()
assert browser.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
assert browser.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
block = browser.document().begin()
while block.isValid():
    fragment = block.begin()
    while not fragment.atEnd():
        assert fragment.fragment().charFormat().fontFamilies() in ([browser.BODY_FONT], [browser.CODE_FONT])
        fragment += 1
    block = block.next()
assert browser.toPlainText().startswith('Start here\n') and 'Copy a password' in browser.toPlainText()
assert '<h1' not in browser.document().toHtml().casefold()
assert browser.verticalScrollBar().value() == 0
callout = browser.document().begin()
while callout.isValid() and callout.text() != 'Tip':
    callout = callout.next()
assert callout.isValid()
callout_frame = QTextCursor(callout).currentFrame()
assert callout_frame.cellAt(QTextCursor(callout)).format().toTableCellFormat().topPadding() == 16
assert callout_frame.frameFormat().border() == 0
body = callout.next()
assert body.text() == 'Test-only callout with strong text.'
assert body.begin().fragment().charFormat().fontWeight() < callout.begin().fragment().charFormat().fontWeight()
assert QTextCursor(body.next()).currentFrame() is callout_frame
code_block = browser.document().begin()
while code_block.isValid() and code_block.text() != 'gopass git remote -v':
    code_block = code_block.next()
assert code_block.isValid()
code_frame = QTextCursor(code_block).currentFrame()
assert code_frame.frameFormat().background().color().name() == browser.SURFACE
assert code_frame.frameFormat().padding() == 16
assert '[!NOTE] is literal code, not a callout.\nsecond code line' in browser.toPlainText()
heading_sizes = {}
block = browser.document().begin()
while block.isValid():
    if block.blockFormat().headingLevel():
        heading_sizes[block.blockFormat().headingLevel()] = block.begin().fragment().charFormat().fontPointSize()
    block = block.next()
assert heading_sizes[2] > heading_sizes[3] > body.begin().fragment().charFormat().fontPointSize()
window.back()
assert not window.plugin_help_panel.isVisible() and not window.plugin_detail_panel.isVisible()
assert window.primary_button.isEnabled() and window.actions_button.isEnabled()
with patch.dict(os.environ, {'GOPASS_BIN': str(Path(test_plugin_temp.name) / 'no-gopass')}):
    create_action.trigger()
    until(lambda: window.mode == 'plugin:gopass' and 'gopass' in window.plugin_views
          and bool(window.plugin_views['gopass'].nodes)
          and any(node.id == 'password' for node in window.plugin_views['gopass'].nodes[0].children))
window.set_mode('plugin:sample')
window.search.setText('query')
until(lambda: window.plugin_views.get('sample') is not None and window.plugin_views['sample'].nodes)
assert window.results_heading.text() == 'Results · 1'
assert window.plugin_detail_panel.isVisible()
assert window.footer.text() == 'Sample plugin'  # Catalog-provided footer remains authoritative.
assert window.search.placeholderText() == 'Plugin search…'
field = window.plugin_detail_panel.findChild(QLineEdit, 'field')
assert field is not None
field.setText('form-value')
button = window.plugin_detail_panel.findChild(QPushButton, 'button')
assert button is not None
button.click()
until(lambda: window.plugin_views['sample'].nodes[0].props.get('text') == 'updated')
window.update_plugin_view('sample', View(nodes=(Node('text_input', 'secret', {'password': True}),)))
secret_field = window.plugin_detail_panel.findChild(QLineEdit, 'secret')
assert secret_field is not None and secret_field.echoMode() == QLineEdit.EchoMode.Password
window.search.setText('old')
until(lambda: 'sample' in window.plugin_calls)
window.search.setText('new')
until(lambda: window.plugin_views.get('sample') is not None
      and window.plugin_views['sample'].nodes[0].props.get('text') == 'new')
QTest.qWait(220)
assert window.plugin_views['sample'].nodes[0].props['text'] == 'new', 'Stale event replaced newer event'
window.update_plugin_view('sample', View(results=(PluginResult('plain', 'Plain result'),)))
assert not window.plugin_detail_panel.isVisible(), 'Views without nodes must have no preview panel'
assert window.results_heading.text() == 'Results · 1'
assert config['search']['font_size'] == 20, 'Plugin mutated global YAML theme'
from main.host_services import context as host_context
assert host_context(config).call('math.calculate', {'query': '2*3'})[0]['title'] == '6'
fake_bin = Path(test_plugin_temp.name) / 'fake-bin'
fake_bin.mkdir()
clipboard_capture = Path(test_plugin_temp.name) / 'copied.txt'
fake_wl_copy = fake_bin / 'wl-copy'
fake_wl_copy.write_text('#!/usr/bin/python3\nimport os,sys\nopen(os.environ["FAKE_CLIPBOARD"],"wb").write(sys.stdin.buffer.read())\n')
fake_wl_copy.chmod(0o700)
window.set_mode('plugin:sample')
window.plugin_timer.stop()  # This fixture injects its view; do not race a scheduled search.
window.update_plugin_view('sample', secret)
assert window.footer.text() == secret.catalog.footer
assert window.results_heading.text() == f'{secret.catalog.heading} · {len(secret.results)}'
assert {item.title for item in secret.results} == {'Copy password', 'Copy path'}
window.list.setCurrentRow(0)
with patch.dict(os.environ, {'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'],
                             'FAKE_CLIPBOARD': str(clipboard_capture)}), patch.object(window.jobs, 'copy_command', [str(fake_wl_copy)]):
    window.activate()
    until(clipboard_capture.exists)
    until(lambda: 'action' not in window.jobs.running)
assert clipboard_capture.read_bytes() == b'test-secret', 'Gopass plugin copy action failed end-to-end'
assert window.isVisible() and window.mode == 'plugin:sample' and window.toast.text() == 'Copied to clipboard', (window.isVisible(), window.mode, window.toast.text())
window.set_mode('root')
assert window.search.font().family() == 'Adwaita Sans'
assert window.search.placeholderText() == 'Search for apps and commands…'
assert window.search.font().pixelSize() == config['search']['font_size'] == 20
assert config['window']['width'] == 750 and config['window']['height'] == 520
assert config['results']['row_height'] == 48 and config['icons']['size'] == 28
assert config['search']['height'] == 72 and config['footer']['height'] == 46
assert config['results']['gap'] == 0 and window.list.viewportMargins().top() == 4
assert all(row.glyph for row in window.commands if row.kind != 'plugin')
from main.widgets import ICON_FONT, pixel_font
from PySide6.QtGui import QFontMetrics
assert window.lead.text() == '\uf002' and not hasattr(window, 'context_icon')
assert config['results']['font_family'] == 'JetBrainsMono Nerd Font'
assert all(QFontMetrics(pixel_font(ICON_FONT, 19)).inFontUcs4(ord(row.glyph))
           for row in window.commands if row.kind != 'plugin')
assert window.list.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
from PySide6.QtGui import QFontInfo, QIcon
assert not QFontInfo(window.search.font()).fixedPitch()
assert QIcon.themeName() == 'WhiteSur-dark'
for name in ['firefox', 'face-smile', 'edit-paste', 'accessories-calculator', 'system-file-manager',
             'web-browser', 'text-html', 'starred', 'emblem-favorite']:
    assert not QIcon.fromTheme(name).pixmap(36, 36).isNull(), name
assert not QIcon.fromTheme('folder').isNull(), 'Configured icon theme failed to load'
assert window.list.iconSize().width() == config['icons']['size']
saved_favorites, saved_aliases = list(config['favorites']), config['aliases']
assert window.favorites_store is not None
window.favorites_store.add('cmd:clipboard')
config['favorites'] = window.favorites_store.ids()
config['aliases'] = [{'id': 'cmd:clipboard', 'alias': 'cb'}]
window.render()
root_sections = [window.list.item(i).text() for i in range(window.list.count())
                 if window.list.item(i).data(Qt.ItemDataRole.UserRole + 2)]
assert root_sections == ['Favorites', 'Plugins', 'Applications']
window.list.setCurrentRow(1)  # Favorites header is not selectable.
assert (selected := window.selected_result()) is not None and selected.id == 'cmd:clipboard'
window.move_selection(1)
assert window.list.currentItem().flags() & Qt.ItemFlag.ItemIsSelectable
assert (selected := window.selected_result()) is not None and selected.id != 'cmd:clipboard'
window.search.setText('cb')
assert window.rows[0].id == 'cmd:clipboard'
assert (selected := window.selected_result()) is not None and selected.id == 'cmd:clipboard'
window.favorites_store.add('cmd:math')
config['favorites'] = window.favorites_store.ids()
window.render()
window.show_actions()
app.processEvents()
action_names = [action.text() for action in window.action_menu.actions() if not action.isSeparator()]
assert {'Copy Result ID', 'Remove from Favorites', 'Move Favorite Down'} <= set(action_names)
assert not window.action_menu.isWindow(), 'Actions must be an inset panel, not a compositor popup'
assert window.action_menu.geometry().bottom() < window.footerbar.y()
assert window.action_menu.width() == 340
assert window.action_menu.list.item(0).sizeHint().height() == 38
primary_layout, actions_layout = window.primary_button.layout(), window.actions_button.layout()
assert primary_layout is not None and window.primary_button.width() >= primary_layout.minimumSize().width()
assert actions_layout is not None and window.actions_button.width() >= actions_layout.minimumSize().width()
assert window.primary_button.geometry().right() < window.actions_button.geometry().left()
for button in (window.primary_button, window.actions_button):
    for cap in button.findChildren(QLabel, 'keycap'):
        assert cap.width() >= cap.fontMetrics().horizontalAdvance(cap.text()) + 12, cap.text()
window.action_menu.search.setText('Move Favorite Down')
QTest.keyClick(window.action_menu.search, Qt.Key.Key_Return)
assert window.favorites_store.ids() == ['cmd:math', 'cmd:clipboard']
window.show_actions()
assert any(action.text() == 'Move Favorite Up' for action in window.action_menu.actions())
window.action_menu.search.setText('Remove from Favorites')
QTest.keyClick(window.action_menu.search, Qt.Key.Key_Return)
assert 'cmd:clipboard' not in window.favorites_store.ids()
assert 'cmd:clipboard' not in config['favorites']
window.show_actions()
assert any(action.text() == 'Add to Favorites' for action in window.action_menu.actions())
window.action_menu.search.setText('Add to Favorites')
QTest.keyClick(window.action_menu.search, Qt.Key.Key_Return)
assert 'cmd:clipboard' in window.favorites_store.ids()
window.show_actions()
assert any(action.text() == 'Move Favorite Up' for action in window.action_menu.actions())
window.action_menu.search.setText('settings')
assert window.action_menu.list.count() == 1
assert window.action_menu.list.item(0).text() == 'Edit YAML Settings'
window.action_menu.search.setText('no matching action')
assert not window.action_menu.list.item(0).flags() & Qt.ItemFlag.ItemIsSelectable
window.back()
assert not window.action_menu.isVisible() and window.mode == 'root'
window.show_actions()
QTest.mouseClick(window.search, Qt.MouseButton.LeftButton)
assert not window.action_menu.isVisible(), 'Outside click must dismiss Actions'
window.favorites_store.remove('cmd:clipboard')
window.favorites_store.remove('cmd:math')
config['favorites'], config['aliases'] = saved_favorites, saved_aliases
window.search.setText('2+2')
until(lambda: bool(window.extra.get('math')))
assert window.rows[0].title == '4' and window.rows[0].expression == '2+2'
assert window.list.currentItem().sizeHint().height() >= 118
window.show_actions()
original_builtin = window.jobs.builtin
requests = []
window.jobs.builtin = lambda key, request, callback, timeout=2500: requests.append(request)
window.action_menu.search.setText('question')
QTest.keyClick(window.action_menu.search, Qt.Key.Key_Return)
assert not window.action_menu.isVisible()
assert requests[-1] == {'kind': 'copy', 'value': '2+2 = 4'}
window.jobs.builtin = original_builtin
window.action_menu.close()
window.search.setText('3+3')
assert not window.extra.get('math'), 'Old answers must disappear immediately'
until(lambda: bool(window.extra.get('math')))
assert window.rows[0].title == '6'
window.move_selection(1)
assert (selected := window.selected_result()) is not None and selected.id == 'web:raw'
window.render()
assert (selected := window.selected_result()) is not None and selected.id == 'web:raw', 'Async render stole deliberate selection'
window.set_mode('emoji')
assert len(window.mode_rows) > 3000
assert window.list.viewMode() == window.list.ViewMode.IconMode
assert window.results_heading.isVisible() and window.results_heading.text() == 'Results'
window.list.doItemsLayout()
columns = window.emoji_columns
assert columns == config['emoji']['columns']
assert window.list.visualItemRect(window.list.item(columns)).y() > window.list.visualItemRect(window.list.item(0)).y()
QTest.keyClick(window.search, Qt.Key.Key_Right)
assert window.list.currentRow() == 1
QTest.keyClick(window.search, Qt.Key.Key_Down)
assert window.list.currentRow() == columns + 1
QTest.keyClick(window.search, Qt.Key.Key_Left)
QTest.keyClick(window.search, Qt.Key.Key_Up)
assert window.list.currentRow() == 0
window.search.setText('family')
assert any('\u200d' in row.value for row in window.rows)
original_builtin = window.jobs.builtin
copies = []
window.jobs.builtin = lambda key, request, callback, timeout=2500: copies.append((request, callback))
selected_emoji = window.selected_result()
assert selected_emoji is not None
window.activate()
assert copies[-1][0] == {'kind': 'copy', 'value': selected_emoji.value}
copies[-1][1]({})
assert window.isVisible() and window.mode == 'emoji' and window.toast.isVisible()
assert window.toast.text() == 'Copied to clipboard'
window.jobs.builtin = original_builtin
window.search.setText('no emoji matches this random text 98765')
assert window.empty_hint.isVisible() and window.empty_hint.text() == 'No matching results'
# Typing while a local index loads must not invalidate that mode's result set.
original_builtin = window.jobs.builtin
pending = []
window.jobs.builtin = lambda key, request, callback, timeout=2500: pending.append(callback)
window.set_mode('clipboard')
assert window.is_busy() and window.loading_bar.isVisible()
assert window.empty_hint.isVisible() and window.empty_hint.text() == 'Loading…'
window.search.setText('hello')
assert window.empty_hint.isVisible() and window.empty_hint.text() == 'Loading…', 'Typing during load displayed an empty-results error'
pending[-1]({'results': [Result('clip:test', 'hello clipboard', 'Link', 'text-html', 'clip', '1').json()]})
assert not window.list.item(1).icon().pixmap(20, 20).isNull(), 'Clipboard link theme icon must render'
assert window.clip_filter.isVisible() and window.clip_detail_panel.isVisible()
assert window.clip_filter.height() >= window.clip_filter.label.fontMetrics().height() + 14
window.clip_filter.showPopup()
assert window.clip_filter.popup.isVisible()
window.back()
assert window.mode == 'clipboard' and not window.clip_filter.popup.isVisible()
assert any(row.title == 'hello clipboard' for row in window.rows)
assert window.clip_matches(Result('clip:image', '[[ binary data 118 KiB png 768x506 ]]'))
window.clip_filter.setCurrentText('Images Only')
assert not window.rows and window.empty_hint.text() == 'No matching results'
window.clip_filter.setCurrentText('All Types')
window.clip_preview_timer.stop()
window.request_clip_preview()
assert pending[-1] is not None
pending[-1]({'results': {'type': 'Text', 'size': 15, 'preview': 'hello clipboard'}})
assert window.clip_text.toPlainText() == 'hello clipboard' and window.clip_type.text() == 'Information'
assert window.clip_stack.currentWidget() is window.clip_text
window.search.clear()
window.mode_rows = [Result('clip:color', '#FF6363', 'Color', kind='clip', value='2'),
                    Result('clip:image', 'Image (32 × 24)', 'Image', '', 'clip', '3', glyph='\uf1c5')]
window.render()
window.clip_preview_timer.stop()
before_click = len(pending)
QTest.mouseClick(window.list.viewport(), Qt.MouseButton.LeftButton,
                 pos=window.list.visualItemRect(window.list.currentItem()).center())
assert len(pending) == before_click, 'Single click must select, not copy/close clipboard history'
window.request_clip_preview()
old_preview = pending[-1]
old_preview({'results': {'type': 'Color', 'size': 7, 'preview': '#FF6363'}})
assert not window.clip_preview.pixmap().isNull() and window.clip_detail_values['Content Type'].text() == 'Color'
window.clip_filter.setCurrentText('Images Only')
assert len(window.rows) == 1 and window.rows[0].id == 'clip:image'
assert window.list.item(1).icon().isNull(), 'Clipboard glyph should replace generic theme icon'
old_preview({'results': {'type': 'Color', 'size': 7, 'preview': '#FF6363'}})
assert not window.clip_type.text(), 'Stale color preview replaced a newer image selection'
window.clip_preview_timer.stop()
window.request_clip_preview()
pending[-1]({'results': {'type': 'Image', 'size': len(image_bytes), 'dimensions': '32 × 24',
                          'preview': base64.b64encode(image_bytes).decode()}})
assert not window.clip_preview.pixmap().isNull() and window.clip_detail_values['Dimensions'].text() == '32 × 24'
assert window.clip_preview.pixmap().width() <= window.clip_preview.width() * .8
assert window.clip_preview.pixmap().height() <= window.clip_preview.height() * .8
window.clip_filter.setCurrentText('All Types')
assert not window.is_busy() and not window.loading_bar.isVisible()
window.set_mode('windows')
pending[-1]({'error': 'Test provider unavailable'})
assert not window.is_busy() and window.empty_hint.isVisible() and window.empty_hint.text() == 'Could not load results'
window.set_mode('files')
stale_mode = pending[-1]
window.set_mode('emoji')
stale_mode({'results': []})
assert window.mode == 'emoji' and not window.is_busy()
window.jobs.builtin = original_builtin
window.set_mode('calc')
assert window.empty_hint.isVisible() and window.empty_hint.text() == 'Type a calculation to begin' and not window.is_busy()
window.set_mode('web')
assert window.results_heading.isVisible() and window.results_heading.text() == 'Results · 0'
window.search.setText('python')
window.web_timer.stop()  # Offline deterministic tests; live endpoint checked separately.
revision = window.revision
window.web_received('python', revision, ['python docs', 'python tutorial'])
assert any(row.title == 'python docs' for row in window.rows)
assert window.results_heading.text() == 'Results · 3'
font_size = config['web']['suggestion_font_size']
config['web']['suggestion_font_size'] = 21
window.render()
delegate = window.list.itemDelegate()
assert isinstance(delegate, ResultDelegate)
web_row = next(row for row in window.rows if row.id.startswith('web:suggestion:'))
assert delegate.title_font(web_row).pixelSize() == 21
assert delegate.title_font(window.rows[0]).pixelSize() == config['results']['font_size']
config['web']['suggestion_font_size'] = font_size
window.list.setCurrentRow(window.list.count() - 1)
QTest.keyClick(window.search, Qt.Key.Key_Tab)
assert window.search.text() == 'python tutorial'
window.search.setText('rust')
window.web_timer.stop()
window.web_received('python', revision, ['python stale'])
assert not any(row.title == 'python stale' for row in window.rows)
window.set_mode('root')
window.search.setText('private app name')
assert window.web_query() == ''
assert any(row.kind == 'web' for row in window.rows), 'Offline root web fallback missing'
assert any(row.id == 'web:ddg' and 'duckduckgo.com' in row.value for row in window.rows)
window.search.setText('? python')
window.web_timer.stop()
assert window.web_query() == 'python'
assert window.mode == 'web' and window.search.text() == 'python', 'Web marker leaked into search field'
assert window.footer.text() == 'Live Google suggestions · Enter opens selected result'
window.set_mode('root')
window.search.setText('=2+2')
assert window.mode == 'calc' and window.search.text() == '2+2', 'Math marker leaked into search field'
window.set_mode('root')
window.search.setText('marker free web completion')
for i in range(window.list.count()):
    if window.list.item(i).data(Qt.ItemDataRole.UserRole) == 'web:raw':
        window.list.setCurrentRow(i)
        break
QTest.keyClick(window.search, Qt.Key.Key_Tab)
assert window.mode == 'web' and window.search.text() == 'marker free web completion'
window.set_mode('root')
with tempfile.TemporaryDirectory() as directory:
    extension = Path(directory) / 'provider.py'
    extension.write_text('import sys,json\nq=json.load(sys.stdin)["query"]\n'
                         'print(json.dumps({"results":[{"title":q,"action":{"type":"copy","value":q}}]}))\n')
    config['extensions'] = [{'name': 'Test', 'prefix': 'test ', 'command': [sys.executable, str(extension)]}]
    window.search.setText('test hello')
    until(lambda: bool(window.extra.get('extension:Test')))
    assert any(row.title == 'hello' and row.kind == 'copy' for row in window.rows)
    config['extensions'] = []
window.set_mode('root')
window.grab().save(str(Path(test_plugin_temp.name) / 'preview-qt.png'))

# History tests use a temporary state file; never touch actual launch history.
import main.launcher as launcher
with patch.dict(os.environ, {'EDITOR': 'nvim --clean', 'TERMINAL': 'kitty'}), \
        patch('main.launcher.subprocess.Popen') as popen:
    window.open_config()
    command = popen.call_args.args[0]
    assert command[:4] == ['kitty', '--detach', 'nvim', '--clean']
    assert command[-1] == str(window.config_path)
with tempfile.TemporaryDirectory() as directory:
    original_state = launcher.STATE
    original_usage = window.usage
    original_learn = config['ranking']['learn_queries']
    launcher.STATE = Path(directory) / 'usage.json'
    try:
        window.usage = {}
        window.remember(Result('app:test', 'Test', kind='app'), 'Test')
        stored = json.loads(launcher.STATE.read_text())
        assert stored['app:test']['terms'] == ['test']
        assert launcher.STATE.stat().st_mode & 0o777 == 0o600
        config['ranking']['learn_queries'] = False
        window.remember(Result('app:test', 'Test', kind='app'), 'private')
        assert json.loads(launcher.STATE.read_text())['app:test']['terms'] == []
        window.remember(Result('copy:test', 'Sensitive', kind='copy'), 'clipboard contents')
        assert 'copy:test' not in window.usage
        launcher.STATE.write_text('not valid JSON')
        broken = Launcher(config, favorites_path=Path(directory) / 'broken-favorites.sqlite')
        assert not broken.history_writable
        broken.apps_loaded({'error': 'Test app-index failure'})
        assert not broken.is_busy() and any(row.kind == 'mode' for row in broken.rows)
        assert 'Test app-index failure' in broken.footer.text()
        broken.remember(Result('app:test', 'Test', kind='app'), 'test')
        assert launcher.STATE.read_text() == 'not valid JSON', 'Corrupt history was overwritten'
        broken.jobs.stop()
        QTest.qWait(50)
        broken.deleteLater()
    finally:
        launcher.STATE = original_state
        window.usage = original_usage
        config['ranking']['learn_queries'] = original_learn

jobs = Jobs(window)
received, beats = [], []
heartbeat = QTimer()
heartbeat.timeout.connect(lambda: beats.append(1))
heartbeat.start(10)
slow = [sys.executable, '-c', 'import time,json;time.sleep(2);print(json.dumps({"results":[]}))']
fast = [sys.executable, '-c', 'print(\'{"results":["fresh"]}\')']
jobs.start('test', slow, {}, received.append, 70)
until(lambda: bool(received))
assert 'timed out' in received[0]['error'] and len(beats) >= 3
received.clear()
jobs.start('test', slow, {}, received.append)
jobs.start('test', fast, {}, received.append)
until(lambda: bool(received))
assert received == [{'results': ['fresh']}]
heartbeat.stop()
jobs.stop()
window.close()
until(lambda: not window.isVisible() and not window.plugin_runtime.sessions)
print('Qt OK: live math, emoji, root web fallback, stale replies, privacy gate, worker cancellation/timeouts')

with tempfile.TemporaryDirectory() as directory:
    env = {**os.environ, 'XDG_RUNTIME_DIR': directory, 'XDG_DATA_HOME': directory,
           'XDG_CONFIG_HOME': directory, 'XDG_STATE_HOME': directory}
    first = subprocess.Popen([sys.executable, str(APP_ROOT / 'main.py')], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        while not (Path(directory) / 'kite-qt').exists() and time.monotonic() < deadline:
            time.sleep(.05)
        second = subprocess.run([sys.executable, str(APP_ROOT / 'main.py')], env=env,
                                capture_output=True, timeout=8)
        assert second.returncode == 0, second.stderr.decode()
        _, error = first.communicate(timeout=8)
        assert first.returncode == 0, error.decode()
    finally:
        if first.poll() is None:
            first.kill()
            first.communicate()
print('IPC OK: second invocation toggles existing launcher closed')
from io import StringIO
dmenu_window = Launcher(config, 'dmenu', favorites_path=Path(test_plugin_temp.name) / 'dmenu-favorites.sqlite')
dmenu_window.mode_rows = options_from_bytes(b'z-last\nfirst\nsecond\n')
dmenu_window.render()
dmenu_output = StringIO()
with patch('sys.stdout', dmenu_output):
    dmenu_window.activate()
assert dmenu_output.getvalue() == 'z-last\n', 'Empty dmenu query must preserve stdin order'
print('dmenu OK: Qt selection writes only selected line')
# Exercise the real argument parser/input/output/exit path in a fresh Qt process.
dmenu_driver = '''
import sys
from PySide6.QtCore import QTimer
import main.launcher as launcher
original = launcher.Launcher
cancel = sys.argv.pop() == 'cancel'
def fixture(*args, **kwargs):
    window = original(*args, **kwargs)
    def choose():
        if cancel:
            window.back()
        else:
            window.search.setText('second')
            window.activate()
    QTimer.singleShot(100, choose)
    return window
launcher.Launcher = fixture
sys.argv = ['main.py', '--dmenu', '--config', str(launcher.DATA / 'defaults.yaml')]
sys.exit(launcher.main())
'''
with tempfile.TemporaryDirectory() as directory:
    env = {**os.environ, 'XDG_RUNTIME_DIR': directory, 'XDG_DATA_HOME': directory,
           'XDG_CONFIG_HOME': directory, 'XDG_STATE_HOME': directory}
    for mode, code, output in [('select', 0, b'second\n'), ('cancel', 1, b'')]:
        selected = subprocess.run([sys.executable, '-c', dmenu_driver, mode], input=b'first\nsecond\n',
                                  capture_output=True, env=env, timeout=10)
        assert (selected.returncode, selected.stdout) == (code, output), selected.stderr.decode()
print('dmenu CLI OK: real stdin/stdout/selection/cancel status')
test_plugin_temp.cleanup()
