# Bundled Emoji plugin

`__init__.py` implements API-v1 `search`/`Catalog`; `backend.py` parses the bundled Unicode emoji-test data and maps names/glyphs to result/action DTOs. `host.call('emoji.data', {})` supplies the data resource. Qt's host renderer retains the configured grid, Noto Color Emoji font, keyboard navigation, and copy toast.

No external executable, network, user data, or per-plugin configuration required. Copy actions go through host clipboard service; queries are not persisted.
