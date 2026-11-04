"""Install KITE's first-party prebuilt ranker; never compile or run downloaded code."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import tempfile
import tomllib
import uuid

REPOSITORY = 'Kabil777/kite'
BINARY = 'kite-flea-ranking'
NOTICES = 'THIRD-PARTY-NOTICES.txt'
GUIDES = ('README.md', 'HOWTO.md', 'PROVENANCE.md', 'FLEA-PROVENANCE.md', 'LICENSE-FLEA')
LIMITS = {'binary': 16 * 1024 * 1024, 'notices': 8 * 1024 * 1024}
PLATFORMS = {'linux-x86_64': ('x86_64-unknown-linux-gnu', 62),
             'linux-aarch64': ('aarch64-unknown-linux-gnu', 183)}


def version(root):
    value = tomllib.loads((Path(root) / 'pyproject.toml').read_text())['project']['version']
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})', value):
        raise ValueError('Invalid KITE release version')
    return value


def source_digest(root):
    package = Path(root) / 'examples/rust-ranking'
    files = [package / name for name in ('Cargo.toml', 'Cargo.lock', 'plugin.json')]
    files += sorted((package / 'src').rglob('*.rs'))
    digest = hashlib.sha256()
    for path in sorted(files):
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Invalid native source file')
        digest.update(path.relative_to(package).as_posix().encode() + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def platform_key():
    machine = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(platform.machine().lower(), platform.machine().lower())
    key = platform.system().lower() + '-' + machine
    if key not in PLATFORMS:
        raise ValueError('No prebuilt native engine for this platform; use --python-only')
    return key


def asset_names(release, key):
    return {'binary': f'kite-flea-ranking-v{release}-{key}',
            'notices': f'kite-native-notices-v{release}-{key}.txt'}


def fetch(url, limit):
    from .plugin_manager import download
    return download(url, limit)


def read_bytes(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
            raise ValueError('Native release files must be bounded, owned regular files')
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('Native release file exceeds its limit')
    return value


def manifest(source, key):
    from .plugin_manager import _decode
    release = version(source)
    path = source / 'native-assets.json'
    raw = read_bytes(path, 65536) if path.exists() or path.is_symlink() else fetch(
        f'https://github.com/{REPOSITORY}/releases/download/v{release}/native-assets.json', 65536)
    data = _decode(raw)
    if (not isinstance(data, dict) or set(data) != {'schema', 'version', 'source_commit', 'source_sha256', 'platforms'}
            or type(data['schema']) is not int or data['schema'] != 1 or data['version'] != release
            or not isinstance(data['source_commit'], str) or not re.fullmatch('[a-f0-9]{40}', data['source_commit'])
            or data['source_sha256'] != source_digest(source) or not isinstance(data['platforms'], dict)
            or set(data['platforms']) - PLATFORMS.keys() or key not in data['platforms']):
        raise ValueError('Native release does not match this source/platform; use a released checkout or --python-only')
    record = data['platforms'][key]
    if (not isinstance(record, dict) or set(record) != {'target', 'glibc', 'rustc', 'binary', 'notices'}
            or record['target'] != PLATFORMS[key][0] or not isinstance(record['rustc'], str)
            or not isinstance(record['glibc'], str) or not re.fullmatch(r'\d+\.\d+', record['glibc'])):
        raise ValueError('Invalid native build metadata')
    libc, current = platform.libc_ver()
    if (libc != 'glibc' or not re.fullmatch(r'\d+\.\d+', current)
            or tuple(map(int, current.split('.'))) < tuple(map(int, record['glibc'].split('.')))):
        raise ValueError(f'Native engine requires glibc {record["glibc"]}+; use --python-only on this system')
    for kind, name in asset_names(release, key).items():
        item = record[kind]
        if (not isinstance(item, dict) or set(item) != {'name', 'sha256', 'size'} or item['name'] != name
                or not isinstance(item['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])
                or type(item['size']) is not int or not 1 <= item['size'] <= LIMITS[kind]):
            raise ValueError('Invalid native asset identity/checksum/size')
    return data, record


def unchanged(directory):
    from .plugin_manager import _paths, _read_json
    metadata = _read_json(directory / 'RELEASE.json', 65536)
    if not isinstance(metadata, dict) or not isinstance(metadata.get('files'), dict):
        raise ValueError('Existing native directory is not owned by the installer; preserved unchanged')
    expected = {BINARY, NOTICES, 'plugin.json', *GUIDES}
    files, directories = _paths(directory)
    if set(metadata['files']) != expected or set(files) != expected | {'RELEASE.json'} or directories:
        raise ValueError('Existing native package has extra/missing files; preserved unchanged')
    for name, digest in metadata['files'].items():
        limit = LIMITS['binary'] if name == BINARY else LIMITS['notices'] if name == NOTICES else 131072
        if hashlib.sha256(read_bytes(directory / name, limit)).hexdigest() != digest:
            raise ValueError('Existing native package was edited; preserved unchanged')


def install(source, app):
    from .plugin_api import _read_manifest
    from .plugin_manager import Manager, UseLease, _decode, _private, mutation_lock
    source, app = Path(source).resolve(), Path(app).resolve()
    key = platform_key()
    data, record = manifest(source, key)
    blobs = {}
    for kind, filename in (('binary', BINARY), ('notices', NOTICES)):
        local = source / 'native/rust-ranking' / filename
        item = record[kind]
        blob = read_bytes(local, LIMITS[kind]) if local.exists() or local.is_symlink() else fetch(
            f'https://github.com/{REPOSITORY}/releases/download/v{data["version"]}/{item["name"]}', LIMITS[kind])
        if len(blob) != item['size'] or hashlib.sha256(blob).hexdigest() != item['sha256']:
            raise ValueError('Native asset checksum/size mismatch; nothing installed')
        blobs[filename] = blob
    elf = blobs[BINARY]
    if len(elf) < 64 or elf[:6] != b'\x7fELF\x02\x01' or int.from_bytes(elf[18:20], 'little') != PLATFORMS[key][1]:
        raise ValueError('Native executable is not the expected Linux architecture')
    package = source / 'examples/rust-ranking'
    specification = _decode(read_bytes(package / 'plugin.json', 16384))
    specification['command'] = ['./' + BINARY]
    blobs['plugin.json'] = (json.dumps(specification, indent=2) + '\n').encode()
    for name in GUIDES:
        blobs[name] = read_bytes(package / name, 131072)
    owner = {'schema': 1, 'version': data['version'], 'platform': key, 'source_commit': data['source_commit'],
             'source_sha256': data['source_sha256'],
             'files': {name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()}}
    manager = Manager()
    if app != manager.data:
        raise ValueError('Native destination must match the selected XDG application directory')
    with mutation_lock(manager.store):
        lease = UseLease(manager.store, shared=False)
        temporary = None
        try:
            registry = manager.registry()
            if manager._has_journal():
                raise ValueError('Resolve package recovery before updating the native engine')
            parent, destination = app / 'native', app / 'native/rust-ranking'
            if parent.is_symlink() or destination.is_symlink() or manager.root.is_symlink():
                raise ValueError('Native paths must not be symlinks; preserved unchanged')
            _private(parent)
            _private(manager.root)
            if destination.exists():
                unchanged(destination)
            temporary = Path(tempfile.mkdtemp(prefix='.install-', dir=parent))
            staged = temporary / 'rust-ranking'
            staged.mkdir(mode=0o700)
            for name, blob in blobs.items():
                path = staged / name
                path.write_bytes(blob)
                path.chmod(0o700 if name == BINARY else 0o600)
            (staged / 'RELEASE.json').write_text(json.dumps(owner, indent=2) + '\n')
            _read_manifest(staged)
            if destination.exists() and (destination / 'RELEASE.json').read_text() == json.dumps(owner, indent=2) + '\n':
                shutil.rmtree(temporary); temporary = None
            else:
                # Preserve old code instead of guessing which files a user might want to keep.
                backup = app / '.native-backups' / ('rust-ranking-' + uuid.uuid4().hex)
                if destination.exists():
                    _private(backup.parent)
                    destination.rename(backup)
                try:
                    staged.rename(destination)
                except OSError:
                    if backup.exists():
                        backup.rename(destination)
                    raise
            link = manager.root / 'rust-ranking'
            authored = manager.authored / 'rust-ranking'
            if (os.path.lexists(authored) and 'rust-ranking' not in registry['installed']
                    and link.is_symlink() and link.resolve() == destination
                    and authored.resolve() != destination):
                link.unlink()  # Retire only our redundant registration, never the user's override.
            if (not os.path.lexists(link) and not os.path.lexists(authored)
                    and 'rust-ranking' not in registry['installed']):
                link.symlink_to(destination, target_is_directory=True)
            print('Prebuilt Rust engine installed; existing engine settings and package overrides preserved.')
        finally:
            if temporary is not None:
                shutil.rmtree(temporary)
            lease.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--app', type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.source, args.app)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f'Native engine: {error}. For Python-only installation, rerun install.sh --python-only.')


if __name__ == '__main__':
    main()
