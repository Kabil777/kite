"""Clipboard portability checks: mocks and temporary capture files only."""
import base64
import json
import os
import signal
import time
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import yaml
from main.core import copy_bytes, load_config, provider
from main.host_services import context, service_request


def check_clipboard():
    def automatic(env, available, mime='text/plain;charset=utf-8'):
        with patch.dict(os.environ, env, clear=True), patch('main.core.shutil.which', side_effect=lambda name: name if name in available else None), patch('main.core.command') as command:
            copy_bytes(b'fixture-only', mime=mime)
            assert command.call_args.kwargs == {'input': b'fixture-only', 'capture_output': False,
                                                'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
            return command.call_args.args[0]

    assert automatic({'WAYLAND_DISPLAY': 'wayland-test', 'DISPLAY': ':99'}, {'wl-copy', 'xclip'}) == ['wl-copy', '--type', 'text/plain;charset=utf-8']
    assert automatic({'XDG_SESSION_TYPE': 'wayland'}, {'wl-copy'}, 'image/png') == ['wl-copy', '--type', 'image/png']
    assert automatic({'DISPLAY': ':99'}, {'xclip', 'xsel'}) == ['xclip', '-selection', 'clipboard', '-in', '-target', 'UTF8_STRING']
    assert automatic({'DISPLAY': ':99'}, {'xclip'}, 'image/png')[-1] == 'image/png'
    assert automatic({'DISPLAY': ':99'}, {'xsel'}) == ['xsel', '--clipboard', '--input']
    for env, available, mime in [({}, {'wl-copy'}, 'text/plain'), ({'WAYLAND_DISPLAY': 'test', 'DISPLAY': ':99'}, {'xclip'}, 'text/plain'), ({'DISPLAY': ':99'}, {'xsel'}, 'image/png'), ({'DISPLAY': ':99'}, set(), 'text/plain')]:
        try:
            automatic(env, available, mime)
        except ValueError:
            pass
        else:
            raise AssertionError('Missing/incompatible backend was accepted')
    with patch('main.core.command') as command:
        copy_bytes(b'raw\x00bytes', ['custom-copy', '--type={mime}'], 'image/png')
        command.assert_called_once_with(['custom-copy', '--type=image/png'], input=b'raw\x00bytes',
                                        capture_output=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with patch('main.core.command') as command:
        try:
            copy_bytes(b'raw\x00bytes', ['text-only'], 'image/png')
        except ValueError:
            pass
        else:
            raise AssertionError('Binary copied without MIME support')
        command.assert_not_called()
    for failure in (subprocess.TimeoutExpired('fixture', 3), subprocess.CalledProcessError(1, 'fixture', stderr=b'private fixture')):
        with patch('main.core.command', side_effect=failure) as command:
            try:
                copy_bytes(b'fixture-only', ['fixture-copy'])
            except ValueError as error:
                assert 'may have changed' in str(error) and 'private fixture' not in str(error)
            else:
                raise AssertionError('Failed copy reported success')
            assert command.call_count == 1, 'Uncertain copy retried'
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config_file = root / 'config.yaml'
        for value in ['auto', ['custom-copy', '--mime', '{mime}']]:
            with config_file.open('w') as stream:
                yaml.safe_dump({'clipboard': {'copy_command': value}}, stream)
            assert load_config(config_file)['clipboard']['copy_command'] == value
        for value in [None, '', 'wl-copy --type text/plain', [], [''], ['bad\x00name'], [1], ['x'] * 33, ['x', '界' * 4096, '界' * 4096], ['{mime}']]:
            with config_file.open('w') as stream:
                yaml.safe_dump({'clipboard': {'copy_command': value}}, stream)
            try:
                load_config(config_file)
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid command configuration accepted')
        captured = root / 'payload'
        argv = [sys.executable, '-c', 'import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())', str(captured)]
        provider('copy', value='fixture-only\nλ', copy_command=argv)
        assert captured.read_bytes() == 'fixture-only\nλ'.encode()
        owner = root / 'clipboard-owner'
        forked = [sys.executable, '-c', '''import os, sys, time
from pathlib import Path
sys.stdin.buffer.read()
pid = os.fork()
if pid == 0:
    time.sleep(60)
    os._exit(0)
Path(sys.argv[1]).write_text(str(pid))
''', str(owner)]
        started = time.monotonic()
        try:
            copy_bytes(b'fixture-only', forked)
            assert time.monotonic() - started < 2, 'Background clipboard owner held capture pipes open'
        finally:
            if owner.exists():
                pidfd = os.pidfd_open(int(owner.read_text()))
                try:
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                finally:
                    os.close(pidfd)
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0ioAAAAASUVORK5CYII=')
        with patch('main.core.command') as command:
            command.return_value.stdout = png
            provider('clip', value='42', copy_command=['capture', '{mime}'])
            assert command.call_args_list[0].args[0] == ['cliphist', 'decode']
            command.assert_called_with(['capture', 'image/png'], input=png, capture_output=False,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cfg = {'clipboard': {'copy_command': argv}}
        with patch('main.host_services.subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, stdout=b'{"results": []}')
            context(cfg).call('clipboard.write', {'value': 'fixture-only'})
            request = json.loads(run.call_args.kwargs['input'])
            assert request['copy_command'] == argv and request['value'] == 'fixture-only'
        try:
            service_request('clipboard.write', {'value': 'fixture', 'copy_command': ['override']})
        except ValueError:
            pass
        else:
            raise AssertionError('Plugin could override trusted copy command')
    print('Clipboard OK: Wayland/X11/custom commands, byte/MIME fidelity, stdin-only payloads, config/service validation and no retries; no real clipboard touched')


if __name__ == '__main__':
    check_clipboard()
