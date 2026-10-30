"""Qt-free Python adapter, service runner and language-neutral systemd supervisor."""
import argparse
from copy import deepcopy
import os
from pathlib import Path
import queue
import re
import runpy
import select
import shutil
import signal
import subprocess
import sys
import threading

from .plugin_api import API_VERSION, HostServices, Toast, View, _load_plugin, _read_manifest
from .plugin_protocol import (WIRE_VERSION, Framer, ProtocolError, encode, event_from_wire,
                              request, response, to_wire)

APP_ROOT = Path(__file__).resolve().parents[1]


class Worker:
    def __init__(self):
        # Keep a private protocol descriptor; even os.write(1, ...) from plugins goes to stderr.
        self.output = os.fdopen(os.dup(sys.stdout.fileno()), 'wb')
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        sys.stdout = sys.stderr
        self.write_lock = threading.Lock()
        self.lock = threading.Lock()
        self.service_lock = threading.Lock()
        self.calls = queue.Queue(maxsize=1)
        self.current = None
        self.waiter = None
        self.sequence = 0
        self.module = None
        self.config = {}
        self.ranking_catalog = None
        self.ranking_ids = frozenset()
        self.ranking = False

    def send(self, message):
        data = encode(message)
        with self.write_lock:
            self.output.write(data)
            self.output.flush()

    def read(self):
        framer = Framer()
        try:
            while data := os.read(sys.stdin.fileno(), 65536):
                for message in framer.feed(data):
                    method = message.get('method')
                    with self.lock:
                        if method == 'cancel':
                            if self.current and message.get('params', {}).get('id') == self.current[0]['id']:
                                self.current[1].set()
                                if self.waiter:
                                    self.waiter['event'].set()
                        elif method == 'shutdown':
                            os._exit(0)  # systemd cleans all descendants when the main worker exits.
                        elif method in ('initialize', 'search', 'rank', 'catalog', 'handle_event'):
                            if self.current is not None:
                                raise ProtocolError('Concurrent plugin calls')
                            self.current = (message, threading.Event())
                            self.calls.put_nowait(self.current)
                        elif method is not None:
                            raise ProtocolError('Invalid host method')
                        elif self.waiter and message['id'] == self.waiter['id']:
                            self.waiter['response'] = message
                            self.waiter['event'].set()
            os._exit(0)
        except Exception:
            os._exit(70)

    def host_call(self, name, args):
        with self.service_lock:
            with self.lock:
                if self.current is None or self.current[1].is_set():
                    raise RuntimeError('Plugin call cancelled')
                parent, token = self.current
                self.sequence += 1
                waiter = {'id': 'service:' + str(self.sequence), 'event': threading.Event()}
                self.waiter = waiter
            try:
                self.send(request(waiter['id'], 'host.call', {'parent': parent['id'], 'name': name, 'args': args}))
                if not waiter['event'].wait(5):
                    raise RuntimeError('Host service timed out')
                if token.is_set():
                    raise RuntimeError('Plugin call cancelled')
                result = waiter.get('response')
                if result is None or 'error' in result:
                    raise RuntimeError('Host service failed')
                return result['result']
            finally:
                with self.lock:
                    if self.waiter is waiter:
                        self.waiter = None

    def initialize(self, params):
        if self.module is not None or set(params) != {'api_version', 'wire_version', 'plugin_id', 'directory', 'config', 'environment'}:
            raise ProtocolError('Invalid initialization')
        if (type(params['api_version']) is not int or params['api_version'] != API_VERSION
                or type(params['wire_version']) is not int or params['wire_version'] != WIRE_VERSION
                or not isinstance(params['directory'], str) or not Path(params['directory']).is_absolute()
                or not isinstance(params['config'], dict) or not isinstance(params['environment'], dict)
                or not all(isinstance(key, str) and isinstance(value, str) and '\0' not in key + value and '=' not in key
                           for key, value in params['environment'].items())):
            raise ProtocolError('Invalid initialization values')
        os.environ.clear()
        os.environ.update(params['environment'])
        loaded = _load_plugin(Path(params['directory']))
        if loaded is None or loaded[0]['id'] != params['plugin_id']:
            raise ProtocolError('Package identity changed')
        self.module, self.config = loaded[1], params['config']
        self.ranking = loaded[0].get('role') == 'ranking'
        return {'api_version': API_VERSION, 'wire_version': WIRE_VERSION,
                'plugin_id': loaded[0]['id'], 'has_handler': callable(getattr(self.module, 'handle_event', None)),
                **({'ranking_version': 2} if self.ranking else {})}

    def run(self):
        threading.Thread(target=self.read, daemon=True).start()
        while True:
            message, token = self.calls.get()
            try:
                if token.is_set():
                    raise RuntimeError('Cancelled')
                method, params = message['method'], message.get('params', {})
                if method == 'initialize':
                    value = self.initialize(params)
                else:
                    if self.module is None:
                        raise ProtocolError('Worker is not initialized')
                    host = HostServices(deepcopy(self.config), token.is_set, self.host_call)
                    if method == 'catalog':
                        from .ranking_plugin import load_catalog
                        if not self.ranking:
                            raise ProtocolError('This plugin has no ranking role')
                        catalog = load_catalog(params)
                        self.ranking_catalog = catalog
                        self.ranking_ids = frozenset(row['id'] for row in catalog['candidates'])
                        value = {'catalog_revision': catalog['catalog_revision'], 'count': len(self.ranking_ids)}
                    elif method == 'rank':
                        from .ranking_plugin import expand_query, validate_request, validate_result
                        cached = params.get('protocol_version') == 2
                        params = expand_query(self.ranking_catalog, params) if cached else validate_request(params)
                        value = self.module.rank(params, host)
                        validate_result(value, params, self.ranking_ids if cached else None)
                    elif method == 'search':
                        if set(params) != {'query'} or not isinstance(params['query'], str) or len(params['query']) > 4096:
                            raise ProtocolError('Invalid search')
                        value = self.module.search(params['query'], host)
                        if not isinstance(value, View):
                            raise ProtocolError('Search must return View')
                    else:
                        if set(params) != {'event'}:
                            raise ProtocolError('Invalid event request')
                        handler = getattr(self.module, 'handle_event', None)
                        value = handler(event_from_wire(params['event']), host) if handler else Toast('Action unavailable', tone='error')
                    if method not in ('rank', 'catalog'):
                        value = to_wire(value)
                result = response(message['id'], value)
                # Encoding is checked before clearing current; malformed replies become bounded errors.
                encode(result)
            except Exception:
                result = response(message['id'], error='Plugin request failed. Check plugin setup and API compatibility.')
            with self.lock:
                self.current = None
            self.send(result)


