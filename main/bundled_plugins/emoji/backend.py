"""Emoji dataset/domain logic; host supplies the versioned data resource."""
from plugin_api import Action, Result


def search(query, dataset):
    rows = []
    for line in dataset.splitlines():
        if '; fully-qualified' in line and '#' in line:
            glyph, _, name = line.split('#', 1)[1].strip().split(' ', 2)
            if not query or query.casefold() in name.casefold():
                rows.append(Result('emoji:' + glyph, name, 'Copy emoji', '', Action('copy', glyph)))
    return tuple(rows)
