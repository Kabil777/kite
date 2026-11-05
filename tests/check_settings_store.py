"""Atomic Settings/defaults persistence and selective session retirement, fixture-only."""
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import yaml
from main.plugin_settings_store import save_settings


def check():
    fields = [{'key': 'limit', 'label': 'Limit', 'type': 'number', 'integer': True, 'default': 0}]
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'config.yaml'
        path.write_text(yaml.safe_dump({'web': {'enabled': False}, 'plugins': {'fixture': {'enabled': False, 'config': {'limit': 0, 'other': 'keep'}}}}))
        path.chmod(0o640)
        result = save_settings(path, 'fixture', fields, {'limit': 1}, {'limit': 0})
        assert result == {'limit': 1, 'other': 'keep'}
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert yaml.safe_load(path.read_text())['plugins']['fixture']['enabled'] is False
        before = path.read_bytes()
        try:
            save_settings(path, 'fixture', fields, {'limit': 2}, {'limit': 0})
        except ValueError:
            pass
        else:
            raise AssertionError('Stale editor overwrote an external edit')
        assert path.read_bytes() == before
        with patch('main.plugin_settings_store.os.replace', side_effect=OSError('fixture failure')):
            try:
                save_settings(path, 'fixture', fields, {'limit': 2}, {'limit': 1})
            except OSError:
                pass
            else:
                raise AssertionError('Failure not propagated')
        assert path.read_bytes() == before and not list(path.parent.glob('.config-*'))
        link = path.parent / 'linked.yaml'
        link.symlink_to(path)
        try:
            save_settings(link, 'fixture', fields, {'limit': 2}, {'limit': 1})
        except ValueError:
            pass
        else:
            raise AssertionError('Symlink replacement allowed')
    from main.plugin_runtime import Runtime
    calls = []
    affected = SimpleNamespace(plugin_id='fixture', retire=lambda: calls.append('fixture'))
    other = SimpleNamespace(plugin_id='other', retire=lambda: calls.append('other'))
    runtime = SimpleNamespace(pending=None, sessions=[affected, other], active=affected, notify_idle=lambda: None)
    Runtime.invalidate(runtime, 'fixture')
    assert calls == ['fixture'] and runtime.active is None
    print('Settings store: atomicity, ownership/mode, conflict/failure preservation and selective invalidation passed')


if __name__ == '__main__':
    check()
