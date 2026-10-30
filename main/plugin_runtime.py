"""Qt-side RPC runtime: supervised tools and opt-in, bounded single-process rankers."""
from dataclasses import dataclass, field
import atexit
import os
from pathlib import Path
import sys
from threading import Event
import time
import uuid

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from .host_services import service_request
from .plugin_api import API_VERSION
from .plugin_manager import Manager, UseLease
from .ranking_plugin import catalog_request, query_request, validate_result
from .plugin_protocol import WIRE_VERSION, MAX_FRAME, Framer, ProtocolError, encode, from_wire, request, response

APP_ROOT = Path(__file__).resolve().parents[1]
LIVE_UNITS = set()
LIVE_CHILDREN = set()


def _cleanup_units():
    # Python unwinding can destroy QProcess before the owner's pidfd signals death.
    from .plugin_worker import stop_unit
    for process in tuple(LIVE_CHILDREN):
        try:
            process.kill()
            process.waitForFinished(1000)
        except RuntimeError:
            pass  # QObject destruction or the kernel parent-death signal already owns cleanup.
    for unit in tuple(LIVE_UNITS):
        stop_unit(unit)


atexit.register(_cleanup_units)


class Signals(QObject):
    finished = Signal(str, int, object, object)


@dataclass
class Call:
    plugin_id: str
    revision: int
    method: str
    params: dict
    cancelled: Event
    navigation: tuple | None = None
    submitted: bool = False
    signals: Signals = field(default_factory=Signals)
    known_ids: frozenset[str] | None = None

    def finish(self, value=None, error=None):
        self.signals.finished.emit(self.plugin_id, self.revision, value, error)


class Runtime(QObject):
    idle = Signal()

    def __init__(self, parent, jobs, config, is_current, manager=None):
        super().__init__(parent)
        self.jobs, self.config, self.is_current = jobs, config, is_current
        self.active = None
        self.sessions = []
        self.pending = None
        self.blocked = False
        self.suspended = False
        self.manager = manager if manager is not None else Manager()

    @property
    def quiescent(self):
        return not self.sessions and self.jobs.plugin_services_idle()

    def confirm_cleanup(self):
        if not self.quiescent:
            raise RuntimeError('Cannot clear cleanup failure while plugin work remains')
        self.blocked = False
        LIVE_UNITS.clear()  # Caller holds EX lease and has confirmed all plugin units empty.

    def notify_idle(self):
        if self.quiescent:
            self.idle.emit()
        elif not self.sessions:
            QTimer.singleShot(25, self.notify_idle)

    def submit(self, call, directory):
        if self.suspended:
            call.finish(error='Plugin management is waiting for cleanup; retry when it finishes.')
            return
        # Permission and ownership are checked at start_pending/next_call, immediately before dispatch.
        if self.blocked:
            call.finish(error='External plugins unavailable: systemd user support or confirmed worker cleanup is required.')
        elif self.active and self.active.plugin_id == call.plugin_id and not self.active.stopping:
            self.active.submit(call)
        else:
            self.deactivate()
            self.pending = (call, directory)
            self.start_pending()

    def start_pending(self):
        if self.suspended or not self.pending or len(self.sessions) >= 2:
            return
        call, directory = self.pending
        if any(session.plugin_id == call.plugin_id for session in self.sessions):
            return
        self.pending = None
        if call.cancelled.is_set() or not self.is_current(call):
            return
        if self.blocked:
            call.finish(error='External plugins unavailable: worker cleanup could not be confirmed.')
            return
        try:
            directory = self.manager.activation_directory(call.plugin_id, directory)
        except (OSError, ValueError, RuntimeError) as error:
            call.finish(error=str(error)[:512])
            return
        session = Session(self, call.plugin_id, directory)
        self.active = session
        self.sessions.append(session)
        session.submit(call)
        session.start()

    def cancel(self):
        if self.pending:
            self.pending[0].cancelled.set()
            self.pending = None
        if self.active:
            self.active.cancel()

    def deactivate(self):
        if self.pending:
            self.pending[0].cancelled.set()
            self.pending = None
        if self.active:
            active, self.active = self.active, None
            active.retire()
        self.notify_idle()

    def ended(self, session, code):
        if session in self.sessions:
            self.sessions.remove(session)
        LIVE_CHILDREN.discard(session.process)
        if session.lease is not None:
            session.lease.close()
            session.lease = None
        if self.active is session:
            self.active = None
        if code == 78:
            self.blocked = True
            pending, self.pending = self.pending, None
            self.deactivate()
            if pending:
                pending[0].finish(error='Worker cleanup is unconfirmed; external plugins are blocked.')
        else:
            LIVE_UNITS.discard(session.unit)
        queued, session.queued = session.queued, None
        if queued and not queued.cancelled.is_set() and self.is_current(queued):
            self.pending = (queued, session.directory)
        session.deleteLater()
        self.start_pending()
        self.notify_idle()


