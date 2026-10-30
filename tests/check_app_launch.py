"""Launch only a synthetic desktop fixture; never start installed applications."""
import json
import os
from pathlib import Path
import resource
import select
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def main():
    with tempfile.TemporaryDirectory(prefix='kite-app-launch-') as name, patch.dict(os.environ, {
        'XDG_CONFIG_HOME': name + '/config', 'XDG_DATA_HOME': name + '/data',
        'XDG_STATE_HOME': name + '/state', 'XDG_CACHE_HOME': name + '/cache',
    }):
        root = Path(name)
        marker = root / 'launched.json'
        script = root / 'fixture.py'
        script.write_text('''import json, os, resource, sys, time
from pathlib import Path
time.sleep(.05)
Path(sys.argv[1] + '.tmp').write_text(json.dumps({'pid': os.getpid(), 'as': resource.getrlimit(resource.RLIMIT_AS),
                                                'cpu': resource.getrlimit(resource.RLIMIT_CPU)}))
os.replace(sys.argv[1] + '.tmp', sys.argv[1])
print('synthetic application stdout', flush=True)
print('synthetic application stderr', file=sys.stderr, flush=True)
''')
        desktop = root / 'fixture.desktop'
        desktop.write_text('[Desktop Entry]\nType=Application\nName=Synthetic launch fixture\n'
                           'Exec=' + ' '.join(json.dumps(str(arg)) for arg in (sys.executable, script, marker)) +
                           '\nTerminal=false\nStartupNotify=false\nDBusActivatable=false\n')
        for entry in (['-m', 'main.core'], ['-m', 'main.plugin_worker', '--service']):
            marker.unlink(missing_ok=True)
            result = subprocess.run([sys.executable, '-B', *entry], capture_output=True, timeout=5,
                                    input=json.dumps({'kind': 'app', 'value': str(desktop)}).encode())
            assert result.returncode == 0, result.stderr.decode()
            assert json.loads(result.stdout) == {'results': []}, 'Application logs corrupted provider JSON'
            end = time.monotonic() + 3
            while not marker.exists() and time.monotonic() < end:
                time.sleep(.01)
            assert marker.exists(), 'Synthetic desktop application did not start'
            child = json.loads(marker.read_text())
            assert child['as'] == list(resource.getrlimit(resource.RLIMIT_AS)), 'App inherited the helper memory cap'
            assert child['cpu'] == list(resource.getrlimit(resource.RLIMIT_CPU)), 'App inherited the helper CPU cap'
            try:
                descriptor = os.pidfd_open(child['pid'])
            except ProcessLookupError:
                pass
            else:
                try:
                    assert select.select([descriptor], [], [], 3)[0], 'Synthetic child did not exit'
                finally:
                    os.close(descriptor)
        missing = subprocess.run([sys.executable, '-B', '-m', 'main.core'], capture_output=True, timeout=5,
                                 input=json.dumps({'kind': 'app', 'value': str(root / 'missing.desktop')}).encode())
        assert missing.returncode != 0 and 'error' in json.loads(missing.stdout)
    print('App launch OK: clean RPC stdout, normal inherited limits, direct and host-service paths, missing-file error')


if __name__ == '__main__':
    main()
