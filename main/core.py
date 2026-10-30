"""Configuration, deterministic ranking, and bounded subprocess providers."""
import ast
import base64
from dataclasses import asdict, dataclass
from importlib import import_module
import json
import io
import math
import operator
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tokenize

import yaml
from .ranking import rank, score, transliterate

DATA = Path(__file__).resolve().parent
APP_ROOT = DATA.parent
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'kite/config.yaml'
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'kite/usage.json'
MAX_CLIPBOARD_BYTES = 32 * 1024 * 1024


@dataclass
class Result:
    id: str
    title: str
    subtitle: str = ''
    icon: str = 'system-search'
    kind: str = 'noop'
    value: object = ''
    keywords: str = ''
    expression: str = ''
    glyph: str = ''
    accessibility_label: str = ''
    tag: str = ''
    push: bool = False

    def json(self):
        return asdict(self)


def load_config(path=CONFIG):
    defaults = yaml.safe_load((DATA / 'defaults.yaml').read_text())
    if not isinstance(defaults, dict):
        raise ValueError('Installed defaults.yaml must be a mapping')
    overrides = yaml.safe_load(Path(path).read_text()) if Path(path).exists() else {}

    def merge(base, extra, prefix=''):
        if not isinstance(extra, dict):
            raise ValueError(f'{prefix or "config"} must be a YAML mapping')
        for key, value in extra.items():
            name = prefix + str(key)
            if not prefix and key == 'plugins':
                if not isinstance(value, dict):
                    raise ValueError('plugins must be a mapping keyed by plugin ID')
                base[key] = value
                continue
            if key not in base:
                raise ValueError(f'Unknown setting: {name}')
            if isinstance(base[key], dict):
                merge(base[key], value, name + '.')
            elif name == 'clipboard.copy_command':
                base[key] = value
            elif type(value) is not type(base[key]):
                raise ValueError(f'{name}: expected {type(base[key]).__name__}')
            else:
                base[key] = value
    merge(defaults, {} if overrides is None else overrides)
    validate_copy_command(defaults['clipboard']['copy_command'])
    ranges = {
        'window': {'width': (420, 2400), 'height': (260, 1600), 'padding': (0, 60), 'radius': (0, 60)},
        'search': {'height': (48, 160), 'padding': (0, 60), 'font_size': (8, 32)},
        'results': {'row_height': (40, 160), 'padding': (0, 50), 'gap': (0, 20),
                    'limit': (1, 200), 'font_size': (8, 28)},
        'icons': {'size': (16, 96), 'search_size': (12, 48)},
        'footer': {'height': (24, 80)},
        'math': {'debounce_ms': (30, 2000), 'timeout_ms': (300, 10000)},
        'web': {'debounce_ms': (100, 3000), 'timeout_ms': (300, 10000),
                'min_characters': (1, 20), 'max_suggestions': (1, 20), 'suggestion_font_size': (8, 32)},
        'emoji': {'columns': (4, 12), 'font_size': (16, 64)},
    }
    for section, fields in ranges.items():
        for key, (low, high) in fields.items():
            if not low <= defaults[section][key] <= high:
                raise ValueError(f'{section}.{key} must be {low}–{high}')
    minimum_height = (defaults['search']['height'] + defaults['results']['row_height']
                      + defaults['window']['padding'] * 2 + 2
                      + (defaults['footer']['height'] if defaults['footer']['visible'] else 0))
    if defaults['window']['height'] < minimum_height:
        raise ValueError(f'window.height must be at least {minimum_height} for this layout')
    if defaults['results']['row_height'] < defaults['icons']['size'] + 8:
        raise ValueError('results.row_height must be at least icons.size + 8')
    for name, color in defaults['colors'].items():
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError(f'colors.{name}: use quoted #RRGGBB')
    for section, key in [('search', 'font_family'), ('results', 'font_family'), ('icons', 'theme')]:
        if not defaults[section][key] or len(defaults[section][key]) > 100:
            raise ValueError(f'{section}.{key}: enter a name up to 100 characters')
    if defaults['results']['layout'] not in ('inline', 'stacked'):
        raise ValueError('results.layout must be inline or stacked')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', defaults['ranking']['engine']):
        raise ValueError('ranking.engine must be builtin or a ranking plugin ID')
    if defaults['ranking']['sensitivity'] not in ('low', 'medium', 'high'):
        raise ValueError('ranking.sensitivity must be low, medium or high')
    for entry in defaults['aliases']:
        if (not isinstance(entry, dict) or set(entry) != {'id', 'alias'}
                or not all(isinstance(v, str) and v.strip() for v in entry.values())
                or len(entry['alias']) > 64):
            raise ValueError('Each alias needs a nonempty id and alias (up to 64 characters)')
    if len({a['id'] for a in defaults['aliases']}) != len(defaults['aliases']):
        raise ValueError('Alias IDs must be unique')
    if len({transliterate(a['alias'].strip()).lower() for a in defaults['aliases']}) != len(defaults['aliases']):
        raise ValueError('Aliases must be unique')
    if not all(isinstance(v, str) and v for v in defaults['favorites']):
        raise ValueError('favorites must contain result IDs')
    store = defaults['gopass']['store']
    plugin_settings = defaults['plugins']
    if len(plugin_settings) > 100:
        raise ValueError('plugins supports at most 100 entries')
    for plugin_id, options in plugin_settings.items():
        if (not isinstance(plugin_id, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', plugin_id)
                or not isinstance(options, dict) or set(options) - {'enabled', 'config'}
                or 'enabled' in options and type(options['enabled']) is not bool):
            raise ValueError(f'Invalid plugin settings for {plugin_id!r}')
        plugin_config = options.get('config', {})
        if not isinstance(plugin_config, dict):
            raise ValueError(f'plugins.{plugin_id}.config must be a mapping')
        try:
            serialized = json.dumps(plugin_config, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(f'plugins.{plugin_id}.config must be JSON-compatible') from error
        if len(serialized) > 16384:
            raise ValueError(f'plugins.{plugin_id}.config exceeds 16 KiB')
    stores = [store]
    if 'gopass' in plugin_settings:
        gopass_config = plugin_settings['gopass'].get('config', {})
        if 'store' in gopass_config:
            stores.append(gopass_config['store'])
    for store in stores:
        if (not isinstance(store, str) or len(store) > 128 or store.startswith('/')
                or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', part) or part in ('.', '..')
                       for part in store.split('/') if store)):
            raise ValueError('gopass store must be an optional relative mount name up to 128 characters')
    names, prefixes = set(), set()
    for extension in defaults['extensions']:
        if (not isinstance(extension, dict) or set(extension) != {'name', 'command', 'prefix'}
                or not isinstance(extension['name'], str) or not extension['name']
                or not isinstance(extension['prefix'], str) or not extension['prefix']
                or not isinstance(extension['command'], list) or not extension['command']
                or not all(isinstance(arg, str) and arg for arg in extension['command'])):
            raise ValueError('Each extension needs name, nonempty prefix and command argv list')
        if extension['prefix'] in prefixes or extension['name'] in names:
            raise ValueError('Extension names and prefixes must be unique')
        names.add(extension['name'])
        prefixes.add(extension['prefix'])
    return defaults


MATH_FUNCTIONS = ('sqrt', 'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'log10', 'log', 'ln',
                  'exp', 'abs', 'round', 'floor', 'ceil', 'factorial', 'solve', 'diff',
                  'integrate', 'limit', 'simplify', 'factor', 'expand')
TRIG_FUNCTIONS = ('sin', 'cos', 'tan', 'asin', 'acos', 'atan')


class IncompleteMathInput(ValueError):
    """An unfinished expression is not a notification-worthy typing error."""


def math_like(query):
    query = query.strip()
    return bool(query.startswith('=') or re.search(r'\d\s*[-+*/^%=×÷]|[-+*/^%=×÷]\s*\d', query)
                or re.match(r'(?:' + '|'.join(MATH_FUNCTIONS) + r')(?=\s|\(|$)', query)
                or re.search(r'\b[xyzt]\s*[-+*/^=]|\d\s*(?:pi|tau|[eixyzt])\b|\d\s*\(', query)
                or re.search(r'\d\s+(?:' + '|'.join(MATH_FUNCTIONS) + r')(?=\s|\(|$)', query)
                or re.fullmatch(r'(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?|pi|e|tau|i', query))


def _math_tree(query):
    """Normalize limited shorthand into AST nodes; never evaluate source text."""
    tokens = []
    balance = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(query).readline):
            if token.type in (tokenize.ENDMARKER, tokenize.NEWLINE, tokenize.NL) or token.string.isspace():
                continue
            if token.type not in (tokenize.NUMBER, tokenize.NAME, tokenize.OP):
                raise ValueError('Use numbers, known functions and arithmetic operators')
            if token.type == tokenize.OP and token.string not in ('+', '-', '*', '/', '%', '**', '(', ')', ','):
                raise ValueError('Unsupported math operator')
            balance += (token.string == '(') - (token.string == ')')
            if balance < 0:
                raise ValueError('Unexpected closing parenthesis')
            tokens.append((token.type, token.string))
    except tokenize.TokenError as error:
        if balance > 0:
            raise IncompleteMathInput('Finish the expression and close its parentheses') from error
        raise ValueError('Invalid math syntax') from error
    position, depth = 0, 0

    def peek():
        return tokens[position][1] if position < len(tokens) else ''

    def take():
        nonlocal position
        if position == len(tokens):
            raise IncompleteMathInput('Finish the expression')
        token = tokens[position]
        position += 1
        return token

    def closing():
        if not peek():
            raise IncompleteMathInput('Close the parenthesis')
        if take()[1] != ')':
            raise ValueError('Expected a closing parenthesis')

    def atom():
        nonlocal depth
        depth += 1
        if depth > 32:
            raise ValueError('Expression too complex')
        try:
            kind, text = take()
            if kind == tokenize.NUMBER:
                return ast.parse(text, mode='eval').body
            if text == '(':
                result = expression()
                closing()
                return result
            if kind != tokenize.NAME or text == 'r':
                raise ValueError('Expected a number, known name or function')
            if text not in MATH_FUNCTIONS:
                return ast.Name(id=text, ctx=ast.Load())
            arguments, radians = [], False
            if peek() == '(':
                take()
                while True:
                    arguments.append(expression())
                    if text in TRIG_FUNCTIONS and peek() == 'r':
                        take()
                        radians = True
                    if peek() != ',':
                        break
                    take()
                closing()
            else:
                if text == 'limit':
                    raise ValueError('Use parentheses for multi-argument functions')
                arguments.append(implicit())
            if text in TRIG_FUNCTIONS and peek() == 'r':
                take()
                radians = True
            return ast.Call(func=ast.Name(id=('_radian_' if radians else '') + text, ctx=ast.Load()),
                            args=arguments, keywords=[])
        finally:
            depth -= 1

    def power():
        left = atom()
        if peek() == '**':
            take()
            return ast.BinOp(left=left, op=ast.Pow(), right=unary())
        return left

    def unary():
        if peek() in ('+', '-'):
            sign = take()[1]
            return ast.UnaryOp(op=ast.USub() if sign == '-' else ast.UAdd(), operand=unary())
        return power()

    def implicit():
        left = unary()
        while position < len(tokens):
            kind, text = tokens[position]
            if (text == 'r' or not (kind in (tokenize.NUMBER, tokenize.NAME) or text == '(')
                    or kind == tokenize.NUMBER and tokens[position - 1][0] == tokenize.NUMBER):
                break
            left = ast.BinOp(left=left, op=ast.Mult(), right=unary())
        return left

    def product():
        left = implicit()
        operators = {'*': ast.Mult, '/': ast.Div, '%': ast.Mod}
        while peek() in operators:
            op = operators[take()[1]]()
            left = ast.BinOp(left=left, op=op, right=implicit())
        return left

    def expression():
        left = product()
        while peek() in ('+', '-'):
            op = ast.Add() if take()[1] == '+' else ast.Sub()
            left = ast.BinOp(left=left, op=op, right=product())
        return left

    result = expression()
    if position != len(tokens):
        if peek() == 'r':
            raise ValueError('Use r only as a trigonometric radian suffix')
        raise ValueError('Unexpected math input; check operators and parentheses')
    return ast.Expression(body=result)


