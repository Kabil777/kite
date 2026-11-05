"""Single atomic, conflict-detecting persistence path for declared plugin preferences."""
import os
from pathlib import Path
import stat
import tempfile
import yaml
from .plugin_settings import validate_values


def save_settings(path, identifier, fields, values, expected):
    path = Path(path)
    values = validate_values(fields, values, partial=True)
    if path.is_symlink():
        raise ValueError('Refusing to replace a symlinked config.yaml')
    exists = path.exists()
    info = path.stat() if exists else None
    if info and (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)):
        raise ValueError('Configuration is not an owned regular file')
    before = path.read_bytes() if exists else b''
    if len(before) > 2 * 1024 * 1024:
        raise ValueError('Configuration exceeds 2 MiB')
    current = yaml.safe_load(before) or {}
    if not isinstance(current, dict):
        raise ValueError('KITE configuration must be a mapping')
    plugins = current.setdefault('plugins', {})
    if not isinstance(plugins, dict):
        raise ValueError('plugins configuration must be a mapping')
    plugin = plugins.setdefault(identifier, {})
    if not isinstance(plugin, dict):
        raise ValueError('plugin configuration must be a mapping')
    config = plugin.setdefault('config', {})
    if not isinstance(config, dict):
        raise ValueError('plugin config must be a mapping')
    missing = object()
    changes = {key: data for key, data in values.items() if expected.get(key, missing) != data}
    for key in changes:
        if config.get(key, missing) != expected.get(key, missing):
            raise ValueError('These plugin settings changed elsewhere. Reopen Settings before saving.')
    config.update(changes)
    # Preserve undeclared preferences, but retain the complete per-plugin payload bound.
    import json
    if len(json.dumps(config, allow_nan=False).encode()) > 16384:
        raise ValueError('Plugin settings exceed 16 KiB')
    output = yaml.safe_dump(current, sort_keys=False, allow_unicode=True).encode()
    if len(output) > 2 * 1024 * 1024:
        raise ValueError('Saved configuration would exceed 2 MiB')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.config-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(output)
            stream.flush()
            os.fsync(stream.fileno())
        if info:
            os.chown(temporary, info.st_uid, info.st_gid)
        os.chmod(temporary, stat.S_IMODE(info.st_mode) if info else 0o600)
        def identity(row):
            return row.st_dev, row.st_ino, row.st_mtime_ns, row.st_ctime_ns, row.st_mode, row.st_uid, row.st_gid
        if path.is_symlink() or path.exists() != exists or exists and (path.read_bytes() != before or identity(path.stat()) != identity(info)):
            raise ValueError('Configuration changed during Save; no changes were written.')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return dict(config)
