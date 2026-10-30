"""Public, Qt-free contracts and metadata validation for trusted launcher plugins (API v2)."""
from dataclasses import dataclass, field
import importlib.util
from itertools import islice
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Literal, Protocol

API_VERSION = 2
RESERVED_PLUGIN_IDS = frozenset({'root', 'apps', 'calc', 'math', 'calculator', 'clipboard',
                                 'emoji', 'files', 'windows', 'web', 'dmenu', 'marketplace'})


@dataclass(frozen=True)
class Action:
    """Host-validated operation; args are data, never a callback."""
    type: Literal['copy', 'open', 'exec', 'activate', 'clipboard', 'web', 'query', 'back']
    value: str | list[str]
    push: bool = False


@dataclass(frozen=True)
class MenuAction:
    """Plugin-defined Actions menu entry; action uses the normal host action contract."""
    id: str
    title: str
    action: Action
    glyph: str = ''
    shortcut: str = ''


@dataclass(frozen=True)
class Result:
    id: str
    title: str
    subtitle: str = ''
    icon: str = 'application-x-executable'
    action: Action | None = None
    accessibility_label: str = ''
    keywords: str = ''
    glyph: str = ''
    tag: str = ''


@dataclass(frozen=True)
class Node:
    """Declarative UI node. Props/events are validated against node type by host."""
    type: Literal['column', 'row', 'label', 'icon', 'button', 'text_input', 'select', 'list', 'markdown', 'image_preview', 'type_label', 'form', 'shortcut_hint']
    id: str
    props: dict[str, Any] = field(default_factory=dict)
    children: tuple['Node', ...] = ()
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    accessibility_label: str = ''


@dataclass(frozen=True)
class Catalog:
    """Shared native results surface; appearance always resolved from host YAML."""
    placeholder: str = 'Search…'
    footer: str = ''
    heading: str = ''
    layout: Literal['list', 'grid', 'list_detail'] = 'list'
    empty: str = 'No results'
    filters: tuple[str, ...] = ()
    preserve_order: bool = False


@dataclass(frozen=True)
class Toast:
    """Transient host notification. Return alone to preserve the current screen."""
    title: str
    message: str = ''
    tone: Literal['info', 'success', 'error'] = 'info'


def validate_toast(toast):
    if (not isinstance(toast, Toast) or toast.tone not in ('info', 'success', 'error')
            or not isinstance(toast.title, str) or not toast.title.strip() or len(toast.title) > 128
            or not isinstance(toast.message, str) or len(toast.message) > 512):
        raise ValueError('Invalid toast: nonempty title up to 128 chars, message up to 512, info/success/error tone required')


@dataclass(frozen=True)
class View:
    nodes: tuple[Node, ...] = ()
    results: tuple[Result, ...] = ()
    title: str = ''
    error: str = ''
    catalog: Catalog | None = None
    actions: tuple[MenuAction, ...] = ()
    toast: Toast | None = None


@dataclass(frozen=True)
class Event:
    node_id: str
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


def unavailable_service(name: str, args: dict[str, Any]) -> Any:
    raise ValueError('Host service unavailable: ' + name)


@dataclass(frozen=True)
class HostServices:
    """Narrow host context; plugins remain trusted and can access process privileges."""
    config: dict[str, Any]
    cancelled: Callable[[], bool] = lambda: False
    call: Callable[[str, dict[str, Any]], Any] = unavailable_service


class Plugin(Protocol):
    """Entry modules export search(query, host) and optionally handle_event(event, host)."""
    def search(self, query: str, host: HostServices) -> View: ...
    def handle_event(self, event: Event, host: HostServices) -> View | Toast: ...


def plugin_config(host: HostServices, plugin_id: str) -> dict[str, Any]:
    """Return packaged defaults overridden by this plugin's user YAML values."""
    values = {}
    for root in (user_plugin_root(), plugin_root()):
        try:
            manifest = _read_manifest(root / plugin_id)
            values.update({item['key']: item.get('default', item.get('options', [''])[0])
                           for item in manifest.get('configuration', [])})
            break
        except (OSError, ValueError, IndexError):
            continue
    values.update(host.config.get('plugins', {}).get(plugin_id, {}).get('config', {}))
    return values


def plugin_root() -> Path:
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return data / 'kite' / 'plugins'


