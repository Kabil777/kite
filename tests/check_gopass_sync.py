"""Gopass adapter sync against temporary local Git only; never GitHub/live stores."""
import json
from importlib import import_module
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from main.plugin_api import Event, HostServices, Toast, View, _load_plugin
from tests.plugin_sources import gopass_source

_, plugin = _load_plugin(gopass_source())
backend = import_module(plugin.__name__ + '.backend')


def check_sync():
    git_binary = shutil.which('git')
    assert git_binary, 'git is required for local-only sync verification'
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name in ('home', 'config', 'empty-template', 'stores'):
            (root / name).mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('GIT_') and key not in ('SSH_AUTH_SOCK', 'GPG_AGENT_INFO')}
        env.update(HOME=str(root / 'home'), XDG_CONFIG_HOME=str(root / 'config'),
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TEMPLATE_DIR=str(root / 'empty-template'), GIT_TERMINAL_PROMPT='0',
                   GIT_ALLOW_PROTOCOL='file', GIT_BIN=git_binary,
                   FAKE_STORES=str(root / 'stores'), FAKE_CALLS=str(root / 'calls'),
                   FAKE_REMOTE_ERROR='', FAKE_SYNC_MODE='')
        def git(directory, *args):
            return subprocess.run([git_binary, '-C', str(directory), '-c', 'user.name=KITE fixture',
                                   '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', *args],
                                  env=env, check=True, capture_output=True, timeout=10).stdout
        origin, local, peer = root / 'origin.git', root / 'stores/default', root / 'peer'
        git(root, 'init', '--bare', '--initial-branch=main', str(origin))
        git(root, 'clone', str(origin), str(local))
        (local / 'fixture.txt').write_text('Non-secret fixture only\n')
        git(local, 'add', '.'); git(local, 'commit', '-m', 'fixture initial'); git(local, 'push', '-u', 'origin', 'main')
        git(root, 'clone', str(origin), str(peer))
        (peer / 'from-peer.txt').write_text('fixture pull\n')
        git(peer, 'add', '.'); git(peer, 'commit', '-m', 'fixture peer'); git(peer, 'push')
        (local / 'from-kite.txt').write_text('fixture push\n')
        git(local, 'add', '.'); git(local, 'commit', '-m', 'fixture local')
        executable = root / 'fake-gopass'
        executable.write_text('#!' + sys.executable + '\n' + '''import json, os, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['FAKE_CALLS']).open('a') as log: log.write(json.dumps(args) + '\\n')
command, args = args[0], args[1:]
store = 'default'
if '--store' in args:
    index = args.index('--store'); store = args[index + 1]; del args[index:index + 2]
repo = Path(os.environ['FAKE_STORES']) / store
git = [os.environ['GIT_BIN'], '-C', str(repo)]
if command == 'git' and args == ['remote']:
    if os.environ['FAKE_REMOTE_ERROR']: sys.exit(int(os.environ['FAKE_REMOTE_ERROR']))
    sys.exit(subprocess.run(git + ['remote']).returncode)
if command == 'sync' and not args:
    if os.environ['FAKE_SYNC_MODE'] == 'fail': sys.exit(17)
    if os.environ['FAKE_SYNC_MODE'] == 'hang': time.sleep(60)
    subprocess.run(git + ['-c', 'commit.gpgsign=false', '-c', 'user.name=KITE fixture', '-c', 'user.email=fixture@example.invalid', 'pull', '--rebase'], check=True)
    sys.exit(subprocess.run(git + ['push', 'origin', 'HEAD:main']).returncode)
if command == 'ls': sys.exit(0)
sys.exit(90)
''')
        executable.chmod(0o700)
        env['GOPASS_BIN'] = str(executable)
        def calls():
            return [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
        host = HostServices({'plugins': {}, 'gopass': {'store': ''}})
        with patch.dict(os.environ, env, clear=True):
            response = plugin.handle_event(Event('sync', 'activate'), host)
            assert isinstance(response, View) and response.toast and response.toast.tone == 'success'
            assert (local / 'from-peer.txt').read_text() == 'fixture pull\n'
            assert git(origin, 'show', 'main:from-kite.txt') == b'fixture push\n'
            assert calls()[:2] == [['git', 'remote'], ['sync']]
            (root / 'stores/team').symlink_to(local, target_is_directory=True)
            backend.sync('team')
            assert calls()[-2:] == [['git', '--store', 'team', 'remote'], ['sync', '--store', 'team']]
            for mode, expected in [('remote-error', 'exit 23'), ('sync-error', 'exit 17'), ('cancel', 'cancelled')]:
                before = len(calls())
                with patch.dict(os.environ, FAKE_REMOTE_ERROR='23' if mode == 'remote-error' else '',
                                FAKE_SYNC_MODE='fail' if mode == 'sync-error' else ''):
                    failing_host = HostServices(host.config, cancelled=lambda: mode == 'cancel')
                    response = plugin.handle_event(Event('sync', 'activate'), failing_host)
                assert isinstance(response, Toast) and response.tone == 'error' and expected in response.message
                assert 'No Git remote' not in response.message, 'Backend failure mislabeled as missing remote'
                count = len(calls()) - before
                assert count == (0 if mode == 'cancel' else 1 if mode == 'remote-error' else 2), 'Failure retried'
            original_run = backend._run
            def short_timeout(*args, **kwargs):
                if args[0] == 'sync': kwargs['timeout'] = .2
                return original_run(*args, **kwargs)
            before, started = len(calls()), time.monotonic()
            with patch.dict(os.environ, FAKE_SYNC_MODE='hang'), patch.object(backend, '_run', short_timeout):
                response = plugin.handle_event(Event('sync', 'activate'), host)
            assert isinstance(response, Toast) and 'timed out' in response.message
            assert time.monotonic() - started < 3 and len(calls()) - before == 2
            git(local, 'remote', 'set-url', 'origin', str(root / 'missing.git'))
            before = len(calls())
            response = plugin.handle_event(Event('sync', 'activate'), host)
            assert isinstance(response, Toast) and 'exit' in response.message and 'No Git remote' not in response.message
            assert len(calls()) - before == 2, 'Failed local Git transport retried'
            with patch.dict(os.environ, GOPASS_BIN=str(root / 'missing-gopass')):
                response = plugin.handle_event(Event('sync', 'activate'), host)
                assert isinstance(response, Toast) and 'executable not found' in response.message
            git(local, 'remote', 'remove', 'origin')
            before = len(calls())
            response = plugin.handle_event(Event('sync', 'activate'), host)
            assert isinstance(response, Toast) and 'No Git remote' in response.message
            assert calls()[before:] == [['git', 'remote']], 'Sync ran despite missing remote'
            assert all(row[0] in ('git', 'sync', 'ls') for row in calls()), 'Sync attempted password access'
    print('Gopass sync OK: local pull/rebase/push, store routing, success/error UI, missing remote, cancellation/timeouts and no retries; fake Gopass, no network/credentials/live store')


if __name__ == '__main__':
    check_sync()
