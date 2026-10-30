"""Default ranking policy, also usable through the public ranking-plugin contract."""
from types import SimpleNamespace

from main.ranking import rank as rank_rows


def rank(params, host):
    rows = [SimpleNamespace(**row) for row in params['candidates']]
    ordered = rank_rows(rows, params['query'], params['usage'], params['limit'],
                        aliases=params['aliases'], sensitivity=params['sensitivity'],
                        learn_queries=params['learn_queries'])
    return {'catalog_revision': params['catalog_revision'], 'ordered_ids': [row.id for row in ordered]}
