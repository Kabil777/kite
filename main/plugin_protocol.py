"""Qt-free, bounded JSON-RPC framing and explicit API-v2 DTO conversion."""
from dataclasses import asdict, fields
import json
import math
from .plugin_api import Action, Catalog, Event, MenuAction, Node, Result, Toast, View, validate_toast, validate_view

WIRE_VERSION = 1
MAX_FRAME = 4 * 1024 * 1024
MAX_DEPTH = 64
METHODS = frozenset({'initialize', 'search', 'rank', 'catalog', 'handle_event', 'cancel', 'shutdown', 'host.call'})


class ProtocolError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError('Duplicate JSON key')
        result[key] = value
    return result


def _constant(value):
    raise ProtocolError('Nonfinite number')


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ProtocolError('Nonfinite number')
    return number


def validate_message(message):
    if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
        raise ProtocolError('Expected a JSON-RPC 2.0 object')
    identity = message.get('id')
    if 'id' in message and identity is None:
        raise ProtocolError('Null request ID')
    if identity is not None and not (isinstance(identity, str) and 0 < len(identity) <= 96
                                     or type(identity) is int and 0 <= identity <= 2**53 - 1):
        raise ProtocolError('Invalid request ID')
    if 'method' in message:
        if (set(message) - {'jsonrpc', 'id', 'method', 'params'} or not isinstance(message['method'], str)
                or message['method'] not in METHODS
                or not isinstance(message.get('params', {}), dict)):
            raise ProtocolError('Invalid request')
        if 'id' not in message and message['method'] not in ('cancel', 'shutdown'):
            raise ProtocolError('This method requires an ID')
    else:
        if ('id' not in message or identity is None or ('result' in message) == ('error' in message)
                or set(message) - {'jsonrpc', 'id', 'result', 'error'}):
            raise ProtocolError('Invalid response')
        if 'error' in message:
            error = message['error']
            if (not isinstance(error, dict) or set(error) != {'code', 'message'}
                    or type(error['code']) is not int or not isinstance(error['message'], str)
                    or len(error['message']) > 512):
                raise ProtocolError('Invalid error response')
    return message


def request(identity, method, params=None):
    message = {'jsonrpc': '2.0', 'method': method, 'params': {} if params is None else params}
    if identity is not None:
        message['id'] = identity
    return validate_message(message)


def response(identity, result=None, error=None):
    return validate_message({'jsonrpc': '2.0', 'id': identity,
                             **({'error': {'code': -32000, 'message': error}} if error is not None else {'result': result})})


def encode(message):
    validate_message(message)
    try:
        data = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError) as error:
        raise ProtocolError('Message is not bounded JSON data') from error
    if len(data) > MAX_FRAME:
        raise ProtocolError('Frame exceeds 4 MiB')
    _depth(data)
    return data + b'\n'


def _depth(data):
    depth, quoted, escaped = 0, False, False
    for byte in data:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > MAX_DEPTH:
                raise ProtocolError('JSON exceeds depth limit')
        elif byte in (93, 125):
            depth -= 1


class Framer:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        messages = []
        parts = data.split(b'\n')
        for index, part in enumerate(parts):
            if len(self.buffer) + len(part) > MAX_FRAME:
                raise ProtocolError('Frame exceeds 4 MiB')
            self.buffer.extend(part)
            if index == len(parts) - 1:
                break
            try:
                _depth(self.buffer)
                message = json.loads(self.buffer.decode('utf-8'), object_pairs_hook=_object,
                                     parse_constant=_constant, parse_float=_float)
                messages.append(validate_message(message))
                if len(messages) > 32:
                    raise ProtocolError('Protocol message flood')
            except (ValueError, UnicodeError, RecursionError, TypeError) as error:
                self.buffer.clear()
                raise ProtocolError('Invalid protocol frame') from error
            self.buffer.clear()
        return messages


def _values(cls, value):
    if not isinstance(value, dict) or set(value) - {field.name for field in fields(cls)}:
        raise ProtocolError('Unknown DTO fields')
    return dict(value)


def _array(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ProtocolError('Invalid DTO collection')
    return value


def _action(value):
    return None if value is None else Action(**_values(Action, value))


def to_wire(value):
    if isinstance(value, View):
        validate_view(value)
        return {'kind': 'view', 'data': asdict(value)}
    if isinstance(value, Toast):
        validate_toast(value)
        return {'kind': 'toast', 'data': asdict(value)}
    raise ProtocolError('Expected View or Toast')


def from_wire(value):
    def node(raw, depth=1):
        nonlocal count
        count += 1
        if depth > 16 or count > 2000:
            raise ProtocolError('Node limit exceeded')
        values = _values(Node, raw)
        values['children'] = tuple(node(child, depth + 1) for child in _array(values.get('children', []), 2000))
        return Node(**values)

    try:
        if not isinstance(value, dict) or set(value) != {'kind', 'data'}:
            raise ProtocolError('Invalid response envelope')
        if value['kind'] == 'toast':
            toast = Toast(**_values(Toast, value['data']))
            validate_toast(toast)
            return toast
        if value['kind'] != 'view':
            raise ProtocolError('Unknown response kind')
        values, count = _values(View, value['data']), 0
        values['nodes'] = tuple(node(raw) for raw in _array(values.get('nodes', []), 2000))
        results, actions = [], []
        for raw in _array(values.get('results', []), 5000):
            item = _values(Result, raw)
            item['action'] = _action(item.get('action'))
            results.append(Result(**item))
        for raw in _array(values.get('actions', []), 32):
            item = _values(MenuAction, raw)
            item['action'] = _action(item.get('action'))
            actions.append(MenuAction(**item))
        values['results'], values['actions'] = tuple(results), tuple(actions)
        if values.get('catalog') is not None:
            catalog = _values(Catalog, values['catalog'])
            catalog['filters'] = tuple(_array(catalog.get('filters', []), 20))
            values['catalog'] = Catalog(**catalog)
        if values.get('toast') is not None:
            values['toast'] = Toast(**_values(Toast, values['toast']))
        view = View(**values)
        validate_view(view)
        return view
    except (TypeError, ValueError, KeyError, RecursionError) as error:
        raise ProtocolError('Invalid plugin response') from error


def event_from_wire(value):
    try:
        event = Event(**_values(Event, value))
        if (not isinstance(event.node_id, str) or not event.node_id or len(event.node_id) > 512
                or event.name not in ('activate', 'change', 'submit', 'filter') or not isinstance(event.payload, dict)):
            raise ProtocolError('Invalid event')
        return event
    except (TypeError, ValueError) as error:
        raise ProtocolError('Invalid event') from error