def calculate(query):
    """No sympify/parse_expr/eval on input. AST allowlist constructs SymPy objects directly."""
    import sympy as s
    query = query.strip().removeprefix('=').strip()
    if not query:
        raise IncompleteMathInput('Enter a calculation')
    if len(query) > 256:
        raise ValueError('Math input must contain at most 256 characters')
    query = query.replace('×', '*').replace('÷', '/').replace('^', '**')
    query = re.sub(r'(\d+(?:\.\d+)?)\s*%\s+of\s+', r'(\1/100)*', query, flags=re.I)
    if query.count('=') == 1:
        left, right = query.split('=')
        query = f'solve(({left})-({right}))'
    tree = _math_tree(query)
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError('Expression too complex')
    functions = {name: getattr(s, name) for name in
                 ('sqrt', 'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'log', 'exp',
                  'factorial', 'solve', 'diff', 'integrate', 'limit', 'simplify', 'factor', 'expand')}
    functions.update(ln=s.log, log10=lambda x: s.log(x, 10),
                     round=lambda number, digits=0: s.Rational(str(round(number, int(digits)))),
                     abs=s.Abs, floor=s.floor, ceil=s.ceiling)
    constants = {'pi': s.pi, 'e': s.E, 'tau': 2 * s.pi, 'i': s.I}
    symbols = {name: s.Symbol(name) for name in ('x', 'y', 'z', 't')}
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}

    def value(node):
        if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)):
            if abs(node.value) > 1e100 or not math.isfinite(node.value):
                raise ValueError('Numeric literal too large')
            return s.Integer(node.value) if type(node.value) is int else s.Rational(str(node.value))
        if isinstance(node, ast.Name) and node.id in constants | symbols:
            return (constants | symbols)[node.id]
        if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.UAdd, ast.USub):
            return value(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = value(node.left), value(node.right)
            if isinstance(node.op, ast.Pow) and (not right.is_number or abs(right) > 1000):
                raise ValueError('Exponent must be numeric and within ±1000')
            return binary[type(node.op)](left, right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id.removeprefix('_radian_')
            if name not in functions or not 1 <= len(node.args) <= 4:
                raise ValueError('Unknown function or invalid number of arguments')
            args = [value(arg) for arg in node.args]
            if name in TRIG_FUNCTIONS:
                if len(args) != 1:
                    raise ValueError('Trigonometric functions take one argument')
                radians = node.func.id.startswith('_radian_')
                if name in ('sin', 'cos', 'tan'):
                    return functions[name](args[0] if radians else args[0] * s.pi / 180)
                angle = functions[name](args[0])
                return angle if radians else angle * 180 / s.pi
            if name == 'factorial' and (not args[0].is_Integer or not 0 <= args[0] <= 1000):
                raise ValueError('Factorial supports integers 0–1000')
            return functions[name](*args)
        raise ValueError('Unknown math name or unsupported syntax')
    result = value(tree.body)
    if isinstance(result, s.Basic) and result.has(s.zoo, s.oo, -s.oo, s.nan):
        raise ValueError('Undefined or infinite result')
    exact = str(result)
    if len(exact) > 4000:
        raise ValueError('Result too large to display')
    approximation = (str(s.N(result, 12)) if isinstance(result, s.Expr) and result.is_number
                     and not (result.is_Integer or result.is_Float) else '')
    return exact, approximation if approximation != exact else ''


def suggestions(payload):
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], list):
        raise ValueError('Invalid Google suggestion response')
    seen, result = set(), []
    for text in payload[1]:
        if isinstance(text, str) and 0 < len(text) <= 512 and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def command(args, capture_output=True, timeout=3, **kwargs):
    try:
        return subprocess.run(args, check=True, capture_output=capture_output, timeout=timeout, **kwargs)
    except FileNotFoundError as error:
        executable = args[0] if args else 'unknown'
        raise FileNotFoundError(f'Required executable not found: {executable}') from error


