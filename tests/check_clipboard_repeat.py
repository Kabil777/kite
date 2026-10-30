"""Repeat restores with a temporary cliphist DB and fake copy command; no live history/clipboard."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from PySide6.QtCore import QBuffer, QCoreApplication, QIODevice, QObject
from PySide6.QtGui import QImage
from main import core
from main.launcher import Jobs


def check():
    binary = shutil.which('cliphist')
    if not binary:
        print('SKIP repeat-history integration: install cliphist to run its isolated DB fixture')
        return
    app = QCoreApplication.instance() or QCoreApplication([])
    with tempfile.TemporaryDirectory(prefix='kite-repeat-history-') as name:
        root = Path(name)
        tools = root / 'bin'; tools.mkdir()
        captured, calls = root / 'copies.jsonl', root / 'decodes'
        args = [binary, '-db-path', str(root / 'fixture.db'), '-config-path', str(root / 'no-config')]
        (tools / 'cliphist').write_text('#!' + sys.executable + '\nimport os, sys\nfrom pathlib import Path\n'
            'if sys.argv[-1] == "decode":\n    with Path(' + repr(str(calls)) + ').open("a") as f: f.write("decode\\n")\n'
            'os.execv(' + repr(binary) + ', ' + repr(args) + ' + sys.argv[1:])\n')
        (tools / 'cliphist').chmod(0o700)
        (tools / 'wl-copy').write_text('#!' + sys.executable + '\nimport base64, json, subprocess, sys\nfrom pathlib import Path\n'
            'data=sys.stdin.buffer.read()\n'
            'with Path(' + repr(str(captured)) + ').open("a") as f: f.write(json.dumps({'
            '"data":base64.b64encode(data).decode(),"args":sys.argv[1:]})+"\\n")\n'
            # Simulate the user's watcher recording every restored value, which deletes its old ID.
            'subprocess.run(' + repr(args + ['store']) + ',input=data,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n')
        (tools / 'wl-copy').chmod(0o700)
        env = {'HOME': name, 'XDG_CONFIG_HOME': name + '/config', 'XDG_CACHE_HOME': name + '/cache',
               'XDG_DATA_HOME': name + '/data', 'XDG_STATE_HOME': name + '/state',
               'PATH': str(tools), 'WAYLAND_DISPLAY': 'fixture-only', 'XDG_SESSION_TYPE': 'wayland'}
        with patch.dict(os.environ, env, clear=True):
            def run(method, data=None):
                return subprocess.run(args + [method], input=data, capture_output=True, timeout=3)
            text = ('KITE_REPEAT_FIXTURE_λ\n' * 1500).encode()
            other = b'KITE_OTHER_FIXTURE'
            image = QImage(2, 2, QImage.Format.Format_ARGB32)
            image.fill(0xff00ff00)
            buffer = QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            assert image.save(buffer, 'PNG')
            png = bytes(buffer.data().data())
            for payload in (text, other, png):
                assert run('store', payload).returncode == 0
            owner = QObject()
            jobs = Jobs(owner)
            def until(test):
                end = time.monotonic() + 7
                while not test() and time.monotonic() < end:
                    app.processEvents(); time.sleep(.005)
                assert test(), 'Clipboard fixture timed out'
            def request(kind, value, key='action'):
                replies = []
                jobs.builtin(key, {'kind': kind, 'value': value}, replies.append, 5000)
                until(lambda: bool(replies))
                return replies[0]
            try:
                assert 'error' not in request('clip', '1')
                assert run('decode', b'1').returncode != 0, 'Fixture did not reproduce ID replacement'
                assert 'error' not in request('clip', '1')
                assert 'error' not in request('clip', '2', 'plugin-service:fixture')
                assert 'error' not in request('clip', '1')  # Returning after copying another entry also works.
                assert 'error' not in request('clip', '3')
                assert 'error' not in request('clip', '3')
                restored = [json.loads(line) for line in captured.read_text().splitlines()]
                assert [base64.b64decode(row['data']) for row in restored] == [text, text, other, text, png, png]
                assert all(row['args'] == ['--type', 'text/plain;charset=utf-8'] for row in restored[:4])
                assert all(row['args'] == ['--type', 'image/png'] for row in restored[4:])
                assert len(calls.read_text().splitlines()) == 3, 'Restored data was decoded again using invalid IDs'
                preview = request('clip-preview', '1')['results']
                assert preview['size'] == len(text) and len(preview['preview']) == 12000
                assert request('clip-preview', '3')['results']['type'] == 'Image'
                assert len(calls.read_text().splitlines()) == 3, 'Cached preview used an obsolete ID'
                assert 'error' not in request('clipboard', '', 'mode')
                assert not jobs.clipboard_cache and jobs.clipboard_cache_bytes == 0
                stale = request('clip', '1')
                assert 'refresh' in stale['error'].lower() and len(captured.read_text().splitlines()) == 6
                listing = run('list').stdout.splitlines()
                latest = listing[0].split(b'\t', 1)[0].decode()
                other_id = next(line.split(b'\t', 1)[0].decode() for line in listing if b'KITE_OTHER_FIXTURE' in line)
                with patch('main.launcher.CLIPBOARD_CACHE_BYTES', 1024):
                    assert 'error' not in request('clip', latest)
                    assert 'error' not in request('clip', other_id)
                    assert jobs.clipboard_cache_bytes <= 1024 and len(jobs.clipboard_cache) == 1
                    assert 'refresh' in request('clip', latest)['error'].lower(), 'Evicted data was silently guessed'
                pending_preview = []
                jobs.builtin('clip-preview', {'kind': 'clip-preview', 'value': other_id}, pending_preview.append)
                jobs.clear_clipboard_cache()
                app.processEvents()
                assert not pending_preview, 'An obsolete cached preview survived cache clearing'
                # A late decode may finish an explicit copy, but must not repopulate a closed mode's cache.
                deferred, finished = [], []
                with patch.object(jobs, 'builtin', side_effect=lambda key, params, callback, timeout: deferred.append(callback)):
                    jobs.restore_clipboard('action', 'late', finished.append, 5000)
                jobs.clear_clipboard_cache()
                deferred[0]({'results': {'data': base64.b64encode(other).decode(), 'mime': 'text/plain;charset=utf-8',
                    'preview': {'preview': other.decode(), 'type': 'Text', 'size': len(other)}}})
                until(lambda: bool(finished))
                assert not jobs.clipboard_cache and jobs.clipboard_cache_bytes == 0
            finally:
                jobs.stop()
                assert not jobs.clipboard_cache
    with patch('main.core.command') as command, patch('main.core.MAX_CLIPBOARD_BYTES', 2):
        command.return_value.stdout = b'123'
        try:
            core.decode_clipboard('fixture')
        except ValueError as error:
            assert 'exceeds' in str(error)
        else:
            raise AssertionError('Oversized history payload was accepted')
    print('Repeat history OK: replaced cliphist IDs, repeated/alternating text+PNG, shared service path, previews, cache lifecycle/eviction and size bound; no live clipboard/history used')


if __name__ == '__main__':
    check()
