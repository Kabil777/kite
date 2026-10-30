# Bundled Clipboard plugin

`__init__.py` adapts the current public DTO views/catalogs; `backend.py` owns cliphist-line classification, filtering metadata and preview information. Host services own `cliphist list`, bounded decode/image conversion and clipboard restoration. Qt host owns the current split list/preview pane and in-panel type selector so YAML style and keyboard behavior stay consistent.

History requires `cliphist` and its compatible clipboard watcher as configured by the desktop. Copy/restore uses the host's `clipboard.copy_command`: auto selects `wl-copy` on Wayland or `xclip` on X11, with text-only `xsel` fallback; a custom argv list may override it. Image/binary copying requires a MIME-aware backend. Missing support produces a visible error; launcher startup remains available. No clipboard content is copied into app state or usage history.