def validate_copy_command(value):
    if value == 'auto':
        return
    if (not isinstance(value, list) or not 1 <= len(value) <= 32
            or not all(isinstance(arg, str) and arg and len(arg) <= 4096 and '\0' not in arg for arg in value)
            or len(json.dumps(value).encode()) > 16384 or '{mime}' in value[0]):
        raise ValueError('clipboard.copy_command must be auto or a nonempty argv list (32 arguments, 16 KiB maximum)')


def clipboard_command(data: bytes, setting: str | list[str] = 'auto', mime='text/plain;charset=utf-8'):
    """Resolve the host's clipboard argv without executing it or exposing payload bytes."""
    validate_copy_command(setting)
    text = mime.startswith('text/')
    if setting != 'auto':
        if not text and not any('{mime}' in arg for arg in setting):
            raise ValueError('Binary copying requires {mime} in clipboard.copy_command; or use auto')
        argv = [arg.replace('{mime}', mime) for arg in setting]
    elif os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
        if not shutil.which('wl-copy'):
            raise ValueError('Wayland copying requires wl-copy; install wl-clipboard or configure clipboard.copy_command')
        argv = ['wl-copy', '--type', mime]
    elif os.environ.get('DISPLAY'):
        if shutil.which('xclip'):
            argv = ['xclip', '-selection', 'clipboard', '-in', '-target', 'UTF8_STRING' if text else mime]
        elif text and b'\0' not in data and shutil.which('xsel'):
            argv = ['xsel', '--clipboard', '--input']
        else:
            raise ValueError('X11 copying requires xclip (text/images) or xsel (text); or configure clipboard.copy_command')
    else:
        raise ValueError('No Wayland/X11 display detected; configure clipboard.copy_command')
    return argv


