"""Synthetic Gopass copy action through fake clipboard executables; no live clipboard."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest import SkipTest
from unittest.mock import patch

from main.core import provider
from main.host_services import plugin_row
from main.plugin_api import Event, HostServices, _load_plugin
from tests.plugin_sources import gopass_source


def check_copy():
    _, plugin = _load_plugin(gopass_source())
    fixture = 'KITE_COPY_TEST_ONLY_λ'
    with patch.object(plugin, 'password', return_value=fixture):
        view = plugin.handle_event(Event('entry:fixture', 'activate', {'value': 'fixture/test'}),
                                   HostServices({'plugins': {}, 'gopass': {'store': ''}}))
    row = plugin_row('gopass', view.results[0])
    assert row.kind == 'copy' and row.value == fixture
    profiles = (
        ('wl-copy', {'WAYLAND_DISPLAY': 'fixture-wayland', 'XDG_SESSION_TYPE': 'wayland'},
         ['--type', 'text/plain;charset=utf-8']),
        ('xclip', {'DISPLAY': ':fixture'}, ['-selection', 'clipboard', '-in', '-target', 'UTF8_STRING']),
        ('xsel', {'DISPLAY': ':fixture'}, ['--clipboard', '--input']),
    )
    with tempfile.TemporaryDirectory(prefix='kite-password-copy-') as name:
        root = Path(name)
        for executable, display, expected in profiles:
            directory = root / executable
            directory.mkdir()
            capture = directory / 'captured.json'
            script = directory / executable
            script.write_text('#!' + sys.executable + '\nimport json, sys\nfrom pathlib import Path\n'
                              'Path(' + repr(str(capture)) + ').write_text(json.dumps({'
                              '"args":sys.argv[1:],"input":sys.stdin.buffer.read().decode("utf-8")}))\n')
            script.chmod(0o700)
            # Only the fake backend is on PATH; no real clipboard command can be reached.
            with patch.dict(os.environ, {'PATH': str(directory), **display}, clear=True):
                provider(row.kind, value=row.value)
            received = json.loads(capture.read_text())
            assert received == {'args': expected, 'input': fixture}, executable
            assert fixture not in ' '.join(received['args']), 'Password was passed on argv'
    print('Gopass copy OK: synthetic value reaches regular Wayland/X11 CLIPBOARD targets via stdin; no live clipboard/store touched')


if __name__ == '__main__':
    try:
        check_copy()
    except SkipTest as reason:
        print('SKIP:', reason)
