"""CI-only release packaging: tracked app source, compiled Rust, and license notices."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import subprocess
import tarfile

from main.native_release import BINARY, NOTICES, PLATFORMS, asset_names, platform_key, source_digest, version

ROOT = Path(__file__).resolve().parents[1]


def notices(package, sysroot):
    metadata = json.loads(subprocess.check_output(['cargo', 'metadata', '--locked', '--format-version=1',
                                                   '--manifest-path', str(package / 'Cargo.toml')]))
    parts = ['KITE native engine — third-party notices\n\n'
             'These collected notices cover the locked crates and Rust standard-library distribution. '
             'They do not declare a project-wide KITE license or imply that every listed component is linked.\n']
    parts.append('\n=== Flea matcher ===\n' + (package / 'LICENSE-FLEA').read_text())
    for item in sorted(metadata['packages'], key=lambda row: (row['name'], row['version'])):
        if item['name'] == 'kite-flea-ranking':
            continue
        directory = Path(item['manifest_path']).parent
        files = sorted(path for path in directory.iterdir() if path.is_file()
                       and path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')))
        if not files:
            raise ValueError('Missing license notices for ' + item['name'])
        parts.append(f'\n=== {item["name"]} {item["version"]} ({item.get("license", "see source")}) ===\n')
        for path in files:
            parts.append('\n--- ' + path.name + ' ---\n' + path.read_text())
    library = sysroot / 'share/doc/rust/COPYRIGHT-library.html'
    licenses = sysroot / 'share/doc/rust/licenses'
    if not library.is_file() or not licenses.is_dir():
        raise ValueError('Rust runtime notices missing; install the rust-docs component for packaging')
    parts.append('\n=== Rust standard library: COPYRIGHT-library.html ===\n' + library.read_text())
    for path in sorted(licenses.iterdir()):
        if path.is_file():
            parts.append('\n=== Rust license: ' + path.name + ' ===\n' + path.read_text())
    return ''.join(parts).encode()


def smoke(binary, directory):
    params = {'protocol_version': 2, 'catalog_revision': 1,
              'candidates': [{'id': 'fixture', 'title': 'Firefox', 'subtitle': '', 'keywords': '', 'kind': 'app'}],
              'usage': {}, 'aliases': {}, 'favorites': []}
    messages = [
        {'jsonrpc': '2.0', 'id': 'init', 'method': 'initialize', 'params': {
            'api_version': 2, 'wire_version': 1, 'plugin_id': 'rust-ranking', 'directory': str(directory),
            'config': {}, 'environment': {}}},
        {'jsonrpc': '2.0', 'id': 'catalog', 'method': 'catalog', 'params': params},
        {'jsonrpc': '2.0', 'id': 'query', 'method': 'rank', 'params': {
            'protocol_version': 2, 'catalog_revision': 1, 'query': 'firefox', 'limit': 60,
            'sensitivity': 'medium', 'learn_queries': False}},
        {'jsonrpc': '2.0', 'method': 'shutdown', 'params': {}}]
    result = subprocess.run([str(binary)], input=''.join(json.dumps(message) + '\n' for message in messages).encode(),
                            capture_output=True, timeout=10, check=True)
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(replies) == 3 and replies[0]['result']['ranking_version'] == 2
    assert replies[1]['result'] == {'catalog_revision': 1, 'count': 1}
    assert replies[2]['result'] == {'catalog_revision': 1, 'ordered_ids': ['fixture']}


def add_file(archive, name, data, executable=False):
    member = tarfile.TarInfo(name)
    member.size, member.mode = len(data), 0o755 if executable else 0o644
    archive.addfile(member, io.BytesIO(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=PLATFORMS, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    release, package = version(ROOT), ROOT / 'examples/rust-ranking'
    assert args.platform == platform_key(), 'Build each release on its matching native runner'
    subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--'], cwd=ROOT, check=True, capture_output=True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    binary = args.binary.resolve()
    elf = binary.read_bytes()
    assert len(elf) >= 64 and elf[:6] == b'\x7fELF\x02\x01' and int.from_bytes(elf[18:20], 'little') == PLATFORMS[args.platform][1]
    smoke(binary, package)
    sysroot = Path(subprocess.check_output(['rustc', '--print', 'sysroot'], text=True).strip())
    payloads = {'binary': binary.read_bytes(), 'notices': notices(package, sysroot)}
    names = asset_names(release, args.platform)
    args.output.mkdir(parents=True, exist_ok=True)
    record = {'target': PLATFORMS[args.platform][0], 'glibc': platform.libc_ver()[1],
              'rustc': subprocess.check_output(['rustc', '--version'], text=True).strip()}
    for kind, payload in payloads.items():
        name = names[kind]
        (args.output / name).write_bytes(payload)
        record[kind] = {'name': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)}
    metadata = {'schema': 1, 'version': release, 'source_commit': commit,
                'source_sha256': source_digest(ROOT), 'platforms': {args.platform: record}}
    encoded = (json.dumps(metadata, indent=2) + '\n').encode()
    (args.output / f'native-assets-{args.platform}.json').write_bytes(encoded)
    prefix = f'kite-{release}/'
    tracked = subprocess.check_output(['git', 'archive', '--format=tar', '--prefix=' + prefix, 'HEAD'], cwd=ROOT)
    output = args.output / f'kite-v{release}-{args.platform}.tar.gz'
    with tarfile.open(fileobj=io.BytesIO(tracked), mode='r:') as original, tarfile.open(output, 'w:gz') as archive:
        for member in original:
            if not member.isfile() and not member.isdir():
                raise ValueError('Release source contains a link/special file: ' + member.name)
            archive.addfile(member, original.extractfile(member) if member.isfile() else None)
        add_file(archive, prefix + 'native-assets.json', encoded)
        add_file(archive, prefix + 'native/rust-ranking/' + BINARY, payloads['binary'], True)
        add_file(archive, prefix + 'native/rust-ranking/' + NOTICES, payloads['notices'])
    print('Release assets built for', args.platform, 'from', commit)


if __name__ == '__main__':
    main()