def copy_bytes(data: bytes, setting: str | list[str] = 'auto', mime='text/plain;charset=utf-8'):
    """One copy attempt; payload stays on stdin, never in command arguments."""
    argv = clipboard_command(data, setting, mime)
    try:
        # Clipboard owners may fork and retain stdio; never wait on their output pipes.
        command(argv, input=data, capture_output=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ValueError('Copy command failed or timed out; clipboard may have changed. Check before retrying.') from error


def decode_clipboard(value):
    try:
        data = command(['cliphist', 'decode'], input=value.encode()).stdout
    except FileNotFoundError as error:
        raise ValueError('Clipboard history requires cliphist; install it and configure a clipboard watcher.') from error
    except subprocess.CalledProcessError as error:
        raise ValueError('This history entry is unavailable; it may have changed or been removed. Reopen Clipboard to refresh the list.') from error
    if len(data) > MAX_CLIPBOARD_BYTES:
        raise ValueError('Clipboard entry exceeds the 32 MiB restore limit; nothing was copied.')
    return data


def clipboard_payload(value):
    from PySide6.QtCore import QMimeDatabase
    data = decode_clipboard(value)
    mime = QMimeDatabase().mimeTypeForData(data).name()
    return data, 'text/plain;charset=utf-8' if mime == 'text/plain' else mime


def clipboard_type(text):
    from .bundled_plugins.clipboard import content_type
    return content_type(text)


def clipboard_preview(data):
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage
    image = QImage.fromData(data)
    if not image.isNull():
        dimensions = f'{image.width()} × {image.height()}'
        image = image.scaled(720, 420, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')  # pyright: ignore[reportCallIssue, reportArgumentType]
        return {'preview': base64.b64encode(bytes(buffer.data().data())).decode(),
                'type': 'Image', 'dimensions': dimensions, 'size': len(data)}
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return {'preview': 'Binary content — no preview available', 'type': 'Binary', 'size': len(data)}
    return {'preview': text[:12000], 'type': clipboard_type(text), 'size': len(data)}


def provider(kind: str, query: str = '', value: str = '', copy_command: str | list[str] = 'auto'):
    if not isinstance(query, str) or not isinstance(value, str):
        raise ValueError('Provider query/value must be strings')
    if kind == 'emoji-data':
        return (DATA / 'emoji-test.txt').read_text()
    if kind == 'clipboard-list':
        return command(['cliphist', 'list']).stdout.decode().splitlines()
    if kind == 'apps':
        # GObject introspection exposes Gio dynamically, without Python source modules.
        Gio = import_module('gi.repository.Gio')
        return [Result('app:' + app.get_id(), app.get_display_name(), 'Application',
                       (app.get_icon().get_names()[0] if isinstance(app.get_icon(), Gio.ThemedIcon)
                        else app.get_icon().get_file().get_path() if isinstance(app.get_icon(), Gio.FileIcon)
                        else 'application-x-executable'),
                       'app', app.get_filename(),
                       ' '.join(app.get_keywords() or []) + ' ' + (app.get_description() or '')).json()
                for app in Gio.AppInfo.get_all()
                if isinstance(app, Gio.DesktopAppInfo) and app.should_show()]
    if kind == 'math':
        exact, approx = calculate(query)
        return [Result('math', exact, (approx + ' · ' if approx else '') + query,
                       'accessories-calculator', 'copy', exact, expression=query).json()]
    if kind == 'clipboard':
        from .bundled_plugins.clipboard import search
        from .host_services import context, rows
        return [row.json() for row in rows(search(query, context({})))]
    if kind == 'clip-data':
        data, mime = clipboard_payload(value)
        return {'data': base64.b64encode(data).decode('ascii'), 'mime': mime, 'preview': clipboard_preview(data)}
    if kind == 'clip-preview':
        return clipboard_preview(decode_clipboard(value))
    if kind == 'windows':
        from .windows import list_windows
        return [Result(w['target'], w['title'],
                       w['app'] + (' · workspace ' + w['workspace'] if w['workspace'] else ''),
                       w['app'].lower() or 'preferences-system-windows', 'focus', w['target']).json()
                for w in list_windows(command)]
    if kind == 'files':
        directory = Path(value).expanduser()
        entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
        return [Result(str(p), p.name, str(directory), 'folder' if p.is_dir() else 'text-x-generic',
                       'directory' if p.is_dir() else 'open', str(p)).json()
                for p in entries if not p.name.startswith('.')][:2000]
    if kind == 'copy':
        copy_bytes(value.encode(), copy_command)
    elif kind == 'clip':
        decoded, mime = clipboard_payload(value)
        copy_bytes(decoded, copy_command, mime)
    elif kind == 'focus':
        from .windows import focus_window
        focus_window(value, command)
    elif kind == 'app':
        Gio = import_module('gi.repository.Gio')
        app = Gio.DesktopAppInfo.new_from_filename(value)
        GLib = import_module('gi.repository.GLib')
        flags = GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.STDOUT_TO_DEV_NULL | GLib.SpawnFlags.STDERR_TO_DEV_NULL
        if app is None or not app.launch_uris_as_manager([], None, flags, None, None, None, None):
            raise ValueError('Application failed to launch')
    else:
        raise ValueError('Unknown provider/action')
    return []


if __name__ == '__main__':
    import resource
    try:
        raw = sys.stdin.buffer.read(131073)
        if len(raw) > 131072:
            raise ValueError('Provider request exceeds 128 KiB')
        request = json.loads(raw)
        # Launched desktop apps must not inherit the helper's symbolic-query resource limits.
        # Input remains bounded; Jobs still enforces the provider's wall-clock/output limits.
        if request['kind'] != 'app':
            resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        result = provider(request['kind'], request.get('query', ''), request.get('value', ''),
                          request.get('copy_command', 'auto'))
        print(json.dumps({'results': result}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'error': str(error)[:300], 'incomplete': isinstance(error, IncompleteMathInput)}))
        sys.exit(1)