def stop_unit(unit):
    try:
        subprocess.run(['systemctl', '--user', 'stop', unit], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        from .plugin_manager import unit_empty
        return unit_empty(unit)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def backend_command(package=None):
    """Resolve an argv without importing or executing package code."""
    backend = [sys.executable, '-B', '-m', 'main.plugin_worker', '--worker']
    directory, manifest = APP_ROOT, {}
    if package is not None:
        package = Path(package).absolute()
        manifest = _read_manifest(package)
        if 'command' in manifest:
            directory = package.resolve(strict=True)
            backend = list(manifest['command'])
            if not Path(backend[0]).is_absolute() and '/' in backend[0]:
                backend[0] = str(directory / backend[0])
            else:
                backend[0] = shutil.which(backend[0]) or backend[0]
    return manifest, backend, directory


def child_command(owner, backend, lease_path):
    """Exec-only Linux launch chain: one PID, owner-death cleanup and no descendants."""
    tools = [shutil.which(name) for name in ('setpriv', 'flock', 'prlimit', 'sh')]
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    if (not all(tools) or os.getuid() == 0 or os.geteuid() != os.getuid()
            or any(int(status.get(key, '1').strip(), 16) for key in ('CapEff', 'CapPrm', 'CapAmb'))):
        raise RuntimeError('Direct ranking requires an unprivileged Linux user and setpriv/flock/prlimit/sh')
    setpriv, flock, prlimit, shell = tools
    # The shell verifies PPID after PDEATHSIG is installed, closing the owner-death startup race.
    # All wrappers exec; no shell, supervisor or Python adapter remains alongside the backend.
    return [setpriv, '--pdeathsig', 'KILL', '--no-new-privs', '--', shell, '-c',
            'test "$PPID" -eq "$1" || exit 78; shift; exec "$@"', 'kite-ranking', str(owner),
            flock, '--shared', '--nonblock', '--no-fork', str(lease_path),
            prlimit, '--as=805306368', '--nproc=0', '--nofile=64', '--core=0', '--', *backend]


def supervise(owner, unit, allowed=(), package=None):
    if (not hasattr(os, 'pidfd_open') or os.getppid() != owner
            or not re.fullmatch(r'kite-plugin-[0-9]+-[0-9a-f]{32}', unit)
            or not Path('/sys/fs/cgroup/cgroup.controllers').is_file()
            or not shutil.which('systemd-run') or not shutil.which('systemctl')):
        return 78
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    process = None
    owner_fd = child_fd = None
    lease = None
    started = False
    try:
        from .plugin_manager import Manager, UseLease, units_clear
        manager = Manager()
        lease = UseLease(manager.store)
        if manager._has_journal() or not units_clear(allowed):
            return 78
        if stopped.is_set():
            return 0
        owner_fd = os.pidfd_open(owner)
        if os.getppid() != owner or select.select([owner_fd], [], [], 0)[0]:
            return 78
        _, backend, working_directory = backend_command(package)
        command = ['systemd-run', '--user', '--pipe', '--wait', '--collect', '--quiet', '--service-type=exec',
                   '--unit=' + unit, '--working-directory=' + str(working_directory),
                   '--property=KillMode=control-group', '--property=TimeoutStopSec=1s',
                   '--property=MemoryMax=768M', '--property=TasksMax=256', '--property=CPUQuota=100%',
                   '--property=OOMPolicy=kill', '--', *backend]
        process = subprocess.Popen(command)
        started = True
        try:
            child_fd = os.pidfd_open(process.pid)
        except ProcessLookupError:
            return 78
        while not stopped.is_set():
            ready, _, _ = select.select([owner_fd, child_fd], [], [], .1)
            if owner_fd in ready:
                break
            if child_fd in ready:
                return 0 if process.wait() == 0 else 70  # 78 is reserved for supervisor/cleanup failure.
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 78
    finally:
        if started:
            cleaned = stop_unit(unit)
            if process is not None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if not cleaned:
                # No process-group fallback: the host must treat exit 78 as unconfirmed cleanup.
                os._exit(78)
        for descriptor in (owner_fd, child_fd):
            if descriptor is not None:
                os.close(descriptor)
        if lease is not None:
            lease.close()


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--worker', action='store_true')
    mode.add_argument('--supervise', action='store_true')
    mode.add_argument('--service', action='store_true')
    parser.add_argument('--owner', type=int)
    parser.add_argument('--unit')
    parser.add_argument('--allow-unit', action='append', default=[])
    parser.add_argument('--package')
    args = parser.parse_args()
    if args.service:
        from .plugin_manager import Manager, UseLease
        lease = UseLease(Manager().store)
        try:
            runpy.run_module('main.core', run_name='__main__')
        finally:
            lease.close()
    elif args.worker:
        Worker().run()
    elif args.owner is None or args.unit is None:
        parser.error('--supervise requires --owner and --unit')
    else:
        raise SystemExit(supervise(args.owner, args.unit, args.allow_unit, args.package))


if __name__ == '__main__':
    main()
