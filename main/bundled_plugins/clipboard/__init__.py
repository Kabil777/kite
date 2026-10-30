"""Clipboard plugin contract; backend rules live in sibling backend.py."""
from plugin_api import Catalog, View
from . import backend


def presentation(config):
    return Catalog('Search clipboard…', 'Clipboard history', layout='list_detail',
                   empty='Clipboard is empty', filters=backend.FILTERS, preserve_order=True)


def search(query, host):
    listing = host.call('clipboard.list', {})
    return View(results=backend.search(query, listing), catalog=presentation(host.config))


matches = backend.matches
information = backend.information
content_type = backend.content_type
