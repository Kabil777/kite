"""Host adapters: public plugin DTOs never require imports of private launcher code."""
import json
import subprocess
import sys
from .plugin_api import HostServices, validate_view


def result_row(item):
    from .core import Result
    action = item.action
    kind, value = (action.type, action.value) if action else ('noop', '')
    kind = {'clipboard': 'clip'}.get(kind, kind)
    return Result(item.id, item.title, item.subtitle, item.icon, kind, value,
                  item.keywords, glyph=item.glyph, accessibility_label=item.accessibility_label, tag=item.tag,
                  push=action.push if action else False)


def plugin_row(plugin_id, item):
    row = result_row(item)
    row.id = 'plugin:' + plugin_id + ':' + item.id
    if row.kind == 'activate':
        row.kind = 'plugin_action'
        row.value = {'result_id': item.id, 'value': item.action.value}
    return row


def rows(view):
    validate_view(view)
    return [result_row(item) for item in view.results]


SERVICE_PROVIDERS = {'apps.list': 'apps', 'files.list': 'files', 'windows.list': 'windows',
                     'math.calculate': 'math', 'clipboard.preview': 'clip-preview',
                     'clipboard.restore': 'clip', 'clipboard.write': 'copy', 'windows.focus': 'focus',
                     'apps.launch': 'app', 'emoji.data': 'emoji-data', 'clipboard.list': 'clipboard-list'}


def service_request(name, args):
    if (not isinstance(name, str) or name not in SERVICE_PROVIDERS or not isinstance(args, dict)
            or set(args) - {'query', 'value'}):
        raise ValueError('Unknown or invalid host service')
    query, value = args.get('query', ''), args.get('value', '')
    if not isinstance(query, str) or not isinstance(value, str):
        raise ValueError('Host service query/value must be strings')
    request = {'kind': SERVICE_PROVIDERS[name], 'query': query, 'value': value}
    if len(json.dumps(request).encode()) > 65536:
        raise ValueError('Host service request exceeds 64 KiB')
    return request


def context(config, cancelled=lambda: False, suggestions=()):
    def call(name, args):
        from .core import APP_ROOT, DATA, command
        if cancelled():
            raise RuntimeError('Plugin call cancelled')
        if name == 'emoji.data':
            return (DATA / 'emoji-test.txt').read_text()
        if name == 'clipboard.list':
            return command(['cliphist', 'list']).stdout.decode().splitlines()
        if name == 'web.suggestions':
            return list(suggestions)
        request = service_request(name, args)
        if request['kind'] in ('copy', 'clip'):
            request['copy_command'] = config.get('clipboard', {}).get('copy_command', 'auto')
        # Preserve existing CPU/address-space isolation for math and OS providers.
        output = subprocess.run([sys.executable, '-m', 'main.core'], input=json.dumps(request).encode(),
                                capture_output=True, timeout=5, cwd=APP_ROOT)
        if len(output.stdout) > 2 * 1024 * 1024:
            raise ValueError('Host service output exceeded 2 MiB')
        payload = json.loads(output.stdout)
        if payload.get('error') or output.returncode:
            raise ValueError(payload.get('error') or 'Host service failed')
        return payload.get('results', [])
    return HostServices(config, cancelled, call)
