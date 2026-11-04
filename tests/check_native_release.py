"""Prebuilt installer fixtures only: fake ELF bytes, mocked downloads, temporary XDG roots."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

from main import native_release as native
from main.plugin_manager import Manager, UseLease

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    key = native.platform_key()
    elf = bytearray(128)
    elf[:6] = b'\x7fELF\x02\x01'
    elf[18:20] = native.PLATFORMS[key][1].to_bytes(2, 'little')
    blobs = {'binary': bytes(elf), 'notices': b'Synthetic license fixture; no executable code.\n'}
    names = native.asset_names(native.version(ROOT), key)
    record = {'target': native.PLATFORMS[key][0], 'glibc': platform.libc_ver()[1], 'rustc': 'fixture compiler'}
    files = {}
    for kind, blob in blobs.items():
        record[kind] = {'name': names[kind], 'sha256': hashlib.sha256(blob).hexdigest(), 'size': len(blob)}
        files[names[kind]] = blob
    metadata = {'schema': 1, 'version': native.version(ROOT), 'source_commit': 'a' * 40,
                'source_sha256': native.source_digest(ROOT), 'platforms': {key: record}}
    files['native-assets.json'] = json.dumps(metadata).encode()
    return key, metadata, files


def reject(call):
    try:
        call()
    except (OSError, ValueError):
        return
    raise AssertionError('Unsafe native update was accepted')


def module_checks():
    key, metadata, files = fixture()
    with tempfile.TemporaryDirectory(prefix='kite-native-assets-') as name, patch.dict(os.environ, {
        'HOME': name, 'XDG_CONFIG_HOME': name + '/config', 'XDG_DATA_HOME': name + '/data',
        'XDG_STATE_HOME': name + '/state', 'XDG_CACHE_HOME': name + '/cache',
    }), patch.object(native, 'fetch', side_effect=lambda url, limit: files[url.rsplit('/', 1)[-1]]):
        manager = Manager()
        app = manager.data
        native.install(ROOT, app)
        directory = app / 'native/rust-ranking'
        link = manager.root / 'rust-ranking'
        assert link.is_symlink() and link.resolve() == directory
        assert json.loads((directory / 'plugin.json').read_text())['command'] == ['./kite-flea-ranking']
        assert (directory / native.BINARY).stat().st_mode & 0o100
        rows = manager.inventory()
        assert len(rows) == 1 and rows[0]['id'] == 'rust-ranking' and rows[0]['enabled']
        errors = []
        assert manager.discover({}, lambda name, error: errors.append(error))[0][0]['role'] == 'ranking'
        assert not errors and not (manager.root / 'gopass').exists()
        native.install(ROOT, app)
        assert not (app / '.native-backups').exists(), 'Identical installation created a backup'
        manager.config_path.parent.mkdir(parents=True, exist_ok=True)
        manager.config_path.write_text('plugins:\n  rust-ranking:\n    enabled: false\n')
        assert manager.discover({}, lambda *args: None) == [], 'Bundled engine bypassed a YAML disable'
        manager.config_path.write_text('plugins: {}\n')
        current = (directory / native.BINARY).read_bytes()
        changed = deepcopy(metadata)
        changed['platforms'][key]['binary']['sha256'] = '0' * 64
        files['native-assets.json'] = json.dumps(changed).encode()
        reject(lambda: native.install(ROOT, app))
        assert (directory / native.BINARY).read_bytes() == current
        changed = deepcopy(metadata)
        changed['platforms'][key]['binary']['name'] = '../untrusted-binary'
        files['native-assets.json'] = json.dumps(changed).encode()
        reject(lambda: native.install(ROOT, app))
        changed = deepcopy(metadata)
        changed['platforms'][key]['glibc'] = '99.0'
        files['native-assets.json'] = json.dumps(changed).encode()
        reject(lambda: native.install(ROOT, app))
        changed = deepcopy(metadata)
        changed['source_sha256'] = '0' * 64
        files['native-assets.json'] = json.dumps(changed).encode()
        reject(lambda: native.install(ROOT, app))
        files['native-assets.json'] = json.dumps(metadata).encode()
        guide = directory / 'HOWTO.md'
        original = guide.read_bytes()
        guide.write_text('user-edited guide')
        reject(lambda: native.install(ROOT, app))
        assert guide.read_text() == 'user-edited guide'
        guide.write_bytes(original)
        extra = directory / 'user-file'
        extra.write_text('preserve me')
        reject(lambda: native.install(ROOT, app))
        assert extra.read_text() == 'preserve me'
        extra.unlink()
        lease = UseLease(manager.store)
        try:
            reject(lambda: native.install(ROOT, app))
        finally:
            lease.close()
        manager.authored.mkdir(parents=True)
        override = manager.authored / 'rust-ranking'
        override.mkdir()
        (override / 'plugin.json').write_text(json.dumps({'id': 'rust-ranking', 'name': 'User override',
            'api_version': 2, 'role': 'ranking', 'command': ['/bin/false']}))
        native.install(ROOT, app)
        assert not link.exists() and manager.discover({}, lambda *args: None)[0][0]['name'] == 'User override'
        embedded = Path(name) / 'bundle'
        (embedded / 'examples').mkdir(parents=True)
        shutil.copytree(ROOT / 'examples/rust-ranking', embedded / 'examples/rust-ranking',
                        ignore=shutil.ignore_patterns('target', '__pycache__'))
        shutil.copy2(ROOT / 'pyproject.toml', embedded / 'pyproject.toml')
        (embedded / 'native-assets.json').write_text(json.dumps(metadata))
        local = embedded / 'native/rust-ranking'
        local.mkdir(parents=True)
        for kind, filename in (('binary', native.BINARY), ('notices', native.NOTICES)):
            (local / filename).write_bytes(files[metadata['platforms'][key][kind]['name']])
        with patch.object(native, 'fetch', side_effect=AssertionError('Embedded release attempted a download')):
            native.install(embedded, app)


def installer_check():
    _, _, files = fixture()
    with tempfile.TemporaryDirectory(prefix='kite-native-install-') as name:
        root = Path(name)
        assets = root / 'assets'; assets.mkdir()
        for filename, value in files.items():
            (assets / filename).write_bytes(value)
        app = root / 'data/kite'
        (app / '.venv/bin').mkdir(parents=True)
        real = str(ROOT / '.venv/bin/python')
        wrapper = app / '.venv/bin/python'
        wrapper.write_text('#!' + real + '\nimport os, sys\nfrom pathlib import Path\n'
            'if sys.argv[1:4] == ["-B", "-m", "main.native_release"]:\n'
            '    sys.path.insert(0, os.getcwd())\n'
            '    from main import native_release as n\n'
            '    n.fetch=lambda url,limit: (Path(' + repr(str(assets)) + ')/url.rsplit("/",1)[-1]).read_bytes()\n'
            '    sys.argv=["native_release"]+sys.argv[4:]\n    n.main()\n'
            'else:\n    os.execv(' + repr(real) + ',[' + repr(real) + ']+sys.argv[1:])\n')
        wrapper.chmod(0o700)
        tools = root / 'tools'; tools.mkdir()
        for tool in ('uv', 'update-desktop-database', 'gtk-update-icon-cache'):
            path = tools / tool; path.write_text('#!/bin/sh\nexit 0\n'); path.chmod(0o700)
        marker = root / 'compiler-was-invoked'
        for tool in ('cargo', 'rustc'):
            path = tools / tool
            path.write_text('#!/bin/sh\ntouch ' + str(marker) + '\nexit 90\n'); path.chmod(0o700)
        env = {**os.environ, 'HOME': str(root / 'home'), 'XDG_DATA_HOME': str(root / 'data'),
               'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_STATE_HOME': str(root / 'state'),
               'XDG_CACHE_HOME': str(root / 'cache'), 'PATH': str(tools) + ':' + os.environ['PATH']}
        result = subprocess.run(['bash', str(ROOT / 'install.sh')], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr + result.stdout
        assert not marker.exists(), 'Installer invoked a Rust compiler'
        assert (app / 'plugins/rust-ranking').resolve() == app / 'native/rust-ranking'
        assert not (app / 'plugins/gopass').exists() and not (app / 'examples/gopass').exists()
        assert not (app / 'examples/rust-ranking/target').exists(), 'Development build outputs leaked into installation'
        assert (app / 'native/rust-ranking/THIRD-PARTY-NOTICES.txt').is_file()


def main():
    module_checks()
    installer_check()
    print('Prebuilt native OK: checked source/platform/hash, no compilation/execution on install, registration, overrides, leases, edits/extras and Python-only compatibility')


if __name__ == '__main__':
    main()
