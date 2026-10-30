"""Web query, URL and result rules; network transport stays host-owned."""
from urllib.parse import urlencode
from plugin_api import Action, Result


def query_for(mode, text, config, excluded=False):
    query = text.strip()
    return query if mode == 'web' or mode == 'root' and config['web']['suggest_in_root'] and not excluded else ''


def suggestion_url(query):
    return 'https://suggestqueries.google.com/complete/search?' + urlencode({'client': 'firefox', 'q': query})


def suggestions(values, query, limit):
    return tuple(Result('web:suggestion:' + text, text, 'Google suggestion', 'system-search',
                        Action('web', text), glyph='\uf002')
                 for text in values[:limit] if text != query)


def fallbacks(query, root=False):
    rows = [Result('web:raw', 'Search Google', query, 'web-browser', Action('web', query), glyph='\uf1a0')]
    if root:
        rows.append(Result('web:ddg', 'Search DuckDuckGo', query, 'web-browser',
                           Action('open', 'https://duckduckgo.com/?' + urlencode({'q': query})), glyph='\uf002'))
    return tuple(rows)