class Session(QObject):
    def __init__(self, runtime, plugin_id, directory):
        super().__init__(runtime)
        self.runtime, self.plugin_id, self.directory = runtime, plugin_id, Path(directory)
        self.direct = False
        self.lease = None
        self.generation = uuid.uuid4().hex
        self.unit = f'kite-plugin-{os.getpid()}-{self.generation}'
        self.init_id = self.generation + ':init'
        self.current = self.queued = self.service = None
        self.initialized = self.stopping = self.cancel_sent = False
        self.ranking_version = 1
        self.loaded_catalog = None
        self.loading_catalog = False
        self.framer = Framer()
        self.recent_ids = []
        self.traffic_start, self.output_bytes, self.error_bytes = time.monotonic(), 0, 0
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(APP_ROOT))
        self.process.started.connect(self.initialize)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_error)
        self.process.finished.connect(self.exited)
        self.process.errorOccurred.connect(self.process_error)
        self.deadline = QTimer(self); self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(lambda: self.fail('Plugin timed out. An operation may have completed; check before retrying.'))
        self.cancel_timer = QTimer(self); self.cancel_timer.setSingleShot(True)
        self.cancel_timer.timeout.connect(self.terminate)
        self.kill_timer = QTimer(self); self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self.process.kill)

    def start(self):
        from .plugin_worker import backend_command, child_command
        self.deadline.start(5000)
        try:
            manifest, backend, directory = backend_command(self.directory)
            self.direct = manifest.get('execution') == 'child'
            if self.direct:
                self.unit = None
                self.lease = UseLease(self.runtime.manager.store)
                command = child_command(os.getpid(), backend, self.lease.path)
                parameters = QProcess.UnixProcessParameters()
                parameters.flags = (QProcess.UnixProcessFlag.CreateNewSession | QProcess.UnixProcessFlag.CloseFileDescriptors
                                    | QProcess.UnixProcessFlag.ResetSignalHandlers | QProcess.UnixProcessFlag.DisableCoreDumps)
                self.process.setUnixProcessParameters(parameters)
                self.process.setWorkingDirectory(str(directory))
                LIVE_CHILDREN.add(self.process)
                self.process.start(command[0], command[1:])
            else:
                LIVE_UNITS.add(self.unit)
                allowed = [arg for session in self.runtime.sessions if session is not self and session.unit
                           for arg in ('--allow-unit', session.unit)]
                self.process.start(sys.executable, ['-B', '-m', 'main.plugin_worker', '--supervise',
                                                  '--owner', str(os.getpid()), '--unit', self.unit,
                                                  '--package', str(self.directory.absolute()), *allowed])
        except (OSError, ValueError, RuntimeError) as error:
            self.fail(str(error)[:512])
            self.runtime.ended(self, 70)

    def send(self, message):
        if self.process.state() != QProcess.ProcessState.Running:
            return False
        try:
            data = encode(message)
            if self.process.bytesToWrite() + len(data) > MAX_FRAME + 65536:
                raise ProtocolError('Pending output exceeds limit')
            return self.process.write(data) == len(data)
        except (ValueError, TypeError):
            self.fail('Plugin protocol limits exceeded.')
            return False

    def initialize(self):
        if self.stopping:
            self.process.terminate()
            return
        self.send(request(self.init_id, 'initialize', {
            'api_version': API_VERSION, 'wire_version': WIRE_VERSION, 'plugin_id': self.plugin_id,
            'directory': str(self.directory.absolute()), 'config': self.runtime.config, 'environment': dict(os.environ)}))

    def submit(self, call):
        if self.queued:
            self.queued.cancelled.set()
        self.queued = call
        if self.current:
            self.cancel(drop_queue=False)
        elif self.initialized:
            self.next_call()

    def next_call(self):
        if self.stopping or self.runtime.suspended or not self.queued:
            return
        call, self.queued = self.queued, None
        if call.cancelled.is_set() or not self.runtime.is_current(call):
            return
        try:
            self.runtime.manager.activation_directory(call.plugin_id, self.directory)
        except (OSError, ValueError, RuntimeError) as error:
            call.finish(error=str(error)[:512])
            self.terminate()
            return
        self.current = call
        self.cancel_sent = False
        self.deadline.start(1000 if call.method == 'rank' else 5000 if call.method == 'search' else 120000)
        self.loading_catalog = (call.method == 'rank' and self.ranking_version == 2
                                and self.loaded_catalog != call.params['catalog_revision'])
        if self.loading_catalog:
            self.send(request(self.current_id(), 'catalog', catalog_request(call.params)))
        else:
            self.send_current()

    def current_id(self):
        return self.generation + ':' + str(self.current.revision) + (':catalog' if self.loading_catalog else '')

    def send_current(self):
        call = self.current
        params = query_request(call.params) if call.method == 'rank' and self.ranking_version == 2 else call.params
        self.send(request(self.current_id(), call.method, params))

    def cancel_service(self):
        if self.service:
            self.runtime.jobs.cancel(self.service[1])
            self.service = None

    def cancel(self, drop_queue=True):
        if drop_queue and self.queued:
            self.queued.cancelled.set(); self.queued = None
        if self.current:
            self.current.cancelled.set()
            self.cancel_service()
            if not self.cancel_sent:
                self.cancel_sent = True
                self.send(request(None, 'cancel', {'id': self.current_id()}))
                self.cancel_timer.start(300)

    def retire(self):
        self.stopping = True
        self.cancel()
        if not self.current:
            if not self.initialized and self.process.state() == QProcess.ProcessState.Running:
                self.send(request(None, 'cancel', {'id': self.init_id}))
                self.cancel_timer.start(300)
            else:
                self.send(request(None, 'shutdown'))
                self.cancel_timer.start(300)

    def terminate(self):
        self.stopping = True
        self.deadline.stop(); self.cancel_timer.stop()
        self.cancel_service()
        self.framer.buffer.clear()
        if self.current:
            call, self.current = self.current, None
            call.finish(error='cancelled')
        self.process.terminate()  # Systemd supervises tools; direct rankers cannot fork or create threads.
        if self.direct and self.process.state() != QProcess.ProcessState.NotRunning and not self.kill_timer.isActive():
            self.kill_timer.start(300)

    def fail(self, error):
        if self.stopping:
            return
        self.stopping = True
        call = self.current or self.queued
        if call:
            if call is self.current:
                self.current = None
            else:
                self.queued = None
            call.finish(error=error)
        self.terminate()

    def traffic(self, size, stderr=False):
        if time.monotonic() - self.traffic_start >= 1:
            self.traffic_start, self.output_bytes, self.error_bytes = time.monotonic(), 0, 0
        if stderr:
            self.error_bytes += size
        else:
            self.output_bytes += size
        if self.error_bytes > 1024 * 1024 or self.output_bytes > 8 * MAX_FRAME:
            self.fail('Plugin output flood exceeded limits.')

    def read_error(self):
        self.traffic(self.process.readAllStandardError().size(), stderr=True)

    def read_output(self):
        if self.stopping:
            self.process.readAllStandardOutput()
            return
        try:
            while self.process.bytesAvailable() and not self.stopping:
                data = self.process.read(65536).data()
                self.traffic(len(data))
                for message in self.framer.feed(data):
                    if not self.stopping:
                        self.message(message)
        except (ValueError, TypeError, KeyError):
            self.fail('Plugin returned an invalid protocol response.')

    def message(self, message):
        if 'method' in message:
            self.host_service(message)
            return
        identity = message['id']
        if not self.initialized:
            if identity != self.init_id or 'error' in message:
                self.fail('Plugin startup failed. Check its v2 package and execution requirements.')
                return
            result = message.get('result')
            required = {'api_version', 'wire_version', 'plugin_id', 'has_handler'}
            if (not isinstance(result, dict) or not required <= set(result) or set(result) - required - {'ranking_version'}
                    or type(result.get('api_version')) is not int or result['api_version'] != API_VERSION
                    or type(result.get('wire_version')) is not int or result['wire_version'] != WIRE_VERSION
                    or type(result.get('has_handler')) is not bool or result.get('plugin_id') != self.plugin_id
                    or type(result.get('ranking_version', 1)) is not int or result.get('ranking_version', 1) not in (1, 2)):
                raise ProtocolError('Invalid initialization response')
            self.ranking_version = result.get('ranking_version', 1)
            self.initialized = True
            self.deadline.stop()
            self.next_call()
            return
        if self.current is None or identity != self.current_id():
            if identity not in self.recent_ids:
                raise ProtocolError('Unknown response ID')
            return
        if self.loading_catalog and 'error' not in message:
            result, call = message.get('result'), self.current
            if (not isinstance(result, dict) or set(result) != {'catalog_revision', 'count'}
                    or type(result['catalog_revision']) is not int
                    or result['catalog_revision'] != call.params['catalog_revision']
                    or type(result['count']) is not int or result['count'] != len(call.params['candidates'])):
                raise ProtocolError('Invalid catalog acknowledgement')
            self.loaded_catalog = result['catalog_revision']
            self.loading_catalog = False
            self.recent_ids = (self.recent_ids + [identity])[-32:]
            self.cancel_timer.stop()
            if call.cancelled.is_set() or not self.runtime.is_current(call):
                self.current = None
                self.deadline.stop()
                self.next_call()
            else:
                self.deadline.start(1000)
                self.send_current()
            return
        value = None
        if 'error' not in message and not self.current.cancelled.is_set():
            value = (validate_result(message['result'], self.current.params, self.current.known_ids) if self.current.method == 'rank'
                     else from_wire(message['result']))
        call, self.current = self.current, None
        self.recent_ids = (self.recent_ids + [identity])[-32:]
        self.deadline.stop(); self.cancel_timer.stop(); self.cancel_service()
        if not call.cancelled.is_set():
            if 'error' in message:
                self.stopping = True
                detail = message['error']['message']
                if call.method == 'handle_event':
                    detail += ' An operation may have completed; check before retrying.'
                call.finish(error=detail[:512])
                self.terminate()
            else:
                call.finish(value)
        if self.stopping:
            self.terminate()
        else:
            self.next_call()

    def host_service(self, message):
        params = message.get('params', {})
        call = self.current
        if (message['method'] != 'host.call' or set(params) != {'parent', 'name', 'args'} or self.service
                or call is None or call.method == 'rank' or params['parent'] != self.generation + ':' + str(call.revision)
                or call.cancelled.is_set() or not self.runtime.is_current(call)):
            self.send(response(message['id'], error='Host service request is no longer active.'))
            return
        try:
            if params['name'] == 'web.suggestions':
                args = params['args']
                if not isinstance(args, dict) or set(args) - {'query'} or not isinstance(args.get('query', ''), str):
                    raise ValueError('Invalid suggestion arguments')
                self.send(response(message['id'], []))
                return
            provider = service_request(params['name'], params['args'])
        except (TypeError, ValueError):
            self.send(response(message['id'], error='Unknown or invalid host service.'))
            return
        key = 'plugin-service:' + self.generation
        self.service = (message['id'], key)

        def completed(payload):
            if self.service != (message['id'], key):
                return
            self.service = None
            if self.current is not call or call.cancelled.is_set() or not self.runtime.is_current(call):
                return
            self.send(response(message['id'], error='Host service failed.') if payload.get('error')
                      else response(message['id'], payload.get('results', [])))
        self.runtime.jobs.builtin(key, provider, completed, 5000)

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.fail('Could not start the ranking child.' if self.direct else 'Could not start the plugin supervisor.')
            self.runtime.ended(self, 70 if self.direct else 78)

    def exited(self, code, status):
        self.stopping = True
        self.deadline.stop(); self.cancel_timer.stop(); self.kill_timer.stop(); self.cancel_service()
        if self.current:
            call, self.current = self.current, None
            call.finish(error='Plugin worker exited. An operation may have completed; check before retrying.')
        if not self.initialized and self.queued:
            call, self.queued = self.queued, None
            call.finish(error='Plugin child could not initialize.' if self.direct else
                        'External plugins require accessible systemd user support and a valid v2 package.')
        self.runtime.ended(self, (0 if code == 0 and status == QProcess.ExitStatus.NormalExit else 70) if self.direct else
                           78 if status == QProcess.ExitStatus.CrashExit else code)
