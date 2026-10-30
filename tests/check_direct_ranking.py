"""Direct ranking lifecycle on synthetic fixtures; no systemd or user stores needed."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
from threading import Event
import time
from types import SimpleNamespace
from unittest.mock import patch

from main.plugin_manager import Manager, UseLease
from main.plugin_worker import child_command


def bounds_and_owner(store):
    lease = UseLease(store)
    source = '''
import os, resource, threading
assert resource.getrlimit(resource.RLIMIT_AS) == (805306368, 805306368)
assert resource.getrlimit(resource.RLIMIT_NPROC) == (0, 0)
assert resource.getrlimit(resource.RLIMIT_NOFILE) == (64, 64)
assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
try:
    pid = os.fork()
except OSError:
    pass
else:
    if pid == 0: os._exit(99)
    os.waitpid(pid, 0)
    raise AssertionError('Direct ranker spawned a child')
try:
    threading.Thread(target=lambda: None).start()
except RuntimeError:
    pass
else:
    raise AssertionError('Direct ranker spawned a thread')
try:
    bytearray(900 * 1024 * 1024)
except MemoryError:
    pass
else:
    raise AssertionError('Address-space limit was not enforced')
print('bounded', flush=True)
'''
    try:
        result = subprocess.run(child_command(os.getpid(), [sys.executable, '-B', '-c', source], lease.path),
                                capture_output=True, timeout=5)
        assert result.returncode == 0 and result.stdout == b'bounded\n', result.stderr
        result = subprocess.run(child_command(os.getpid() + 100000, ['/bin/echo', 'unexpected'], lease.path),
                                capture_output=True, timeout=5)
        assert result.returncode == 78 and not result.stdout, 'Dead-owner startup guard was bypassed'
    finally:
        lease.close()
    owner_source = '''
import os, subprocess, sys, time
from pathlib import Path
from main.plugin_manager import UseLease
from main.plugin_worker import child_command
lease = UseLease(Path(sys.argv[1]))
backend = [sys.executable, '-B', '-c', "import time; print('ready', flush=True); time.sleep(60)"]
child = subprocess.Popen(child_command(os.getpid(), backend, lease.path), stdout=subprocess.PIPE)
assert child.stdout.readline() == b'ready\\n'
lease.close()  # The exec'd backend must independently retain its shared package lease.
print(child.pid, flush=True)
time.sleep(60)
'''
    owner = subprocess.Popen([sys.executable, '-B', '-c', owner_source, str(store)], stdout=subprocess.PIPE)
    descriptor = None
    try:
        assert select.select([owner.stdout], [], [], 5)[0], 'Owner fixture timed out'
        pid = int(owner.stdout.readline())
        descriptor = os.pidfd_open(pid)
        try:
            unexpected = UseLease(store, shared=False)
        except ValueError:
            pass
        else:
            unexpected.close()
            raise AssertionError('Backend failed to retain its package lease')
        owner.kill()
        owner.wait(timeout=5)
        assert select.select([descriptor], [], [], 3)[0], 'Backend survived owner death'
        released = UseLease(store, shared=False)
        released.close()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        owner.stdout.close()
        if descriptor is not None:
            os.close(descriptor)


def qt_failures(manager):
    from PySide6.QtCore import QCoreApplication, QObject, QTimer
    from main.core import Result
    from main.plugin_runtime import Call, Runtime, LIVE_CHILDREN, LIVE_UNITS
    from main.ranking_plugin import make_request
    app = QCoreApplication.instance() or QCoreApplication([])
    directory = manager.authored / 'child-test'
    directory.mkdir(parents=True)
    (directory / 'plugin.json').write_text(json.dumps({'id': 'child-test', 'name': 'Synthetic child',
        'api_version': 2, 'role': 'ranking', 'execution': 'child', 'command': [sys.executable, '-B', 'backend.py']}))
    (directory / 'backend.py').write_text('''
import json, os, signal, sys, time
for line in sys.stdin:
    message = json.loads(line)
    p = message.get('params', {})
    if message['method'] == 'initialize':
        result = dict(api_version=2, wire_version=1, plugin_id=p['plugin_id'], has_handler=False)
    elif message['method'] == 'shutdown':
        break
    elif message['method'] == 'cancel':
        continue
    else:
        if p['query'] == 'crash': os._exit(78)
        if p['query'] == 'hang':
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(60)
        result = dict(catalog_revision=p['catalog_revision'], ordered_ids=['unknown'] if p['query'] == 'bad' else ['a'])
    print(json.dumps(dict(jsonrpc='2.0', id=message['id'], result=result)), flush=True)
''')
    owner = QObject()
    jobs = SimpleNamespace(plugin_services_idle=lambda: True, cancel=lambda key: None)
    runtime = Runtime(owner, jobs, {}, lambda call: not call.cancelled.is_set(), manager)
    beats = []
    timer = QTimer(owner)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start(10)

    def until(test):
        end = time.monotonic() + 6
        while not test() and time.monotonic() < end:
            app.processEvents()
            time.sleep(.005)
        assert test(), 'Direct child did not finish/clean up'

    try:
        for revision, query in enumerate(('good', 'bad', 'crash', 'hang'), 1):
            replies = []
            params = make_request([Result('a', 'Alpha')], query, {}, {}, [],
                                  {'sensitivity': 'medium', 'learn_queries': False}, 60)
            call = Call('child-test', revision, 'rank', params, Event(), known_ids=frozenset({'a'}))
            call.signals.finished.connect(lambda pid, rev, value, error: replies.append((value, error)))
            runtime.submit(call, directory)
            until(lambda: bool(replies))
            assert (replies[0][0] == ['a'] and replies[0][1] is None) if query == 'good' else replies[0][1]
            runtime.deactivate()
            until(lambda: runtime.quiescent)
            assert not runtime.blocked and not LIVE_CHILDREN and not LIVE_UNITS
        assert len(beats) > 20, 'Hung child blocked the Qt event loop'
        replies = []
        call = Call('child-test', 5, 'rank', params, Event(), known_ids=frozenset({'a'}))
        call.signals.finished.connect(lambda pid, rev, value, error: replies.append(error))
        with patch('main.plugin_worker.shutil.which', return_value=None):
            runtime.submit(call, directory)
            until(lambda: bool(replies))
        assert 'setpriv' in replies[0] and runtime.quiescent and not runtime.blocked
        assert not LIVE_CHILDREN and not LIVE_UNITS, 'Missing limits silently changed execution mode'
    finally:
        timer.stop()
        runtime.deactivate()
        until(lambda: runtime.quiescent)


def main():
    with tempfile.TemporaryDirectory(prefix='kite-direct-ranking-') as name, patch.dict(os.environ, {
        'XDG_CONFIG_HOME': name + '/config', 'XDG_DATA_HOME': name + '/data',
        'XDG_STATE_HOME': name + '/state', 'XDG_CACHE_HOME': name + '/cache',
    }):
        manager = Manager()
        bounds_and_owner(manager.store)
        qt_failures(manager)
    print('Direct ranking OK: memory/fork/thread limits, startup race, owner death, leases, RPC, crash/hang cleanup')


if __name__ == '__main__':
    main()
