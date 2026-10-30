"""Desktop window snapshots and activation; no Qt, shell, or background service."""
from collections import Counter
from importlib import import_module
import json
import os
import re
import shutil
import subprocess
import time
from uuid import UUID


TOOLS = {'hyprland': 'hyprctl', 'sway': 'swaymsg', 'niri': 'niri',
         'kde': 'kdotool', 'x11': 'wmctrl', 'wlr': 'wlrctl'}
KWIN_SNAPSHOT = '''output_result(JSON.stringify(workspace.stackingOrder
.filter(w => w.normalWindow && !w.skipTaskbar && !w.deleted)
.map(w => ({id: String(w.internalId), title: w.caption,
app: String(w.desktopFileName || w.resourceClass || ''),
workspace: w.desktops.map(d => d.name).join(', ')}))));'''


def _text(run, argv):
    try:
        output = run(argv, timeout=1.5).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(f'{argv[0]} window request failed; check its installation and desktop session') from error
    if len(output) > 1024 * 1024:
        raise ValueError('Window information exceeded 1 MiB')
    return output.decode('utf-8')


def _json(run, argv):
    return json.loads(_text(run, argv))


def _wayland():
    return bool(os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland')


def _target(backend, value):
    if backend in ('hyprland', 'x11'):
        if not re.fullmatch(r'0x[0-9a-fA-F]{1,16}', value) or int(value, 16) == 0:
            raise ValueError('Invalid window address')
        value = hex(int(value, 16))
    elif backend in ('sway', 'niri', 'gnome'):
        if not re.fullmatch(r'[0-9]{1,20}', value) or int(value) >= 2 ** (32 if backend == 'gnome' else 64):
            raise ValueError('Invalid window ID')
        value = str(int(value))
    elif backend == 'kde':
        value = '{' + str(UUID(value.strip('{}'))) + '}'
    elif backend == 'wlr':
        pair = json.loads(value)
        if (not isinstance(pair, list) or len(pair) != 2 or not pair[0]
                or any(not isinstance(part, str) or len(part) > 4096
                       or any(ord(c) < 32 for c in part) for part in pair)):
            raise ValueError('Invalid wlroots window selector')
        value = json.dumps(pair, ensure_ascii=False, separators=(',', ':'))
    else:
        raise ValueError('Unknown window backend')
    return backend + ':' + value


def _row(backend, identifier, title, app='', workspace=''):
    if (not all(isinstance(v, str) for v in (title, app, workspace))
            or any(len(v) > 4096 for v in (title, app, workspace))):
        raise ValueError('Invalid window metadata')
    target = _target(backend, str(identifier))
    return {'target': target, 'title': title or app or 'Untitled window',
            'app': app, 'workspace': workspace}


def _gnome(method, identifier=None):
    Gio = import_module('gi.repository.Gio')
    GLib = import_module('gi.repository.GLib')
    arguments = GLib.Variant('(u)', (identifier,)) if identifier is not None else None
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = connection.call_sync('org.gnome.Shell', '/org/gnome/Shell/Extensions/WindowCommander',
                                     'org.gnome.Shell.Extensions.WindowCommander', method, arguments,
                                     GLib.VariantType.new('()' if method == 'Activate' else '(s)'),
                                     Gio.DBusCallFlags.NONE, 500, None)
    except GLib.Error as error:
        raise ValueError('GNOME needs an enabled, shell-compatible Window Commander extension') from error
    return reply.unpack()[0] if method != 'Activate' else None


def _snapshot(backend, run):
    rows = []
    if backend == 'hyprland':
        for w in _json(run, ['hyprctl', 'clients', '-j']):
            if w.get('mapped'):
                rows.append(_row(backend, w['address'], w['title'], w['class'],
                                 str(w['workspace']['name'])))
    elif backend == 'x11':
        if _wayland():
            raise ValueError('X11 window listing cannot cover native Wayland windows')
        for line in _text(run, ['wmctrl', '-u', '-l', '-p', '-x']).splitlines():
            fields = line.split(None, 5)
            if len(fields) < 5:
                raise ValueError('wmctrl returned malformed window information')
            rows.append(_row(backend, fields[0], fields[5] if len(fields) == 6 else '',
                             fields[3], fields[1]))
    elif backend == 'sway':
        pending = [(_json(run, ['swaymsg', '-r', '-t', 'get_tree']), '')]
        while pending:
            w, workspace = pending.pop()
            if w.get('type') == 'workspace':
                workspace = w.get('name') or ''
            app = w.get('app_id') or (w.get('window_properties') or {}).get('class') or ''
            if w.get('type') == 'con' and (w.get('window') or w.get('app_id')):
                rows.append(_row(backend, w['id'], w.get('name') or '', app, workspace))
            pending.extend((child, workspace) for child in w.get('nodes', []) + w.get('floating_nodes', []))
    elif backend == 'niri':
        for w in _json(run, ['niri', 'msg', '--json', 'windows']):
            rows.append(_row(backend, w['id'], w.get('title') or '', w.get('app_id') or '',
                             str(w['workspace_id']) if w.get('workspace_id') is not None else ''))
    elif backend == 'kde':
        for w in _json(run, ['kdotool', 'kwinscript', '--inline', KWIN_SNAPSHOT]):
            rows.append(_row(backend, w['id'], w['title'], w['app'], w['workspace']))
    elif backend == 'gnome':
        deadline = time.monotonic() + 2.5
        for w in json.loads(_gnome('List')):
            if time.monotonic() > deadline:
                raise ValueError('GNOME window listing timed out')
            identifier = w['id']
            _target(backend, str(identifier))
            details = json.loads(_gnome('GetDetails', int(identifier)))
            rows.append(_row(backend, identifier, _gnome('GetTitle', int(identifier)),
                             details.get('wm_class') or ''))
    elif backend == 'wlr':
        # ponytail: CLI selectors have no stable ID; reject ambiguity, use native backends first.
        for line in _text(run, ['wlrctl', 'toplevel', 'list']).splitlines():
            app, separator, title = line.partition(': ')
            if not separator or not app:
                raise ValueError('wlrctl returned an ambiguous or multiline window record')
            rows.append(_row(backend, json.dumps([app, title]), title, app))
        if any(count > 1 for count in Counter(w['target'] for w in rows).values()):
            raise ValueError('wlrctl cannot distinguish windows with the same app and title; use a native desktop integration')
    else:
        raise ValueError('Unknown window backend')
    if len(rows) > 2048:
        raise ValueError('Too many windows to display')
    return rows


def list_windows(run):
    if not _wayland():
        if not os.environ.get('DISPLAY'):
            raise ValueError('No X11 or Wayland desktop session detected')
        return _snapshot('x11', run)
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    hints = [('hyprland', os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') or 'hypr' in desktop),
             ('sway', os.environ.get('SWAYSOCK') or 'sway' in desktop),
             ('niri', os.environ.get('NIRI_SOCKET') or 'niri' in desktop),
             ('kde', 'kde' in desktop or 'plasma' in desktop), ('gnome', 'gnome' in desktop)]
    candidates = [name for name, hinted in hints if hinted]
    candidates += [name for name, _ in hints if name not in candidates]
    candidates.append('wlr')
    errors = []
    for backend in candidates:
        if backend != 'gnome' and not shutil.which(TOOLS[backend]):
            if backend in [name for name, hinted in hints if hinted]:
                errors.append(f'{backend}: install optional {TOOLS[backend]}')
            continue
        try:
            return _snapshot(backend, run)
        except (ValueError, KeyError, TypeError, AttributeError, ImportError, UnicodeError) as error:
            # Do not expose window titles or command arguments through error messages.
            errors.append(f'{backend}: ' + (str(error) if isinstance(error, ValueError)
                                           and not isinstance(error, json.JSONDecodeError)
                                           else 'window interface unavailable or incompatible'))
    raise ValueError('Window switching unavailable. ' + '; '.join(errors)
                     if errors else 'This Wayland session needs a supported window interface or optional helper')


def focus_window(target, run):
    if not isinstance(target, str) or len(target) > 16384:
        raise ValueError('Invalid window target')
    if re.fullmatch(r'0x[0-9a-fA-F]{1,16}', target):
        if not _wayland():
            raise ValueError('Legacy window addresses are only supported on Hyprland')
        target = 'hyprland:' + target
    backend, separator, identifier = target.partition(':')
    if not separator:
        raise ValueError('Window target is missing its desktop backend')
    target = _target(backend, identifier)
    identifier = target.partition(':')[2]
    if target not in {row['target'] for row in _snapshot(backend, run)}:
        raise ValueError('That window is no longer available; reopen the window list')
    if backend == 'hyprland':
        script = 'return hl.dispatch(hl.dsp.focus({window = ' + json.dumps('address:' + identifier) + '}))'
        if _text(run, ['hyprctl', 'eval', script]).strip() != 'ok':
            raise ValueError('Hyprland did not accept the focus request')
    elif backend == 'x11':
        _text(run, ['wmctrl', '-i', '-a', identifier])
    elif backend == 'sway':
        reply = _json(run, ['swaymsg', '-r', f'[con_id={identifier}] focus'])
        if not reply or not all(item.get('success') for item in reply):
            raise ValueError('Sway did not accept the focus request')
    elif backend == 'niri':
        _text(run, ['niri', 'msg', 'action', 'focus-window', '--id', identifier])
    elif backend == 'kde':
        _text(run, ['kdotool', 'windowactivate', identifier])
    elif backend == 'gnome':
        _gnome('Activate', int(identifier))
    elif backend == 'wlr':
        app, title = json.loads(identifier)
        _text(run, ['wlrctl', 'toplevel', 'focus', 'app_id:' + app, 'title:' + title])
