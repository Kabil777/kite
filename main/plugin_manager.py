"""Qt-free catalog and package management. Never import installed plugin code."""
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import io
from itertools import islice
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile

import yaml
from .plugin_api import API_VERSION, RESERVED_PLUGIN_IDS, _read_manifest, discover_plugins

# ponytail: fixed bounds and one global writer, not a dependency solver or daemon.
MAX_CATALOG = 2 * 1024 * 1024
MAX_ARCHIVE = 20 * 1024 * 1024
MAX_EXTRACTED = 100 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_STATE = 16 * 1024 * 1024
MAX_GUIDE = 128 * 1024
GITHUB_HOSTS = frozenset({'github.com', 'raw.githubusercontent.com',
                          'release-assets.githubusercontent.com', 'objects.githubusercontent.com'})


class ManagerError(ValueError):
    pass


def plugin_id(value, reserved=True):
    if (not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value)
            or reserved and value in RESERVED_PLUGIN_IDS):
        raise ManagerError('Invalid or reserved plugin ID')
    return value


def _text(value, limit=256):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ManagerError('Invalid catalog text')
    return value


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]{0,5})(\.(0|[1-9][0-9]{0,5})){2}', value):
        raise ManagerError('Package versions must be stable major.minor.patch')
    return value


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ManagerError('Invalid SHA-256')
    return value


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 512
            or '\\' in value or ':' in value or any(ord(c) < 32 for c in value)
            or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ManagerError('Unsafe package path')
    return value


def github_url(value, kind='download', redirect=False):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) <= 32 for c in value):
        raise ManagerError('Use a public HTTPS GitHub URL')
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.hostname not in GITHUB_HOSTS or url.username is not None
                or url.password is not None or url.port not in (None, 443) or url.fragment
                or url.query and not redirect):
            raise ManagerError('Use a public HTTPS GitHub URL without credentials')
    except ValueError as error:
        raise ManagerError('Invalid GitHub URL') from error
    parts = url.path.strip('/').split('/')
    if not redirect:
        if len(parts) < 2 or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', p) or p in ('.', '..') for p in parts[:2]):
            raise ManagerError('Invalid GitHub repository identity')
        if kind == 'catalog' and (url.hostname != 'raw.githubusercontent.com' or len(parts) < 4 or not url.path.endswith('.json')):
            raise ManagerError('Catalog must be a raw.githubusercontent.com JSON file')
        if kind == 'repository' and (url.hostname != 'github.com' or len(parts) != 2 or not all(parts)):
            raise ManagerError('Source must identify a GitHub owner/repository')
        if kind == 'artifact' and (url.hostname != 'github.com' or len(parts) != 6
                                   or parts[2:4] != ['releases', 'download'] or not url.path.lower().endswith('.zip')):
            raise ManagerError('Packages must be pinned GitHub Release ZIP assets')
    return value


