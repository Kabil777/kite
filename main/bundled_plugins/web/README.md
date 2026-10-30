# Bundled Web Search plugin

`__init__.py` adapts API-v1 search/catalog responses; `backend.py` owns suggestion/fallback DTOs, query-mode policy and URL construction. Host retains Qt HTTPS request, timeout/size checks, cache, cancellation, and privacy gates (`web.enabled`, `web.suggest_in_root`). The plugin cannot trigger an additional network request through the `web.suggestions` capability: it receives only host-fetched suggestions.

No extra executable or new dependency required. Browser launch remains host-owned and uses encoded URLs.
