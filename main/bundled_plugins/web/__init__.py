"""Web-search plugin contract; backend rules live in sibling backend.py."""
from plugin_api import Catalog, View
from . import backend


def presentation(config):
    return Catalog('Search the web…', 'Live Google suggestions · Enter opens selected result'
                   if config['web']['enabled'] else 'Suggestions off · Enter searches Google',
                   'Results', empty='Type to search the web')


def search(query, host):
    values = host.call('web.suggestions', {'query': query})
    return View(results=backend.suggestions(values, query, host.config['web']['max_suggestions']),
                catalog=presentation(host.config))


query_for = backend.query_for
suggestion_url = backend.suggestion_url
fallbacks = backend.fallbacks
