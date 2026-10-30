"""Qt-free ranking-plugin contract; only host-owned candidate IDs cross back."""
import math

from .plugin_protocol import ProtocolError

RANKING_VERSION = 1
FIELDS = {'protocol_version', 'catalog_revision', 'query', 'candidates', 'usage', 'aliases',
          'favorites', 'limit', 'sensitivity', 'learn_queries'}


def validate_request(params):
    if (not isinstance(params, dict) or set(params) != FIELDS
            or type(params['protocol_version']) is not int or params['protocol_version'] != RANKING_VERSION
            or type(params['catalog_revision']) is not int or not 0 <= params['catalog_revision'] < 2**53
            or not isinstance(params['query'], str) or len(params['query']) > 4096
            or type(params['limit']) is not int or not 1 <= params['limit'] <= 200
            or params['sensitivity'] not in ('low', 'medium', 'high')
            or type(params['learn_queries']) is not bool
            or not isinstance(params['candidates'], list) or len(params['candidates']) > 10000):
        raise ProtocolError('Invalid ranking request')
    ids = set()
    for row in params['candidates']:
        if (not isinstance(row, dict) or set(row) != {'id', 'title', 'subtitle', 'keywords', 'kind'}
                or any(not isinstance(value, str) or len(value) > 8192 for value in row.values())
                or not row['id'] or len(row['id']) > 1024 or row['id'] in ids):
            raise ProtocolError('Invalid ranking candidate')
        ids.add(row['id'])
    if (not isinstance(params['aliases'], dict) or not params['aliases'].keys() <= ids
            or any(not isinstance(value, str) or len(value) > 1024 for value in params['aliases'].values())
            or not isinstance(params['favorites'], list) or len(params['favorites']) > len(ids)
            or any(not isinstance(value, str) or value not in ids for value in params['favorites'])
            or len(set(params['favorites'])) != len(params['favorites'])
            or not isinstance(params['usage'], dict) or not params['usage'].keys() <= ids):
        raise ProtocolError('Invalid ranking context')
    for record in params['usage'].values():
        if not isinstance(record, dict) or set(record) - {'count', 'last', 'anchor', 'terms'}:
            raise ProtocolError('Invalid ranking usage')
        for key, value in record.items():
            if key == 'terms':
                if not isinstance(value, list) or len(value) > 20 or any(not isinstance(t, str) or len(t) > 512 for t in value):
                    raise ProtocolError('Invalid learned terms')
            elif type(value) not in (int, float) or abs(value) >= 2**53 or not math.isfinite(value):
                raise ProtocolError('Invalid usage number')
            elif key == 'count' and (type(value) is not int or value < 0):
                raise ProtocolError('Invalid usage count')
    return params


def validate_result(value, params, known_ids=None):
    if (not isinstance(value, dict) or set(value) != {'catalog_revision', 'ordered_ids'}
            or type(value['catalog_revision']) is not int or value['catalog_revision'] != params['catalog_revision']
            or not isinstance(value['ordered_ids'], list) or len(value['ordered_ids']) > params['limit']):
        raise ProtocolError('Invalid ranking response')
    ids = known_ids if known_ids is not None else {row['id'] for row in params['candidates']}
    ordered = value['ordered_ids']
    if any(not isinstance(item, str) or item not in ids for item in ordered) or len(set(ordered)) != len(ordered):
        raise ProtocolError('Unknown or duplicate ranking ID')
    return ordered


def query_request(params):
    """Small v2 query; the catalog was validated at its revision boundary."""
    value = {key: params[key] for key in ('catalog_revision', 'query', 'limit', 'sensitivity', 'learn_queries')}
    if (type(value['catalog_revision']) is not int or not 0 <= value['catalog_revision'] < 2**53
            or not isinstance(value['query'], str) or len(value['query']) > 4096
            or type(value['limit']) is not int or not 1 <= value['limit'] <= 200
            or value['sensitivity'] not in ('low', 'medium', 'high') or type(value['learn_queries']) is not bool):
        raise ProtocolError('Invalid ranking query')
    return dict(value, protocol_version=2)


def catalog_request(params):
    return dict({key: params[key] for key in ('catalog_revision', 'candidates', 'usage', 'aliases', 'favorites')},
                protocol_version=2)


def load_catalog(params):
    if (not isinstance(params, dict) or set(params) != {'protocol_version', 'catalog_revision', 'candidates',
                                                      'usage', 'aliases', 'favorites'}
            or type(params['protocol_version']) is not int or params['protocol_version'] != 2):
        raise ProtocolError('Invalid ranking catalog')
    return validate_request(dict(params, protocol_version=1, query='', limit=60,
                                 sensitivity='medium', learn_queries=True))


def expand_query(catalog, params):
    if not isinstance(params, dict) or set(params) != {'protocol_version', 'catalog_revision', 'query',
                                                     'limit', 'sensitivity', 'learn_queries'}:
        raise ProtocolError('Invalid ranking query fields')
    if (type(params['protocol_version']) is not int or params['protocol_version'] != 2
            or catalog is None or params['catalog_revision'] != catalog['catalog_revision']):
        raise ProtocolError('Ranking catalog revision is not loaded')
    query_request(params)
    return {**catalog, **params, 'protocol_version': 1}


def make_request(rows, query, usage, aliases, favorites, options, limit):
    candidates = [{key: getattr(row, key) for key in ('id', 'title', 'subtitle', 'keywords', 'kind')} for row in rows]
    ids = {row.id for row in rows}
    history = {key: {field: value for field, value in record.items()
                     if field in ('count', 'last', 'anchor') or field == 'terms' and options['learn_queries']}
               for key, record in usage.items() if key in ids}
    return validate_request({'protocol_version': RANKING_VERSION, 'catalog_revision': 0, 'query': query,
                             'candidates': candidates, 'usage': history,
                             'aliases': {key: value for key, value in aliases.items() if key in ids},
                             'favorites': list(dict.fromkeys(key for key in favorites if key in ids)),
                             'sensitivity': options['sensitivity'], 'learn_queries': options['learn_queries'],
                             'limit': limit})
