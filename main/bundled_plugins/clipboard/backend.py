"""Clipboard classification and presentation metadata; host provides cliphist IO."""
import re
from plugin_api import Action, Result


FILTERS = ('All Types', 'Text Only', 'Images Only', 'Files Only', 'Links Only', 'Colors Only')


def content_type(text):
    text = text.strip()
    if text.startswith('[[ binary data'):
        return 'Image' if re.search(r'\b(png|jpe?g|gif|webp|bmp|tiff|avif|svg)\b', text, re.I) else 'Binary'
    if re.match(r'^(https?://|www\.)', text, re.I):
        return 'Link'
    if re.fullmatch(r'#(?:[\da-f]{3}|[\da-f]{6}|[\da-f]{8})', text, re.I):
        return 'Color'
    if text.startswith(('/', 'file://')) or re.match(r'^[a-z]:[\\/]', text, re.I):
        return 'File'
    return 'Text'


def search(query, listing):
    rows = []
    for line in listing[:500]:
        identifier, _, text = line.partition('\t')
        kind = content_type(text)
        dimensions = re.search(r'(\d+)x(\d+)', text) if kind == 'Image' else None
        title = f'Image ({dimensions[1]} × {dimensions[2]})' if dimensions else text[:300]
        icon = {'Image': '', 'Link': 'text-html', 'File': 'text-x-generic',
                'Color': 'applications-graphics'}.get(kind, 'edit-paste')
        if not query or query.casefold() in text.casefold():
            rows.append(Result('clip:' + identifier, title, kind, icon,
                               Action('clipboard', identifier), keywords=text,
                               glyph='\uf1c5' if kind == 'Image' else ''))
    return tuple(rows)


def matches(title, kind, selected):
    kind = kind if kind in ('Image', 'Text', 'File', 'Link', 'Color', 'Binary') else content_type(title)
    return selected == 'All Types' or selected == {
        'Image': 'Images Only', 'File': 'Files Only', 'Link': 'Links Only',
        'Color': 'Colors Only', 'Text': 'Text Only'}.get(kind)


def information(preview):
    fields = [('Content Type', preview.get('type', 'Text')),
              ('Size', f"{preview.get('size', 0):,} bytes")]
    if preview.get('dimensions'):
        fields.append(('Dimensions', preview['dimensions']))
    return fields