class _GitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        github_url(newurl, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, limit, cancelled=lambda: False):
    """Anonymous HTTPS only; redirect URLs (which may be signed) are never stored."""
    github_url(url)
    deadline = time.monotonic() + 60
    chunks, total = [], 0
    try:
        request = Request(url, headers={'User-Agent': 'KITE-plugin-manager/1', 'Accept-Encoding': 'identity'})
        with build_opener(_GitHubRedirect()).open(request, timeout=5) as response:
            github_url(response.geturl(), redirect=True)
            while True:
                if cancelled() or time.monotonic() > deadline:
                    raise ManagerError('Download cancelled or timed out')
                chunk = response.read(min(65536, limit + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ManagerError('Download exceeded its size limit')
                chunks.append(chunk)
    except ManagerError:
        raise
    except OSError as error:
        raise ManagerError('GitHub download failed; check connectivity and retry') from error
    return b''.join(chunks)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManagerError('Duplicate JSON key')
        result[key] = value
    return result


def _decode(data):
    def invalid(_):
        raise ManagerError('Non-finite JSON value')
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ManagerError('Invalid JSON data') from error


def _read_json(path, limit=MAX_STATE):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ManagerError('Manager metadata must be an owned regular file')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ManagerError('Manager metadata exceeded its size limit')
    return _decode(data)


def _private(directory):
    missing, parent = [], directory
    while not parent.exists():
        if parent.is_symlink():
            raise ManagerError('Manager directory is a broken link')
        missing.append(parent)
        parent = parent.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700, exist_ok=True)
        _sync(path.parent)
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != os.getuid():
        raise ManagerError('Manager directory is not an owned directory')
    directory.chmod(0o700)
    _sync(directory)


def _sync(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path, value):
    _private(path.parent)
    if path.is_symlink():
        raise ManagerError('Refusing a symlink as manager metadata')
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(data) > MAX_STATE:
        raise ManagerError('Manager metadata exceeded its size limit')
    descriptor, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_catalog(data):
    if (not isinstance(data, dict) or set(data) != {'schema', 'plugins'} or type(data['schema']) is not int
            or data['schema'] != 1 or not isinstance(data['plugins'], list) or len(data['plugins']) > 1000):
        raise ManagerError('Unsupported or invalid catalog')
    seen = set()
    for item in data['plugins']:
        required = {'id', 'name', 'summary', 'author', 'repository', 'latest', 'releases'}
        if not isinstance(item, dict) or not required <= set(item) or set(item) - required - {'requirements', 'commands'}:
            raise ManagerError('Invalid catalog entry')
        identifier = plugin_id(item['id'])
        if identifier in seen:
            raise ManagerError('Duplicate catalog plugin ID')
        seen.add(identifier)
        for field, limit in (('name', 128), ('summary', 512), ('author', 128)):
            _text(item[field], limit)
        github_url(item['repository'], 'repository')
        _version(item['latest'])
        for field in ('requirements', 'commands'):
            values = item.get(field, [])
            if not isinstance(values, list) or len(values) > 20:
                raise ManagerError('Invalid dependency information')
            for value in values:
                _text(value, 200)
                if field == 'commands' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}', value):
                    raise ManagerError('Commands must be executable names, never shell instructions')
        releases = item['releases']
        if not isinstance(releases, list) or not 1 <= len(releases) <= 100:
            raise ManagerError('Invalid release list')
        versions = set()
        for release in releases:
            if not isinstance(release, dict) or set(release) != {'version', 'api_version', 'url', 'sha256'}:
                raise ManagerError('Invalid release metadata')
            version = _version(release['version'])
            if version in versions:
                raise ManagerError('Duplicate release version')
            versions.add(version)
            if type(release['api_version']) is not int or not 1 <= release['api_version'] <= 100:
                raise ManagerError('Invalid plugin API version')
            github_url(release['url'], 'artifact')
            if urlsplit(release['url']).path.casefold().split('/')[1:3] != urlsplit(item['repository']).path.casefold().strip('/').split('/'):
                raise ManagerError('Release artifact and source repository must have the same identity')
            _digest(release['sha256'])
        if item['latest'] not in versions:
            raise ManagerError('Latest version is absent from the release list')
    if len(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_CATALOG:
        raise ManagerError('Catalog exceeds 2 MiB')
    return data


def _validate_record(record):
    if not isinstance(record, dict) or set(record) != {'version', 'sha256', 'repository', 'catalog_url', 'files', 'directories', 'commands'}:
        raise ManagerError('Invalid ownership record')
    _version(record['version']); _digest(record['sha256'])
    github_url(record['repository'], 'repository'); github_url(record['catalog_url'], 'catalog')
    if (not isinstance(record['files'], dict) or not isinstance(record['directories'], list)
            or len(record['files']) + len(record['directories']) > MAX_MEMBERS
            or 'plugin.json' not in record['files']):
        raise ManagerError('Invalid file inventory')
    for path, digest in record['files'].items():
        _relative(path); _digest(digest)
    for path in record['directories']:
        _relative(path)
    if len(set(record['directories'])) != len(record['directories']) or set(record['directories']) & record['files'].keys():
        raise ManagerError('Conflicting inventory paths')
    if any(str(parent) not in record['directories'] for path in (*record['files'], *record['directories'])
           for parent in PurePosixPath(path).parents if str(parent) != '.'):
        raise ManagerError('Inventory has missing/conflicting parent directories')
    if not isinstance(record['commands'], list) or len(record['commands']) > 20:
        raise ManagerError('Invalid command requirements')
    if any(not isinstance(c, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}', c) for c in record['commands']):
        raise ManagerError('Invalid command requirement')


def _lockfile(path):
    _private(path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(descriptor)
        raise ManagerError('Invalid manager lock file')
    os.fchmod(descriptor, 0o600)
    return descriptor


@contextmanager
def mutation_lock(directory):
    descriptor = _lockfile(directory / 'mutation.lock')
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ManagerError('Another plugin operation is in progress; retry shortly') from error
        yield
    finally:
        os.close(descriptor)


class UseLease:
    """One process-owned lease; worker supervisors hold independent shared leases."""
    def __init__(self, directory, shared=True):
        self.path = directory / 'use.lock'
        self.fd = _lockfile(self.path)
        try:
            fcntl.flock(self.fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError as error:
            self.close()
            raise ManagerError('Close KITE and wait for plugin cleanup before changing packages') from error

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    @contextmanager
    def exclusive(self):
        try:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                # flock conversions can drop SH on failure. Restore it under mutation.lock.
                fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                raise ManagerError('Plugin cleanup is still pending; close KITE and retry') from error
            yield
        finally:
            fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)


def unit_empty(unit):
    if not re.fullmatch(r'kite-plugin-[0-9]+-[0-9a-f]{32}(\.service)?', unit):
        return False
    try:
        result = subprocess.run(['systemctl', '--user', 'show', unit, '--property=ActiveState', '--property=ControlGroup'],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=2)
        properties = dict(line.split(b'=', 1) for line in result.stdout.splitlines() if b'=' in line)
        if result.returncode or properties.get(b'ActiveState') not in (b'inactive', b'failed'):
            return False
        group = properties.get(b'ControlGroup', b'').decode()
        if not group:
            return True
        base = Path('/sys/fs/cgroup').resolve()
        directory = (base / group.lstrip('/')).resolve()
        return directory.is_relative_to(base) and (not directory.exists() or 'populated 0' in (directory / 'cgroup.events').read_text())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def units_clear(allowed=()):
    """A dead supervisor/absent flock does not prove its systemd unit is empty."""
    try:
        result = subprocess.run(['systemctl', '--user', 'list-units', '--all', '--no-pager', '--output=json',
                                 'kite-plugin-*.service'], stdin=subprocess.DEVNULL, capture_output=True, timeout=2)
        if result.returncode or len(result.stdout) > MAX_CATALOG:
            return False
        units = _decode(result.stdout)
        if not isinstance(units, list) or len(units) > 1000:
            return False
        deadline = time.monotonic() + 5
        for unit in units:
            if (time.monotonic() > deadline or not isinstance(unit, dict) or not isinstance(unit.get('unit'), str)
                    or unit['unit'].removesuffix('.service') not in allowed and not unit_empty(unit['unit'])):
                return False
        return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _file_hash(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EXTRACTED:
            raise ManagerError('Payload contains a nonregular or oversized file')
        digest, total = hashlib.sha256(), 0
        while chunk := stream.read(65536):
            total += len(chunk)
            if total > MAX_EXTRACTED:
                raise ManagerError('Payload changed size while inspecting it')
            digest.update(chunk)
    return digest.hexdigest()


def _paths(directory, record=None):
    files, directories, pending = set(), set(), [directory]
    if directory.is_symlink() or not directory.is_dir():
        raise ManagerError('Payload is not a regular directory')
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink():
                    raise ManagerError('Payload contains a link')
                name, is_dir = path.relative_to(directory).as_posix(), entry.is_dir(follow_symlinks=False)
                if record is not None and name not in (record['directories'] if is_dir else record['files']):
                    raise ManagerError('Payload has unknown extras; retain without reading them')
                (directories if is_dir else files).add(name)
                if len(files) + len(directories) > MAX_MEMBERS:
                    raise ManagerError('Payload has too many entries')
                if is_dir:
                    pending.append(path)
    return files, directories


def read_guide(directory):
    for name in ('HOWTO.md', 'README.md'):
        path = Path(directory) / name
        if not os.path.lexists(path):
            continue
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ManagerError('Guide must be a regular UTF-8 file')
            content = stream.read(MAX_GUIDE + 1)
        if len(content) > MAX_GUIDE:
            raise ManagerError('Plugin guide exceeds 128 KiB')
        return content.decode('utf-8')
    return ''


def _unchanged(directory, record):
    try:
        files, dirs = _paths(directory, record)
        # Unknown extras are never opened or hashed, even when they are links to external data.
        return (files == record['files'].keys() and dirs == set(record['directories'])
                and all(_file_hash(directory / path) == digest for path, digest in record['files'].items()))
    except (OSError, ValueError):
        return False


class Manager:
    def __init__(self, config_path=None, cancelled=lambda: False):
        def xdg(name, fallback):
            path = Path(os.environ.get(name, str(Path.home() / fallback)))
            if not path.is_absolute():
                raise ManagerError('XDG paths must be absolute')
            return path.resolve()
        self.data = (xdg('XDG_DATA_HOME', '.local/share') / 'kite').resolve()
        self.authored = xdg('XDG_CONFIG_HOME', '.config') / 'kite/plugin'
        self.config_path = Path(config_path) if config_path is not None else self.authored.parent / 'config.yaml'
        self.state = xdg('XDG_STATE_HOME', '.local/state') / 'kite/plugin-manager'
        self.cache = xdg('XDG_CACHE_HOME', '.cache') / 'kite/plugin-manager/catalog.json'
        self.root, self.store = self.data / 'plugins', self.data / 'plugin-packages'
        self.staging, self.backups = self.data / '.plugin-staging', self.data / 'plugin-backups'
        self.cancelled = cancelled

    def registry(self):
        try:
            data = _read_json(self.state / 'registry.json')
            if data is None:
                if (self.state / 'registry.json').exists():
                    raise ManagerError('Null registry is invalid')
                return {'schema': 1, 'catalog_url': '', 'enabled': {}, 'installed': {}}
            if (not isinstance(data, dict) or set(data) != {'schema', 'catalog_url', 'enabled', 'installed'}
                    or type(data['schema']) is not int or data['schema'] != 1):
                raise ManagerError('Invalid registry schema')
            if data['catalog_url']:
                github_url(data['catalog_url'], 'catalog')
            elif data['catalog_url'] != '':
                raise ManagerError('Invalid catalog source')
            if not isinstance(data['enabled'], dict) or len(data['enabled']) > 1000:
                raise ManagerError('Invalid enablement records')
            for identifier, enabled in data['enabled'].items():
                plugin_id(identifier, reserved=False)
                if type(enabled) is not bool:
                    raise ManagerError('Invalid enablement flag')
            if not isinstance(data['installed'], dict) or len(data['installed']) > 100:
                raise ManagerError('Too many installed packages')
            for identifier, record in data['installed'].items():
                plugin_id(identifier)
                _validate_record(record)
            return data
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ManagerError('Plugin registry is invalid; preserved unchanged. Repair it before managing or opening managed plugins.') from error

    def yaml_flags(self):
        try:
            descriptor = os.open(self.config_path, os.O_RDONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            return {}
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ManagerError('Configuration must be a regular file')
            raw = stream.read(262145)
        if len(raw) > 262144:
            raise ManagerError('Configuration is too large to inspect safely')
        try:
            config = yaml.safe_load(raw) or {}
            settings = config.get('plugins', {})
            if not isinstance(settings, dict) or len(settings) > 100:
                raise ValueError('Invalid plugin settings')
            flags = {}
            for identifier, options in settings.items():
                plugin_id(identifier, reserved=False)
                if not isinstance(options, dict) or 'enabled' in options and type(options['enabled']) is not bool:
                    raise ValueError('Invalid plugin settings')
                flags[identifier] = options.get('enabled', True)
            return flags
        except (ValueError, TypeError, AttributeError, yaml.YAMLError) as error:
            raise ManagerError('Cannot read plugin enablement from YAML; the file was not changed') from error

    def payload(self, identifier, record):
        return self.store / _digest(record['sha256']) / plugin_id(identifier)

    def owns(self, identifier, record):
        link = self.root / identifier
        payload = self.payload(identifier, record)
        return (link.is_symlink() and os.readlink(link) == str(payload.absolute())
                and not any(path.is_symlink() for path in (self.root, self.store, payload.parent, payload)))

    def catalog(self, refresh=False):
        source = self.registry()['catalog_url']
        if not source:
            raise ManagerError('Configure a reviewed GitHub catalog URL first')
        cached = None
        try:
            data = _read_json(self.cache, MAX_CATALOG + 16384)
            if isinstance(data, dict) and data.get('source') == source:
                cached = validate_catalog(data['catalog'])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if not refresh and cached is not None:
            return {**deepcopy(cached), 'catalog_url': source}, False
        try:
            catalog = validate_catalog(_decode(download(source, MAX_CATALOG, self.cancelled)))
        except ManagerError:
            if cached is None or self.cancelled():
                raise
            return {**deepcopy(cached), 'catalog_url': source}, True
        _atomic_json(self.cache, {'source': source, 'catalog': catalog, 'fetched_at': int(time.time())})
        return {**deepcopy(catalog), 'catalog_url': source}, False

    def release(self, identifier, catalog=None):
        plugin_id(identifier)
        if catalog is None:
            catalog, _ = self.catalog()
        for item in catalog['plugins']:
            if item['id'] == identifier:
                release = next(r for r in item['releases'] if r['version'] == item['latest'])
                return deepcopy({**item, **release, 'catalog_url': catalog['catalog_url']})
        raise ManagerError('Plugin is not in the configured reviewed catalog')

    def inventory(self):
        with mutation_lock(self.store):
            if self._has_journal():
                raise ManagerError('Package recovery is pending; use Check cleanup / recover, or close KITE and retry a CLI mutation')
            return self._inventory()

    def _inventory(self):
        registry = self.registry()
        flags = self.yaml_flags()
        rows, identifiers = [], defaultdict(set)
        for root in (self.authored, self.root):
            if not root.exists():
                continue
            entries = list(islice(root.iterdir(), 1001))
            if len(entries) > 1000:
                raise ManagerError('Plugin directory exceeds 1000 entries')
            for directory in sorted(entries):
                if not directory.is_dir() and not directory.is_symlink():
                    continue
                if not (directory / 'plugin.json').exists() and directory.name not in registry['installed']:
                    continue
                identifier, error = directory.name, ''
                try:
                    manifest = _read_manifest(directory)
                    if identifier in RESERVED_PLUGIN_IDS:
                        raise ManagerError('ID is reserved for a native tool')
                    name = manifest['name']
                except (OSError, ValueError, RuntimeError) as exc:
                    name, error = identifier, str(exc)[:240]
                record = registry['installed'].get(identifier)
                managed = bool(record and directory == self.root / identifier and self.owns(identifier, record))
                enabled = flags.get(identifier, True) and registry['enabled'].get(identifier, True)
                missing = [c for c in record['commands'] if not shutil.which(c)] if managed else []
                if directory.resolve().is_relative_to(self.store.resolve()) and not managed:
                    error = error or 'Untracked managed payload; restore its ownership record before activation'
                    enabled = False
                status = ('Incompatible' if error.startswith('Unsupported plugin API;') else 'Broken' if error else 'Disabled by configuration' if not flags.get(identifier, True)
                          else 'Disabled' if not enabled else 'Needs setup' if missing else 'Enabled')
                rows.append({'id': identifier, 'name': name, 'directory': str(directory), 'managed': managed,
                             'enabled': bool(enabled and not error), 'version': record['version'] if managed else '',
                             'status': status, 'error': error, 'missing': missing})
                identifiers[identifier].add(str(directory.resolve()))
        for identifier, record in registry['installed'].items():
            if not any(r['id'] == identifier and r['managed'] for r in rows):
                rows.append({'id': identifier, 'name': identifier, 'directory': str(self.root / identifier),
                             'managed': False, 'enabled': False, 'version': record['version'],
                             'status': 'Ownership changed', 'error': 'Managed link is missing or changed; files were preserved', 'missing': []})
        for row in rows:
            if len(identifiers[row['id']]) > 1:
                row.update(status='Conflicting packages', enabled=False, error='Distinct packages claim the same ID')
        return rows

    def _activation_directory(self, identifier, directory, registry, flags):
        if not flags.get(identifier, True) or not registry['enabled'].get(identifier, True):
            raise ManagerError('Plugin is disabled (YAML and manager permission are both required)')
        record = registry['installed'].get(identifier)
        if record:
            payload = self._safe_payload(identifier, record).absolute()
            if not self.owns(identifier, record) or directory.resolve() != payload:
                raise ManagerError('Managed ownership/payload changed; activation was blocked')
            return payload
        if directory.resolve().is_relative_to(self.store.resolve()):
            raise ManagerError('Untracked managed payload; activation was blocked')
        return directory.absolute()

    def activation_directory(self, identifier, directory):
        if self._has_journal():
            raise ManagerError('Package recovery is required before activation')
        return self._activation_directory(identifier, Path(directory), self.registry(), self.yaml_flags())

    def discover(self, config, on_error):
        if self._has_journal():
            raise ManagerError('Package recovery is required before discovery')
        registry, flags = self.registry(), self.yaml_flags()
        effective = deepcopy(config)
        for identifier in flags.keys() | registry['enabled'].keys():
            if not flags.get(identifier, True) or not registry['enabled'].get(identifier, True):
                effective.setdefault(identifier, {})['enabled'] = False
        found = []
        for manifest, directory in discover_plugins(roots=(self.authored, self.root), config=effective, on_error=on_error):
            try:
                found.append((manifest, self._activation_directory(manifest['id'], directory, registry, flags)))
            except (OSError, ValueError, RuntimeError) as error:
                on_error(manifest['id'], str(error))
        return found

    def _has_journal(self):
        return os.path.lexists(self.state / 'journal.json')

    @contextmanager
    def mutation(self, lease=None, payload=True):
        # Coordinate the actual data store, even if callers select different state homes.
        with mutation_lock(self.store):
            if lease is None:
                own = UseLease(self.store, shared=False)
                try:
                    if not units_clear():
                        raise ManagerError('Cannot confirm plugin unit cleanup. Close KITE and wait for cleanup; files were preserved.')
                    self._recover_locked()
                    yield self.registry()
                finally:
                    own.close()
            elif lease.path != self.store / 'use.lock' or lease.fd is None:
                raise ManagerError('Invalid application package-use lease')
            elif payload:
                with lease.exclusive():
                    if not units_clear():
                        raise ManagerError('Plugin unit cleanup is uncertain; files were preserved')
                    self._recover_locked()
                    yield self.registry()
            else:
                if self._has_journal():
                    raise ManagerError('Package recovery is pending; finish cleanup before changing settings')
                yield self.registry()

    def recover(self, lease=None):
        with self.mutation(lease):
            pass

    def set_source(self, source, lease=None):
        github_url(source, 'catalog')
        with self.mutation(lease, payload=False) as registry:
            registry['catalog_url'] = source
            _atomic_json(self.state / 'registry.json', registry)
        return 'Catalog source saved. Browse explicitly to fetch it.'

    def set_enabled(self, identifier, enabled, lease=None):
        plugin_id(identifier)
        if type(enabled) is not bool:
            raise ManagerError('Enablement must be true or false')
        with self.mutation(lease, payload=False) as registry:
            if not any(os.path.lexists(root / identifier) for root in (self.root, self.authored)) and identifier not in registry['installed']:
                raise ManagerError('Plugin is not installed')
            registry['enabled'][identifier] = enabled
            _atomic_json(self.state / 'registry.json', registry)
        return 'Enabled for lazy activation (an explicit YAML disable still wins).' if enabled else 'Disabled. Cleanup must finish before changing code.'

    def prepare(self, entry):
        """Stage a frozen Manager.release() entry for manual preview and later commit."""
        entry = deepcopy(entry)
        fields = {'id', 'name', 'summary', 'author', 'repository', 'latest', 'releases', 'commands', 'requirements'}
        validate_catalog({'schema': 1, 'plugins': [{k: v for k, v in entry.items() if k in fields}]})
        selected = next(r for r in entry['releases'] if r['version'] == entry['latest'])
        if (any(entry.get(k) != v for k, v in selected.items())
                or entry['catalog_url'] != self.registry()['catalog_url']):
            raise ManagerError('Catalog/source changed; review the package again')
        if entry['api_version'] != API_VERSION:
            raise ManagerError(f'Incompatible plugin API; KITE requires {API_VERSION}')
        archive = download(entry['url'], MAX_ARCHIVE, self.cancelled)
        if hashlib.sha256(archive).hexdigest() != entry['sha256']:
            raise ManagerError('Artifact checksum mismatch; nothing installed')
        _private(self.staging)
        temporary = Path(tempfile.mkdtemp(prefix='package-', dir=self.staging))
        directory = temporary / entry['id']
        try:
            total, seen, file_paths, expanded = 0, set(), set(), set()
            with zipfile.ZipFile(io.BytesIO(archive)) as package:
                members = package.infolist()
                if not 1 <= len(members) <= MAX_MEMBERS or sum(m.file_size for m in members) > MAX_EXTRACTED:
                    raise ManagerError('ZIP exceeds its member or extracted-size limit')
                for member in members:
                    if self.cancelled():
                        raise ManagerError('Staging cancelled; nothing installed')
                    name = _relative(member.filename.removesuffix('/'))
                    if (member.orig_filename != member.filename or name in seen
                            or name.split('/')[0] != entry['id'] or member.flag_bits & 1
                            or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                        raise ManagerError('ZIP has an unsafe, duplicate, encrypted or unsupported member')
                    seen.add(name)
                    mode = member.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or kind == stat.S_IFDIR and not member.is_dir():
                        raise ManagerError('ZIP links and special files are forbidden')
                    extra = member.extra
                    while extra:
                        if len(extra) < 4:
                            raise ManagerError('Malformed ZIP extra field')
                        tag, size = int.from_bytes(extra[:2], 'little'), int.from_bytes(extra[2:4], 'little')
                        if len(extra) < size + 4 or tag not in (0x0001, 0x5455, 0x7875, 0x5855, 0x7075):
                            raise ManagerError('Unsupported ZIP metadata (including link metadata)')
                        extra = extra[size + 4:]
                    parents = list(PurePosixPath(name).parents)[:-1]
                    expanded.update([name, *(str(p) for p in parents)])
                    if len(expanded) > MAX_MEMBERS + 1:
                        raise ManagerError('ZIP implicit directories exceed the entry limit')
                    if any(str(parent) in file_paths for parent in parents) or not member.is_dir() and any(p.startswith(name + '/') for p in seen):
                        raise ManagerError('Conflicting ZIP paths')
                    path = temporary / name
                    if member.is_dir():
                        path.mkdir(parents=True, mode=0o700, exist_ok=True)
                        continue
                    file_paths.add(name)
                    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    with package.open(member) as source, path.open('xb') as output:
                        while chunk := source.read(65536):
                            total += len(chunk)
                            if total > MAX_EXTRACTED or self.cancelled():
                                raise ManagerError('Extraction size limit exceeded or cancelled')
                            output.write(chunk)
                        os.fchmod(output.fileno(), 0o700 if mode & 0o111 else 0o600)
                        output.flush()
                        os.fsync(output.fileno())
            manifest = _read_manifest(directory)
            if manifest['id'] != entry['id'] or manifest['name'] != entry['name']:
                raise ManagerError('Manifest identity does not match the approved catalog entry')
            files, dirs = _paths(directory)
            record = {'version': entry['version'], 'sha256': entry['sha256'], 'repository': entry['repository'],
                      'catalog_url': entry['catalog_url'], 'files': {p: _file_hash(directory / p) for p in sorted(files)},
                      'directories': sorted(dirs), 'commands': entry.get('commands', [])}
            _validate_record(record)
            guide = read_guide(directory)
            for parent, _, _ in os.walk(temporary, topdown=False):
                _sync(Path(parent))
            _sync(self.staging)
            return {'entry': entry, 'record': record, 'directory': str(directory), 'guide': guide}
        except BaseException:
            shutil.rmtree(temporary)
            raise

    def discard(self, prepared):
        directory = Path(prepared['directory'])
        if directory.parent.parent != self.staging or not directory.parent.name.startswith('package-') or directory.parent.is_symlink():
            raise ManagerError('Invalid staging directory')
        if directory.parent.exists():
            try:
                shutil.rmtree(directory.parent)
            except OSError:
                return 'Private preview staging retained: ' + str(directory.parent)
        return ''

    def _safe_payload(self, identifier, record):
        path = self.payload(identifier, record)
        if any(p.is_symlink() for p in (self.store, path.parent, path)):
            raise ManagerError('Package-store links changed; refusing to follow them')
        return path

    def _references(self, payload, identifier):
        references = []
        for root in (self.authored, self.root):
            if not root.exists():
                continue
            entries = list(islice(root.iterdir(), 1001))
            if len(entries) > 1000:
                raise ManagerError('Plugin root has too many entries')
            for path in entries:
                if path == self.root / identifier:
                    continue
                canonical = path.resolve()
                if canonical == payload or canonical.is_relative_to(payload) or payload.is_relative_to(canonical):
                    references.append(path)
        return references

    def _constraints(self, identifier, before, after):
        if self.root.is_symlink() or self.root.exists() and self.root.stat().st_uid != os.getuid():
            raise ManagerError('Installed plugin root ownership changed; it was preserved')
        authored = self.authored / identifier
        if os.path.lexists(authored):
            raise ManagerError('Authored/development package conflicts with this ID; it will not be changed')
        link = self.root / identifier
        if before is None:
            if os.path.lexists(link):
                raise ManagerError('Existing package/link is not manager-owned; adoption is not supported')
        elif not self.owns(identifier, before):
            raise ManagerError('Managed link changed; refusing to replace or delete it')
        for record in (before, after):
            if record and self._references(self._safe_payload(identifier, record), identifier):
                raise ManagerError('A development/other package references this payload; preserve or unlink that reference first')
        packages = set()
        for root in (self.authored, self.root):
            if root.exists():
                for path in islice(root.iterdir(), 1001):
                    if path.is_dir() and (path / 'plugin.json').exists():
                        packages.add(path.resolve())
        if before is None and after is not None and len(packages) >= 100:
            raise ManagerError('External plugins exceed the 100-package limit')

    def _publish(self, identifier, record, directory, before):
        _private(self.store)
        payload = self._safe_payload(identifier, record)
        _private(payload.parent)
        self.root.mkdir(parents=True, exist_ok=True)
        _sync(self.data)
        if self.root.is_symlink() or self.root.stat().st_uid != os.getuid():
            raise ManagerError('Installed plugin root must be an owned directory, not a link')
        if len({p.stat().st_dev for p in (self.root, self.store, self.staging)}) != 1:
            raise ManagerError('Plugin roots, staging and package store must share a filesystem')
        if payload.exists():
            if not before or before['sha256'] != record['sha256'] or not self.owns(identifier, before):
                raise ManagerError('Untracked hash payload exists; settle its ownership/recovery before retrying')
            if not _unchanged(payload, record):
                raise ManagerError('Existing hash payload was modified; preserve/uninstall it before reinstalling')
        else:
            os.rename(directory, payload)
            _sync(directory.parent)
            _sync(payload.parent)
            _sync(self.store)
        return payload

    def _pointer_matches(self, identifier, record):
        return self.owns(identifier, record) if record else not os.path.lexists(self.root / identifier)

    def _point(self, identifier, record):
        if self.root.is_symlink() or not self.root.is_dir() or self.root.stat().st_uid != os.getuid():
            raise ManagerError('Installed plugin root changed; refusing pointer recovery')
        link = self.root / identifier
        if record is None:
            link.unlink(missing_ok=True)
        else:
            _private(self.staging)
            temporary = self.staging / ('link-' + uuid.uuid4().hex)
            try:
                temporary.symlink_to(self._safe_payload(identifier, record).absolute())
                os.replace(temporary, link)
            finally:
                temporary.unlink(missing_ok=True)
        _sync(self.root)

    @staticmethod
    def _apply(registry, identifier, record, enabled):
        if record is None:
            registry['installed'].pop(identifier, None)
        else:
            registry['installed'][identifier] = record
        if enabled is None:
            registry['enabled'].pop(identifier, None)
        else:
            registry['enabled'][identifier] = enabled

    def _clean(self, identifier, record):
        payload = self._safe_payload(identifier, record)
        if not payload.exists():
            return []
        if self._references(payload, identifier):
            return ['Retained referenced payload: ' + str(payload)]
        _private(self.backups)
        retained = self.backups / f'{identifier}-{record["version"]}-{uuid.uuid4().hex}'
        os.rename(payload, retained)
        _sync(payload.parent)
        _sync(self.backups)
        if _unchanged(retained, record):
            shutil.rmtree(retained)
            _sync(self.backups)
            notes = []
        else:
            notes = ['Retained edited/extra files: ' + str(retained)]
        try:
            payload.parent.rmdir()
            _sync(self.store)
        except OSError:
            pass  # Other store content is never adopted or recursively removed.
        return notes

    def _journal(self):
        journal = _read_json(self.state / 'journal.json')
        fields = {'schema', 'id', 'before', 'after', 'enabled_before', 'enabled_after', 'phase'}
        if (not isinstance(journal, dict) or set(journal) != fields or type(journal['schema']) is not int
                or journal['schema'] != 1 or journal['phase'] not in ('intent', 'committed')):
            raise ManagerError('Invalid recovery journal; it was preserved')
        plugin_id(journal['id'])
        if journal['before'] is None and journal['after'] is None:
            raise ManagerError('Empty recovery transaction')
        for field in ('before', 'after'):
            if journal[field] is not None:
                _validate_record(journal[field])
                self._safe_payload(journal['id'], journal[field])
        if any(v is not None and type(v) is not bool for v in (journal['enabled_before'], journal['enabled_after'])):
            raise ManagerError('Invalid journal enablement')
        return journal

    def _recover_locked(self):
        if not self._has_journal():
            return []
        journal, registry = self._journal(), self.registry()
        identifier = journal['id']
        current, flag = registry['installed'].get(identifier), registry['enabled'].get(identifier)
        if (current not in (journal['before'], journal['after']) or flag not in (journal['enabled_before'], journal['enabled_after'])
                or not any(self._pointer_matches(identifier, journal[key]) for key in ('before', 'after'))):
            raise ManagerError('Journal conflicts with current ownership; everything was preserved for repair')
        if journal['phase'] == 'committed':
            if current != journal['after'] or flag != journal['enabled_after'] or not self._pointer_matches(identifier, current):
                raise ManagerError('Committed transaction is inconsistent; refusing recovery')
            discarded, kept = journal['before'], journal['after']
        else:
            self._point(identifier, journal['before'])
            self._apply(registry, identifier, journal['before'], journal['enabled_before'])
            _atomic_json(self.state / 'registry.json', registry)
            discarded, kept = journal['after'], journal['before']
        notes = []
        if discarded and (kept is None or discarded['sha256'] != kept['sha256']):
            notes = self._clean(identifier, discarded)
        (self.state / 'journal.json').unlink()
        _sync(self.state)
        return notes

    def _transact(self, registry, identifier, after, enabled):
        journal = {'schema': 1, 'id': identifier, 'before': registry['installed'].get(identifier), 'after': after,
                   'enabled_before': registry['enabled'].get(identifier), 'enabled_after': enabled, 'phase': 'intent'}
        _atomic_json(self.state / 'journal.json', journal)
        try:
            self._point(identifier, after)
            self._apply(registry, identifier, after, enabled)
            _atomic_json(self.state / 'registry.json', registry)
            _atomic_json(self.state / 'journal.json', {**journal, 'phase': 'committed'})
            return self._recover_locked()
        except Exception as error:
            try:
                committed = self._journal()['phase'] == 'committed'
                notes = self._recover_locked()
            except Exception as recovery_error:
                raise ManagerError('Package operation needs recovery; files/journal preserved. Close KITE and retry.') from recovery_error
            if committed:
                return notes + ['Commit completed; recovered interrupted housekeeping.']
            raise ManagerError('Operation failed; the previous installation was restored') from error

    def install(self, prepared, update=False, lease=None):
        entry, record = deepcopy(prepared['entry']), deepcopy(prepared['record'])
        identifier, directory = plugin_id(entry['id']), Path(prepared['directory'])
        _validate_record(record)
        if (directory.parent.parent != self.staging or directory.name != identifier or directory.parent.is_symlink()
                or not _unchanged(directory, record) or _read_manifest(directory)['name'] != entry['name']
                or any(record[k] != entry[k] for k in ('version', 'sha256', 'repository', 'catalog_url'))):
            raise ManagerError('Staged identity or contents changed; review again')
        if self.cancelled():
            raise ManagerError('Cancelled before commit; nothing installed')
        with self.mutation(lease) as registry:
            if registry['catalog_url'] != entry['catalog_url']:
                raise ManagerError('Catalog source changed; review again')
            before = registry['installed'].get(identifier)
            if update != (before is not None):
                raise ManagerError('Use update for an owned installation; unmanaged packages cannot be adopted')
            if before:
                if any(before[k] != record[k] for k in ('repository', 'catalog_url')):
                    raise ManagerError('Publisher/catalog identity changed; automatic migration is forbidden')
                if before['version'] == record['version']:
                    raise ManagerError('Already installed' if before['sha256'] == record['sha256'] else 'Published artifact changed without a new version; refusing it')
            self._constraints(identifier, before, record)
            self._publish(identifier, record, directory, before)
            notes = self._transact(registry, identifier, record, registry['enabled'].get(identifier) if update else True)
        return {'message': ('Updated ' if update else 'Installed ') + identifier + ' ' + record['version'] + '. Code starts only on demand.', 'retained': notes}

    def uninstall(self, identifier, lease=None):
        plugin_id(identifier)
        with self.mutation(lease) as registry:
            before = registry['installed'].get(identifier)
            if before is None:
                raise ManagerError('Not manager-owned; authored/manual packages will not be removed')
            self._constraints(identifier, before, None)
            notes = self._transact(registry, identifier, None, registry['enabled'].get(identifier))
        return {'message': 'Uninstalled ' + identifier + '. YAML and external data were preserved.', 'retained': notes}


def cli(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog='kite plugins', description='Manage trusted plugins without loading Qt')
    parser.add_argument('--config', type=Path, help='YAML file whose explicit plugin disables must be respected')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='Installed metadata; running state is only available in-app')
    source = commands.add_parser('source', help='Configure a reviewed public GitHub raw JSON catalog')
    source.add_argument('url')
    search = commands.add_parser('search', help='Explicitly fetch/search the catalog; fall back to a labeled cache')
    search.add_argument('query', nargs='?', default='')
    for command in ('info', 'install', 'enable', 'disable', 'update', 'uninstall'):
        subparser = commands.add_parser(command)
        subparser.add_argument('id')
        if command in ('install', 'update', 'uninstall'):
            subparser.add_argument('--yes', action='store_true', help='Approve this operation, not a validation bypass')
    args = parser.parse_args(argv)
    manager, prepared = None, None

    def plain(text):
        return ''.join(c for c in str(text) if c.isprintable() or c in '\n\t')

    def approve(message):
        print(plain(message))
        if args.yes:
            return
        if not sys.stdin.isatty():
            raise ManagerError('Noninteractive mutation requires --yes')
        if input('Approve? [y/N] ').strip().lower() not in ('y', 'yes'):
            raise ManagerError('Not approved; nothing committed')

    try:
        manager = Manager(args.config)
        if args.command == 'source':
            print(manager.set_source(args.url))
        elif args.command == 'list':
            for row in manager.inventory():
                print(plain(f'{row["id"]}\t{row["version"] or "unmanaged"}\t{row["status"]}\t{row["error"]}'))
                if row['missing']:
                    print('  Missing commands (install manually): ' + ', '.join(row['missing']))
            print('Running state is available in-app; listing never starts plugins.')
        elif args.command == 'search':
            catalog, offline = manager.catalog(refresh=True)
            if offline:
                print('Using cached catalog; refresh failed.', file=sys.stderr)
            query = args.query.casefold()
            for entry in catalog['plugins']:
                if query in ' '.join(entry[k] for k in ('id', 'name', 'summary', 'author')).casefold():
                    release = manager.release(entry['id'], catalog)
                    print(plain(f'{entry["id"]}\t{release["version"]}\tAPI {release["api_version"]}\t{entry["summary"]}'))
        elif args.command == 'info':
            rows = [row for row in manager.inventory() if row['id'] == args.id]
            for row in rows:
                print(plain(json.dumps(row, ensure_ascii=False, indent=2)))
            record = manager.registry()['installed'].get(args.id)
            if record:
                print('Recorded ownership:')
                print(plain(json.dumps({k: record[k] for k in ('version', 'sha256', 'repository', 'catalog_url', 'commands')}, indent=2)))
            try:
                print(plain(json.dumps(manager.release(args.id), ensure_ascii=False, indent=2)))
            except ManagerError:
                if not rows:
                    raise
        elif args.command in ('enable', 'disable'):
            print(manager.set_enabled(args.id, args.command == 'enable'))
        else:
            # Detect an open app before network/staging; the commit rechecks after approval.
            manager.recover()
            if not args.yes and not sys.stdin.isatty():
                raise ManagerError('Noninteractive mutation requires --yes')
            if args.command == 'uninstall':
                approve(f'Uninstall manager-owned code for {plugin_id(args.id)}? YAML and external data remain.')
                result = manager.uninstall(args.id)
            else:
                catalog, offline = manager.catalog(refresh=True)
                if offline:
                    print('Using cached catalog; refresh failed.', file=sys.stderr)
                entry = manager.release(args.id, catalog)
                prepared = manager.prepare(entry)
                print(plain(prepared['guide'] or '(No packaged manual)'))
                approve(f'{args.command.title()} {entry["id"]} {entry["version"]}\nAuthor: {entry["author"]}\n'
                        f'Source: {entry["repository"]}\nCatalog: {entry["catalog_url"]}\nArtifact: {entry["url"]}\n'
                        f'SHA-256: {entry["sha256"]}\nRequirements (manual): {entry.get("requirements", [])}\n'
                        'Plugins run with your full user permissions. Systemd limits failures, NOT access to files, secrets or network. '
                        'A checksum is not a safety review. No dependencies will be installed.')
                result = manager.install(prepared, update=args.command == 'update')
            print(result['message'])
            for note in result['retained']:
                print(note)
        return 0
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        print('KITE plugins: ' + plain(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('Interrupted before approval/commit; any journal records the actual committed outcome.', file=sys.stderr)
        return 130
    finally:
        if prepared is not None and manager is not None:
            note = manager.discard(prepared)
            if note:
                print(note, file=sys.stderr)