def user_plugin_root() -> Path:
    config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return config / 'kite' / 'plugin'


def _read_manifest(directory: Path, config=None):
    manifest = directory / 'plugin.json'
    if not manifest.is_file():
        raise ValueError('Plugin manifest must be a regular file')
    with manifest.open('rb') as stream:
        contents = stream.read(16385)
    if len(contents) > 16384:
        raise ValueError('Plugin manifest exceeds 16 KiB')
    data = json.loads(contents)
    if (not isinstance(data, dict) or set(data) - {'id', 'name', 'entry', 'api_version', 'glyph', 'configuration', 'command', 'role', 'execution'}
            or not isinstance(data.get('id'), str)
            or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', data['id']) or data['id'] != directory.name):
        raise ValueError(f'Invalid plugin manifest: {manifest}')
    if config is not None and config.get(data['id'], {}).get('enabled', True) is False:
        return None
    if type(data.get('api_version')) is not int or data['api_version'] != API_VERSION:
        raise ValueError(f'Unsupported plugin API; expected {API_VERSION}. Migrate this package to the v2 worker API.')
    if data.get('role', 'tool') not in ('tool', 'ranking'):
        raise ValueError('Plugin role must be tool or ranking')
    if (data.get('execution', 'systemd') not in ('systemd', 'child')
            or data.get('execution') == 'child' and (data.get('role') != 'ranking' or 'command' not in data)):
        raise ValueError('Child execution requires a single-process executable ranking backend')
    if 'command' in data:
        command = data['command']
        if ('entry' in data or not isinstance(command, list) or not 1 <= len(command) <= 32
                or not all(isinstance(arg, str) and arg and '\0' not in arg and len(arg) <= 4096 for arg in command)
                or sum(len(arg.encode('utf-8')) for arg in command) > 16384):
            raise ValueError('Plugin command must be bounded argv; command and entry are mutually exclusive')
        executable = Path(command[0])
        if not executable.is_absolute() and '/' in command[0]:
            if ('..' in executable.parts or not (directory / executable).is_file()
                    or not (directory / executable).resolve().is_relative_to(directory.resolve())):
                raise ValueError('Relative plugin executable must stay inside the package')
    elif data.get('entry', '__init__.py') != '__init__.py':
        raise ValueError('Plugin entry must be __init__.py')
    if not isinstance(data.get('name'), str) or not data['name'].strip() or len(data['name']) > 128:
        raise ValueError('Invalid plugin name')
    glyph = data.get('glyph', '')
    if not isinstance(glyph, str) or len(glyph) > 8 or any(ord(char) < 32 for char in glyph):
        raise ValueError('Plugin glyph must be at most 8 printable characters')
    fields = data.get('configuration', [])
    if not isinstance(fields, list) or len(fields) > 32:
        raise ValueError('Plugin configuration must be a list of at most 32 fields')
    keys = set()
    for item in fields:
        if (not isinstance(item, dict) or set(item) - {'key', 'label', 'type', 'default', 'options', 'placeholder'}
                or not isinstance(item.get('key'), str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', item['key'])
                or item['key'] in keys or not isinstance(item.get('label'), str) or not item['label'].strip()
                or len(item['label']) > 128 or item.get('type') not in ('text', 'choice')):
            raise ValueError('Invalid plugin configuration field')
        keys.add(item['key'])
        default = item.get('default', item.get('options', [''])[0])
        if item['type'] == 'text' and (not isinstance(default, str) or len(default) > 1024):
            raise ValueError('Text configuration defaults must be strings up to 1024 characters')
        if item['type'] == 'choice':
            options = item.get('options')
            if (not isinstance(options, list) or not options or len(options) > 64
                    or not all(isinstance(value, str) and len(value) <= 256 for value in options)
                    or default not in options):
                raise ValueError('Choice configuration needs bounded options and a valid default')
    if 'command' not in data and not (directory / '__init__.py').is_file():
        raise ValueError('Plugin entry __init__.py is missing')
    return data


def discover_plugins(roots=None, on_error=None, config=None):
    """Read external package metadata only. Never import plugin entry modules."""
    loaded, seen_paths, duplicates = {}, set(), set()

    def report(name, error):
        if on_error is None:
            raise ValueError(str(error))
        on_error(name, str(error))

    for root in roots if roots is not None else (user_plugin_root(), plugin_root()):
        root = Path(root)
        if not root.exists():
            continue
        try:
            entries = list(islice(root.iterdir(), 1001))
            if len(entries) > 1000:
                raise ValueError('Plugin root exceeds 1000 directory entries')
        except (OSError, ValueError) as error:
            report(root.name, error)
            continue
        for directory in sorted(entries):
            if directory.is_symlink() and not directory.exists():
                report(directory.name, 'Broken plugin link; restore its source or remove the link')
                continue
            if not directory.is_dir() or not (directory / 'plugin.json').exists():
                continue
            try:
                data = _read_manifest(directory, config)
                if data is None:
                    continue
                plugin_id = data['id']
                if plugin_id in RESERVED_PLUGIN_IDS:
                    raise ValueError('Plugin ID is reserved for a native tool')
                canonical = directory.resolve(strict=True)
                if canonical in seen_paths:
                    continue
                if len(seen_paths) >= 100:
                    raise ValueError('External plugins exceed the 100-package limit')
                seen_paths.add(canonical)
                if plugin_id in loaded or plugin_id in duplicates:
                    loaded.pop(plugin_id, None)
                    duplicates.add(plugin_id)
                    raise ValueError('Duplicate plugin ID in distinct packages; remove or rename one package')
                loaded[plugin_id] = (data, directory)
            except (OSError, ValueError, RuntimeError) as error:
                report(directory.name, error)
    return list(loaded.values())


def _load_plugin(directory: Path, config=None) -> tuple[dict[str, Any], Any] | None:
    """Explicit Python import for the worker and trusted bundled adapters, not discovery."""
    data = _read_manifest(directory, config)
    if data is None:
        return None
    if 'command' in data:
        raise ValueError('Executable plugins run through the worker protocol, never Python import')
    entry = directory / '__init__.py'
    module_name = '_launcher_plugin_' + str(directory.resolve()).encode().hex()
    spec = importlib.util.spec_from_file_location(module_name, entry, submodule_search_locations=[str(directory)])
    if spec is None or spec.loader is None:
        raise ValueError(f'Cannot load plugin: {entry}')
    # Clear only this package on reload, including backend submodules.
    for key in list(sys.modules):
        if key == module_name or key.startswith(module_name + '.'):
            sys.modules.pop(key)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        method = 'rank' if data.get('role') == 'ranking' else 'search'
        if not callable(getattr(module, method, None)):
            raise ValueError(f'Plugin must define {method}')
        if hasattr(module, 'handle_event') and not callable(module.handle_event):
            raise ValueError('Plugin handle_event must be callable')
    except Exception:
        for key in list(sys.modules):
            if key == module_name or key.startswith(module_name + '.'):
                sys.modules.pop(key)
        raise
    return data, module


def load_plugins(root: Path | None = None, on_error=None, config=None) -> list[tuple[dict[str, Any], Any]]:
    """Load trusted packages, honoring optional YAML enable flags."""
    root = root or plugin_root()
    loaded = []
    if root.exists():
        for directory in sorted(root.iterdir()):
            if not directory.is_dir() or not (directory / 'plugin.json').is_file():
                continue
            try:
                plugin = _load_plugin(directory, config)
                if plugin is not None:
                    loaded.append(plugin)
            except Exception as error:
                if on_error is None:
                    raise
                on_error(directory.name, str(error))
    return loaded


def _canonical_shortcut(value):
    """One portable chord, normalized without importing Qt into the public contract."""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Shortcut must be a string up to 64 characters')
    if not value:
        return ''
    parts = value.split('+')
    names = {'ctrl': 'Ctrl', 'alt': 'Alt', 'shift': 'Shift', 'meta': 'Meta'}
    if any(part.lower() not in names for part in parts[:-1]):
        raise ValueError('Unsupported shortcut modifier')
    modifiers = [names[part.lower()] for part in parts[:-1]]
    if len(set(modifiers)) != len(modifiers):
        raise ValueError('Duplicate shortcut modifier')
    key = parts[-1].upper()
    if not re.fullmatch(r'[A-Z0-9?\/]|F(?:[1-9]|1[0-9]|2[0-4])', key):
        raise ValueError('Shortcut key must be a letter, digit, ?, / or F1–F24')
    if not key.startswith('F') or len(key) == 1:
        if not any(modifier in modifiers for modifier in ('Ctrl', 'Alt', 'Meta')):
            raise ValueError('Typing keys require Ctrl, Alt or Meta')
    if key == '/' and 'Shift' in modifiers:
        key = '?'
    if key == '?' and 'Shift' in modifiers:
        modifiers.remove('Shift')
    result = '+'.join([modifier for modifier in ('Ctrl', 'Alt', 'Shift', 'Meta') if modifier in modifiers] + [key])
    if result in {'Ctrl+?', 'Ctrl+K', 'Ctrl+L', 'Ctrl+N', 'Ctrl+P',
                  'Ctrl+A', 'Ctrl+C', 'Ctrl+V', 'Ctrl+X', 'Ctrl+Z', 'Ctrl+Y',
                  'Ctrl+Shift+C', 'Ctrl+Shift+I', 'Ctrl+Shift+F', 'Ctrl+Shift+D'}:
        raise ValueError('Shortcut is reserved for host navigation, help or editing')
    return result


def _validate_action(action):
    if not isinstance(action, Action):
        raise ValueError('Plugin action must be an Action')
    if action.type not in ('copy', 'open', 'exec', 'activate', 'clipboard', 'web', 'query', 'back'):
        raise ValueError('Unsupported plugin action')
    if type(action.push) is not bool or action.push and action.type not in ('activate', 'query'):
        raise ValueError('Only activate/query actions may push navigation history')
    value = action.value
    if action.type == 'exec':
        if not isinstance(value, list) or not value or len(value) > 64 or not all(isinstance(part, str) and part for part in value):
            raise ValueError('exec action requires a nonempty argv list')
    elif not isinstance(value, str) or len(value) > 4096:
        raise ValueError('Action value must be a string up to 4096 characters')
    if action.type == 'back' and value != '':
        raise ValueError('Back actions use an empty value')


def validate_view(view: View) -> None:
    """Reject malformed trees before host rendering; also catches accidental API drift."""
    if not isinstance(view, View):
        raise TypeError('Plugin must return plugin_api.View')
    seen: set[str] = set()
    node_count = 0

    def check_node(node: Node, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if depth > 16 or node_count > 2000:
            raise ValueError('Plugin view exceeds depth/node limits')
        if not isinstance(node, Node) or node.type not in {
            'column', 'row', 'label', 'icon', 'button', 'text_input', 'select', 'list', 'markdown', 'image_preview', 'type_label', 'form', 'shortcut_hint'
        }:
            raise ValueError('Unsupported plugin UI node')
        if not isinstance(node.id, str) or not node.id or len(node.id) > 128 or node.id in seen:
            raise ValueError('Plugin UI node IDs must be unique nonempty strings up to 128 characters')
        seen.add(node.id)
        if not isinstance(node.props, dict) or not isinstance(node.events, dict):
            raise ValueError('Plugin node props/events must be mappings')
        allowed_props = {
            'column': {'spacing'}, 'row': {'spacing'}, 'label': {'text', 'wrap'}, 'type_label': {'text'},
            'icon': {'name', 'size'}, 'button': {'text', 'variant'},
            'form': {'title', 'description', 'error', 'submit_text'}, 'shortcut_hint': {'keys'},
            'markdown': {'text'}, 'image_preview': {'source', 'alt'},
            'text_input': {'text', 'placeholder', 'password'}, 'select': {'options', 'value'}, 'list': {'items', 'value'},
        }[node.type]
        if set(node.props) - allowed_props:
            raise ValueError(f'Unsupported properties for {node.type}')
        try:
            prop_limit = 6 * 131072 + 1024 if node.type == 'markdown' else 16384
            if len(json.dumps(node.props, ensure_ascii=False)) > prop_limit or len(json.dumps(node.events)) > 16384:
                raise ValueError('Plugin node data exceeds limits')
        except (TypeError, ValueError) as error:
            raise ValueError('Plugin node data must be JSON-compatible') from error
        if any(not isinstance(key, str) or len(key) > 64 for key in (*node.props, *node.events)):
            raise ValueError('Plugin node property/event names must be strings up to 64 characters')
        event_names = {'form': {'submit'}, 'button': {'activate'}, 'text_input': {'change'}, 'select': {'change'},
                       'list': {'activate'}}.get(node.type, set())
        if set(node.events) - event_names or any(not isinstance(payload, dict) for payload in node.events.values()):
            raise ValueError(f'Unsupported events for {node.type}')
        if not isinstance(node.accessibility_label, str) or len(node.accessibility_label) > 512:
            raise ValueError('Invalid accessibility label')
        props = node.props
        text_keys = {'label': ('text',), 'button': ('text',),
                     'text_input': ('text', 'placeholder')}.get(node.type, ())
        if any(key in props and (not isinstance(props[key], str) or len(props[key]) > 4096) for key in text_keys):
            raise ValueError(f'Invalid text property for {node.type}')
        if node.type == 'shortcut_hint' and (not isinstance(props.get('keys', ''), str) or len(props.get('keys', '')) > 128):
            raise ValueError('Shortcut hint keys must be a string up to 128 characters')
        if node.type == 'button' and props.get('variant', 'secondary') not in ('primary', 'secondary', 'danger'):
            raise ValueError('Unsupported button variant')
        if node.type == 'form':
            if depth != 1 or len(view.nodes) != 1 or view.results:
                raise ValueError('A form must be the only root node, without result rows')
            for key, limit in (('title', 256), ('description', 2048), ('error', 512), ('submit_text', 128)):
                if key in props and (not isinstance(props[key], str) or len(props[key]) > limit):
                    raise ValueError('Invalid form text property')
        if node.type == 'type_label' and (not isinstance(props.get('text', ''), str) or len(props.get('text', '')) > 256):
            raise ValueError('Type label text must be a string up to 256 characters')
        if node.type == 'markdown':
            text = props.get('text', '')
            if not isinstance(text, str) or len(text.encode('utf-8')) > 131072:
                raise ValueError('Markdown text must be a string up to 128 KiB')
        if node.type == 'image_preview':
            source, alt = props.get('source'), props.get('alt', '')
            if (not isinstance(source, str) or not source or len(source) > 4096 or '\0' in source
                    or not Path(source).is_absolute()):
                raise ValueError('Image source must be an absolute local path up to 4096 characters')
            if not isinstance(alt, str) or len(alt) > 512:
                raise ValueError('Image alt text must be a string up to 512 characters')
        if node.type in ('row', 'column') and ('spacing' in props
                and (type(props['spacing']) is not int or not 0 <= props['spacing'] <= 48)):
            raise ValueError('Layout spacing must be an integer from 0 to 48')
        if node.type == 'icon':
            if 'name' in props and (not isinstance(props['name'], str) or len(props['name']) > 256):
                raise ValueError('Icon name must be a string up to 256 characters')
            if 'size' in props and (type(props['size']) is not int or not 12 <= props['size'] <= 96):
                raise ValueError('Icon size must be an integer from 12 to 96')
        if node.type == 'label' and 'wrap' in props and type(props['wrap']) is not bool:
            raise ValueError('Label wrap must be boolean')
        if node.type == 'text_input' and 'password' in props and type(props['password']) is not bool:
            raise ValueError('Text input password mode must be boolean')
        for key in ('options', 'items'):
            if key in props and (not isinstance(props[key], list) or len(props[key]) > 1000
                                 or not all(isinstance(value, str) and len(value) <= 512 for value in props[key])):
                raise ValueError(f'{key} must be a list of at most 1000 short strings')
        if node.type in ('select', 'list') and 'value' in props:
            options = props.get('options' if node.type == 'select' else 'items', [])
            if not isinstance(props['value'], str) or props['value'] not in options:
                raise ValueError('Selected value must belong to component options/items')
        if not isinstance(node.children, tuple) or node.children and node.type not in ('row', 'column', 'form'):
            raise ValueError('Only row/column/form nodes accept tuple children')
        for child in node.children:
            check_node(child, depth + 1)

    if (not isinstance(view.title, str) or len(view.title) > 256
            or not isinstance(view.error, str) or len(view.error) > 512):
        raise ValueError('Invalid plugin view title/error')
    if view.toast is not None:
        validate_toast(view.toast)
    if not isinstance(view.nodes, tuple) or not isinstance(view.results, tuple):
        raise ValueError('Plugin view nodes/results must be tuples')
    for node in view.nodes:
        check_node(node, 1)
    if view.catalog is not None:
        catalog = view.catalog
        if (not isinstance(catalog, Catalog) or catalog.layout not in ('list', 'grid', 'list_detail')
                or type(catalog.preserve_order) is not bool or not isinstance(catalog.filters, tuple)
                or len(catalog.filters) > 20
                or not all(isinstance(text, str) and len(text) <= 256 for text in
                           (catalog.placeholder, catalog.footer, catalog.heading, catalog.empty, *catalog.filters))):
            raise ValueError('Invalid native catalog description')
    if not isinstance(view.actions, tuple) or len(view.actions) > 32:
        raise ValueError('Plugin menu actions must be a tuple of at most 32 items')
    menu_ids, shortcuts = set(), set()
    for item in view.actions:
        if (not isinstance(item, MenuAction) or not isinstance(item.id, str) or not item.id
                or len(item.id) > 128 or item.id in menu_ids):
            raise ValueError('Plugin menu action IDs must be unique nonempty strings up to 128 characters')
        menu_ids.add(item.id)
        if (not isinstance(item.title, str) or not item.title.strip() or len(item.title) > 256
                or not isinstance(item.glyph, str) or len(item.glyph) > 8
                or any(ord(char) < 32 for char in item.glyph)):
            raise ValueError('Invalid plugin menu action title/glyph')
        chord = _canonical_shortcut(item.shortcut)
        if chord and chord in shortcuts:
            raise ValueError('Duplicate plugin menu shortcut')
        if chord:
            shortcuts.add(chord)
        _validate_action(item.action)
    if len(view.results) > 5000:
        raise ValueError('Plugin returned too many results')
    result_ids = set()
    for result in view.results:
        if (not isinstance(result, Result) or not isinstance(result.id, str) or not result.id
                or len(result.id) > 512 or result.id in result_ids):
            raise ValueError('Plugin result IDs must be unique nonempty strings')
        result_ids.add(result.id)
        if (not isinstance(result.title, str) or len(result.title) > 2048
                or not isinstance(result.subtitle, str) or len(result.subtitle) > 2048
                or not isinstance(result.icon, str) or len(result.icon) > 256):
            raise ValueError('Invalid plugin result text/icon')
        if (not isinstance(result.keywords, str) or len(result.keywords) > 16384
                or not isinstance(result.glyph, str) or len(result.glyph) > 16
                or not isinstance(result.accessibility_label, str) or len(result.accessibility_label) > 512
                or not isinstance(result.tag, str) or len(result.tag) > 256):
            raise ValueError('Invalid result metadata')
        if result.action is not None:
            _validate_action(result.action)


if __name__ == '__main__':
    # Small Qt-free contract check; no plugins, clipboard or commands are executed.
    validate_view(View(nodes=(Node('type_label', 'kind', {'text': 'Vault'}),
                                  Node('shortcut_hint', 'keys', {'keys': 'Ctrl+R'})),
                       actions=(MenuAction('refresh', 'Refresh', Action('query', '', push=True), shortcut='Ctrl+R'),)))
    validate_view(View(nodes=(Node('form', 'create', {'title': 'Create'},
                                  children=(Node('text_input', 'entry'),), events={'submit': {}}),),
                       actions=(MenuAction('cancel', 'Cancel', Action('back', '')),)))
    try:
        validate_view(View(actions=(MenuAction('bad', 'Bad', Action('exec', [])),)))
    except ValueError:
        pass
    else:
        raise AssertionError('Invalid menu action was accepted')
    validate_toast(Toast('Sync unavailable', 'Configure a Git remote.', 'error'))
    validate_view(View(toast=Toast('Synced', tone='success')))
    bad_tone = Toast('Oops', tone='invalid')  # type: ignore[arg-type] -- intentional contract rejection check
    for bad in (Toast(''), bad_tone, Toast('Oops', 'x' * 513)):
        try:
            validate_toast(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid toast was accepted')
    assert _canonical_shortcut('ctrl+s') == 'Ctrl+S'
    for reserved in ('Ctrl+K', 'Ctrl+Shift+/', 'S'):
        try:
            _canonical_shortcut(reserved)
        except ValueError:
            pass
        else:
            raise AssertionError('Reserved/typing shortcut was accepted')
    print('Plugin form/menu/navigation/shortcut/toast contract check OK')
