"""Bounded, Qt-free settings contracts shared by discovery, editors and backends."""
from copy import deepcopy
import json
import math
import re

KINDS = {'text', 'choice', 'boolean', 'number', 'directory', 'file', 'directory_list', 'file_list', 'string_list', 'color'}
PROPS = {'key', 'label', 'type', 'default', 'options', 'placeholder', 'help', 'required', 'optional', 'group', 'advanced',
         'min', 'max', 'step', 'decimals', 'integer', 'max_length', 'max_items'}


def default(field):
    if 'default' in field:
        return deepcopy(field['default'])
    if field.get('optional'):
        return None
    if field['type'] in ('boolean', 'number'):
        return False if field['type'] == 'boolean' else 0
    if field['type'] == 'color':
        return '000000ff'
    if field['type'] == 'choice':
        return field['options'][0]
    return [] if field['type'].endswith('_list') else ''


def value(field, data):
    kind, label = field['type'], field['label']
    if data is None and field.get('optional'):
        return None
    if kind.endswith('_list'):
        # Legacy text-list settings migrate only when the user explicitly saves.
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, RecursionError) as error:
                raise ValueError(label + ': stored list needs repair; add folders/items again.') from error
        if (not isinstance(data, list) or len(data) > field.get('max_items', 16)
                or any(not isinstance(item, str) or not item or '\0' in item
                       or len(item) > field.get('max_length', 4096) for item in data)):
            raise ValueError(label + ': use a bounded list of nonempty text values.')
        result = list(dict.fromkeys(data))
    elif kind == 'boolean':
        if type(data) is not bool:
            raise ValueError(label + ': choose on or off.')
        result = data
    elif kind == 'number':
        if (type(data) not in (int, float) or not math.isfinite(data)
                or not field.get('min', -1e9) <= data <= field.get('max', 1e9)
                or field.get('integer') and int(data) != data):
            raise ValueError(label + ': enter a finite number within the allowed range.')
        result = int(data) if field.get('integer') else data
    else:
        if not isinstance(data, str) or '\0' in data or len(data) > field.get('max_length', 1024):
            raise ValueError(label + ': enter bounded text.')
        if kind == 'choice' and data not in field['options']:
            raise ValueError(label + ': select one of the declared choices.')
        if kind == 'color' and not re.fullmatch(r'#?[0-9a-fA-F]{6}([0-9a-fA-F]{2})?', data):
            raise ValueError(label + ': use RRGGBB or RRGGBBAA.')
        result = data
    if kind in ('directory', 'file', 'directory_list', 'file_list'):
        paths = result if isinstance(result, list) else [result] if result else []
        if any(not (path.startswith('/') or path.startswith('~/')) for path in paths):
            raise ValueError(label + ': use absolute paths or ~/ paths.')
    if field.get('required') and result in ('', [], None):
        raise ValueError(label + ': a value is required.')
    return result


def validate_fields(fields):
    if not isinstance(fields, list) or len(fields) > 32:
        raise ValueError('Plugin configuration must have at most 32 fields')
    keys = set()
    for field in fields:
        if (not isinstance(field, dict) or set(field) - PROPS or not isinstance(field.get('type'), str) or field.get('type') not in KINDS
                or not isinstance(field.get('key'), str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', field['key'])
                or field['key'] in keys or not isinstance(field.get('label'), str)
                or not field['label'].strip() or len(field['label']) > 128):
            raise ValueError('Invalid plugin configuration field or unsupported field kind')
        keys.add(field['key'])
        if field.get('required') and field.get('optional'):
            raise ValueError('A field cannot be both required and optional')
        for name in ('placeholder', 'help', 'group'):
            if name in field and (not isinstance(field[name], str) or len(field[name]) > 1024):
                raise ValueError('Invalid field help/placeholder/group')
        for name in ('required', 'optional', 'advanced', 'integer'):
            if name in field and type(field[name]) is not bool:
                raise ValueError('Invalid field boolean metadata')
        for name, maximum in (('max_items', 1000), ('max_length', 4096), ('decimals', 6)):
            if name in field and (type(field[name]) is not int or not 0 <= field[name] <= maximum):
                raise ValueError('Invalid field size/precision limit')
        for name in ('min', 'max', 'step'):
            if name in field and (type(field[name]) not in (int, float) or not math.isfinite(field[name]) or abs(field[name]) > 1e9):
                raise ValueError('Invalid numeric field bounds')
        if field.get('min', -1e9) > field.get('max', 1e9) or field.get('step', 1) <= 0:
            raise ValueError('Invalid numeric field range/step')
        if field['type'] == 'choice':
            options = field.get('options')
            if (not isinstance(options, list) or not 1 <= len(options) <= 64
                    or any(not isinstance(item, str) or len(item) > 256 for item in options)
                    or len(set(options)) != len(options)):
                raise ValueError('Choice configuration needs bounded unique options')
        value({**field, 'required': False} if 'default' not in field else field, default(field))
    validate_values([{**field, 'required': False} for field in fields], {})
    return fields


def validate_values(fields, values, partial=False):
    if not isinstance(values, dict):
        raise ValueError('Plugin settings must be a mapping')
    result = {field['key']: value(field, values.get(field['key'], default(field)))
              for field in fields if not partial or field['key'] in values}
    try:
        size = len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'))
    except (ValueError, UnicodeError) as error:
        raise ValueError('Invalid settings text/number encoding') from error
    if size > 16384:
        raise ValueError('Plugin settings exceed 16 KiB')
    return result
