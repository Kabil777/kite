"""Emoji catalog backend and view. No Qt or private host imports."""
from plugin_api import Catalog, View
from .backend import search as search_dataset


def presentation(config):
    return Catalog('Search emoji…', 'Search Emoji & Symbols', 'Results', 'grid')


def search(query, host):
    return View(results=search_dataset(query, host.call('emoji.data', {})),
                catalog=presentation(host.config))
