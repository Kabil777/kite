# Plugin integration: add your own feature

**Installing someone else's plugin?** Use the [user guide](howto.md#install-and-manage-plugins) and [official registry](https://github.com/Kabil777/kite_plugins). This page is for writing your own.

Start with one small tool: accept a query, return a result, and let KITE draw it. You can add a form or custom actions once that works. To publish a finished package, follow the registry's [contribution guide](https://github.com/Kabil777/kite_plugins/blob/main/CONTRIBUTING.md).

A KITE plugin returns results and, optionally, a custom view; its backend may use Python or any executable language. You do not subclass the launcher or build a Qt window. This guide starts with Python; [the language-neutral protocol](plugin-protocol.md) covers other backends and replaceable root-ranking plugins.

Read [Frontend](frontend.md) for every available UI component and [Core](core.md) for the application's execution model.

> **Trust boundary:** API-v2 external plugins run outside the GUI process. Tools and default rankers use systemd-contained workers; executable rankers may explicitly opt into the [bounded direct-child mode](plugin-protocol.md#cancellation-lifecycle-and-trust). These contain faults, not permissions: plugins still have your user's privileges, copied configuration and desktop environment. Only install code you have reviewed and trust. Supervised execution requires a working systemd user manager and cgroup v2; direct rankers have separate Linux utility/rlimit requirements.

## 1. Create a plugin directory

Put packages you author directly under:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/kite/plugin/<id>/
```

Installed packages are discovered separately under `${XDG_DATA_HOME:-$HOME/.local/share}/kite/plugins/<id>/`. Do not add a redundant share-directory link for a config-directory package.

For a plugin named `hello`:

```text
hello/
  plugin.json
  __init__.py
  HOWTO.md        # Optional task-first user manual
  README.md       # Developer/setup notes; legacy guide fallback
  backend.py      # Optional domain logic / CLI calls
```

Keep these locations distinct:

- `~/.config/kite/plugin/`: user-authored code, discovered directly at startup.
- `plugins/` under the XDG app data directory: installed code or explicit development links.
- `examples/`: core protocol/search samples, not auto-loaded. `bash install.sh --link-example node-tool` is an explicit development opt-in for a package in this checkout.
- `kite_plugins/plugins/` in the separate registry checkout: optional plugin source, including Gopass. Cloning that repository does not install or activate its packages; follow each directory's README.
- `main/bundled_plugins/`: first-party clipboard/emoji/web features and the default ranking policy shipped with KITE.

Bundled IDs/routes (including `marketplace`) cannot be replaced by user packages. Links to the same canonical package are deduplicated; distinct packages claiming the same ID are rejected rather than silently overriding each other. Manager actions refresh discovery explicitly; restart after manually editing authored source/manifests. Source is imported afresh when a worker starts; there is no file watcher or automatic update.

## 2. Add the manifest

Create `plugin.json`:

```json
{
  "id": "hello",
  "name": "Hello",
  "api_version": 2,
  "entry": "__init__.py",
  "glyph": "\uf075"
}
```

- `id` must match the directory name. Use a lowercase letter first, followed by lowercase letters, digits, `_` or `-`; maximum 64 characters.
- `name` is the human-facing name, at most 128 characters.
- `api_version` must be the integer `2`. Unsupported majors, including v1, produce a migration error; there is no v1 thread fallback.
- `entry` is optional; the Python default is `__init__.py`. Alternatively, `command` supplies executable argv and needs no Python entry. Never declare both.
- `role` defaults to `tool`; `ranking` selects the root-search ranking contract. Executable rankers may use `execution: child` for bounded single-process/single-thread execution; other packages default to `execution: systemd`. See [worker protocol and ranking](plugin-protocol.md).
- `glyph` is optional: up to eight printable characters, typically a Unicode/Nerd Font glyph. Its appearance depends on the installed font.

The manifest is capped at 16 KiB. Invalid/incompatible packages are reported and skipped rather than disabling other valid plugins. Unknown manifest fields are rejected.

## 3. Return your first result

Create `__init__.py`:

```python
from plugin_api import Action, Catalog, Result, View


def search(query, host):
    text = query.strip() or 'Hello from KITE'
    return View(
        results=(Result(
            'copy-text',
            'Copy greeting',
            subtitle=text[:80],
            action=Action('copy', text),
        ),),
        catalog=Catalog(
            placeholder='Type a greeting…',
            footer='Enter copies the greeting',
            empty='Type a greeting to begin',
        ),
    )
```

`search(query, host)` is required. KITE calls it as the query changes. This example only prepares a copy action; it does not write to the clipboard while searching. Copy happens after explicit activation.

Import public objects from **`plugin_api`**. Do not import private launcher/core classes. Use relative imports for your own files, such as `from .backend import find_entries`.

Keep module-level imports fast. Discovery reads metadata only; package import happens in the isolated worker on activation and has a five-second deadline. IO belongs in bounded search/event/backend operations, not module import side effects.

## 4. Install and enable it

Put your editable package under the config-directory path above, restart KITE, then select **Hello** from the launcher. Installed packages may instead use the data-directory path.

Approved manager installs are enabled but stopped; manual packages default to enabled. Effective permission is **YAML enabled AND manager enabled** (both default true). An explicit YAML disable always wins:

```yaml
plugins:
  hello:
    enabled: false
```

A disabled plugin's Python entry point is not imported. KITE does not automatically install dependencies, download other plugins or enable examples. External commands/libraries your plugin needs must already be available.

## Marketplace and package lifecycle

Search **Plugin marketplace** in KITE for native Installed/Browse views. Configure a reviewed public GitHub raw JSON catalog URL; no default repository or release URL is assumed. Browse is an explicit fetch, with a labeled source-bound cache fallback. Review the pinned package's origin, SHA-256, manual and requirements before approving install/update. Code is never imported or executed during browse, staging, install or enable; tool backends start when opened, and a selected ranking backend starts when root ranking is needed.

With KITE completely closed, the Qt-free CLI exposes the same backend:

```sh
kite plugins source "$KITE_PLUGIN_CATALOG_URL"
kite plugins search hello
kite plugins list
kite plugins info hello
kite plugins install hello       # interactive manual/origin review and approval
kite plugins disable hello
kite plugins enable hello
kite plugins update hello
kite plugins uninstall hello
```

Use `--yes` on install/update/uninstall for deliberate noninteractive approval; it bypasses no validation. `kite plugins --config /absolute/path/config.yaml ...` selects another YAML file to inspect, never rewrite. Read-only search/list/info may run while the app is open; CLI mutations require the GUI and worker/service leases to be released. There is no live-CLI IPC or automatic app shutdown. Native manager operations wait for confirmed worker and host-service cleanup before changing code and honor dirty-form confirmation.

The manager's enable flags live under `$XDG_STATE_HOME/kite/plugin-manager/`, separate from YAML. Disabling is durable before cleanup: a cleanup failure leaves it disabled and visibly unresolved, not falsely stopped. Enabling never defeats `enabled: false` in YAML or starts backend code. Missing declared commands are reported as setup requirements; libraries and other setup remain manual.

Only catalog-listed public GitHub Release ZIPs are network-installable through the manager. Manual/authored roots remain supported. No automatic updates, dependency installation, hooks, Git clone or secret-store cleanup occurs. `--link-example` links are unmanaged; do not overwrite a manager-owned link with an example—uninstall it first.

Managed `plugins/<id>` entries point to `plugin-packages/<sha256>/<id>`, retaining the manifest's ID basename. Registry records hold version, provenance and file inventory outside the manifest. Atomic pointer changes have an fsynced recovery journal because pointer and registry updates are not one filesystem transaction. Workers pin the resolved managed payload and independent supervisors/service runners hold use leases through cleanup, including GUI owner death. A released lock alone is not proof of unit cleanup.

Uninstall removes only manager-owned code/link and its installation record. YAML, external stores and authored/development targets remain intact. Edited/extra files are retained privately under `plugin-backups/`; unknown extras are not opened to classify them. Conflicting IDs, changed links, references to a payload, corrupt state and uncertain cleanup block destructive operations instead of guessing ownership. Staging/backups/package-store directories are excluded from app-upgrade overlays.

For an interrupted operation, use **Check cleanup / recover**, or close KITE and retry a CLI mutation. Intent-only transactions roll back; a durable commit finishes housekeeping. Never delete a registry/journal to bypass a refusal or assume Cancel reverses a commit. Resolve leftover systemd units yourself; the manager only inspects them, it does not stop unknown units. Preserve state/payloads until ownership is understood. Author publishing instructions, catalog contract, limits and detailed recovery: [catalog guide](../catalog/README.md).

## Migrating an existing v1 package

1. Move user-authored code to the config-directory root, or keep its installed data-directory package. Remove a duplicate installed copy/link if both claim the same ID.
2. Review the package, then set `api_version` to `2`. Keep `search(query, host)` and `handle_event(event, host)` returning public DTOs. Import Toast and other public objects from `plugin_api`.
3. Remove assumptions about shared GUI-process globals, inline Python callback registration or direct Qt access. The host invokes named worker methods, not function objects. Values must be bounded JSON-compatible data.
4. Keep secrets in stdin/private pipe data, never argv, labels or notifications. Check cancellation and do not retry writes automatically after uncertain failure.
5. Restart KITE for the new manifest. Native configuration keys, plugin_config and the separate executable-extension protocol remain compatible; plugin code runs in the same project Python environment but a different process.

There is no automatic v1 adapter or permissive major-version range. The wire framing version is independently `1`; JSON-RPC's `2.0` marker is not the plugin API version.

## The response objects

A returned `View` is a complete replacement for the previous response, including removing a previous Catalog when the new view omits one:

| Object | Purpose |
| --- | --- |
| `View` | `nodes`, `results`, optional `title`, `error`, `catalog`, `actions` and `toast` |
| `Toast` | Plain-text status notification; return alone from an event to preserve the current screen |
| `MenuAction` | A plugin-defined entry in the host Actions menu |
| `Result` | An item in the main result list: ID, title, subtitle, icon/glyph, optional `tag` badge, action and search metadata |
| `Node` | A component inside the custom detail pane, or a full-width root `form` |
| `Catalog` | Search prompt, footer, section heading, layout, empty message, filters and ordering |
| `Action` | An explicit operation to perform after activation |
| `Event` | Interaction information delivered to your handler |
| `HostServices` | Per-call configuration, cancellation check and documented host operations |

`nodes`, `results` and node `children` must be tuples, not lists of dictionaries. Treat returned objects as immutable. IDs must be stable and unique within their respective result/node collections. KITE handles namespacing result IDs internally.

`Result.tag` is an optional string up to 256 characters, shown as the shared borderless type/context label. For example, use `tag='example.com'` on account results. `Action('query', 'example.com/', push=True)` can drill into a Vault using the search text as navigation state; the plugin remains stateless, and returning the same query explicitly refreshes its list.

For a user-facing failure, return a bounded, useful message:

```python
return View(error='Required command is not installed. Install it and reopen this plugin.')
```

Do not include passwords or raw sensitive backend output in errors.

## Handling events

`handle_event(event, host)` is optional until your plugin needs interaction. A button or an `Action('activate', ...)` requires a handler to produce the next view.

KITE supplies:

- `event.node_id`: the control/result being activated.
- `event.name`: event such as `activate` or `change`.
- `event.payload`: declared static data plus dynamic interaction values.
- `event.payload['query']`: current main search text.
- `event.payload['inputs']`: current text/select/list values, keyed by component ID.

Text-input changes add `text`; select changes and list activation add `value`. Catalog filters use node ID `catalog`, event name `filter`, and a selected `value`. Host query/input snapshots take precedence over static fields with those names.

A small complete form example, replacing the earlier `__init__.py`:

```python
from plugin_api import Action, Node, Result, View


def search(query, host):
    return View(nodes=(Node('form', 'message-form', {
        'title': 'Prepare a message',
        'submit_text': 'Prepare copy',
    }, children=(
        Node('label', 'prompt', {'text': 'Text to copy'}),
        Node('text_input', 'message', {'placeholder': 'Message'}),
    ), events={'submit': {}}),))


def handle_event(event, host):
    if event.node_id != 'message-form' or event.name != 'submit':
        return View(error='Unsupported action')
    text = event.payload['inputs'].get('message', '')
    return View(results=(Result('copy-message', 'Copy message', action=Action('copy', text)),))
```

The `form` component fills the content area and supplies its own Submit/Cancel controls. When validation fails, return that same form ID with `error` and current nonsecret field values; otherwise the redraw creates fresh empty inputs. Password fields should remain empty in the returned view. Stable IDs restore focus/cursor position, not missing application state. You do not need a per-keystroke `change` event merely to read a field when a button is clicked.

There is no per-window plugin object provided by KITE. Prefer reconstructing the view from query/event inputs rather than keeping mutable global form state.

## Actions

| Action | Value | What happens |
| --- | --- | --- |
| `copy` | String | Host writes clipboard through stdin, not command-line arguments |
| `open` | Local path or HTTP(S)/file URL | Host validates it, then uses `xdg-open` |
| `exec` | Nonempty list of argv strings | Run a trusted command without shell interpolation |
| `activate` | String | Deliver an activation event to the plugin instead of closing |
| `clipboard` | cliphist item ID string | Restore the original item's bytes |
| `web` | Query string | Open an encoded Google search |
| `query` | Search text | Replace the current search query without closing or running an OS action |
| `back` | Empty string | Pop one host navigation step, honoring dirty-form confirmation |

Copy and clipboard-restore actions keep KITE open, preserve the current query/selection, and show a success or error toast without exposing copied contents. Application launches, window focus and open/exec actions still close the launcher on success; plugin activation returns an updated view. A failed action must not pretend it succeeded.

`exec` is powerful: use it only for trusted, explicitly selected operations. Never put a password in argv. For secret creation, use a backend command with stdin and a timeout instead.

## Navigation and Escape

Use the optional `push=True` flag for an action that opens a new browsing screen:

```python
Action('query', 'example.com/', push=True)  # Open a vault folder.
Action('activate', 'example.com/p1', push=True)  # Open explicit copy choices after unlocking.
```

Only `query` and `activate` may push history. Query pushes happen when the query actually changes. Activation pushes are committed only after a successful current plugin response; errors do not add a false navigation step.

Escape unwinds overlays first, then restores the previous mode/query/selection. The stack holds at most 64 routing-metadata entries in memory—not `View` objects, action values, form inputs or decrypted passwords. Returning rebuilds the browsing view through normal search; it does not restore cached secret screens or form drafts. Editing query text alone does not create a stack entry.

A full-width `form` automatically asks before discarding user edits. Its built-in Cancel follows the same path. For a menu item, use `MenuAction('cancel', 'Cancel', Action('back', ''))`. Do not emulate Back by returning a list containing navigation rows. After a successful submit, return the refreshed browsing view; the host removes the completed form step. An error should return the same form, with nonsecret values preserved and password inputs empty.

## Plugin Actions menu

Return `View.actions` to add plugin-specific entries to **Actions / ⌃ K**. They are menu commands, not searchable result rows:

```python
from plugin_api import Action, MenuAction, View


def search(query, host):
    return View(actions=(
        MenuAction('refresh', 'Refresh entries', Action('activate', 'refresh'), glyph='\uf021'),
    ))


def handle_event(event, host):
    if event.node_id == 'refresh' and event.name == 'activate':
        return search(event.payload['query'], host)
    return View(error='Unsupported action')
```

`MenuAction(id, title, action, glyph='', shortcut='')` uses the **same validated `Action` contract** as a result. `activate` calls the plugin handler with the menu ID and value, plus current query/form inputs. Other action types use the normal host copy/open/exec/query behavior. Constructing a menu does not perform any of those operations.

- `actions` must be a tuple with at most 32 items.
- IDs must be unique within the menu, nonempty, and at most 128 characters.
- Titles are nonblank strings up to 256 characters.
- Optional glyphs are up to eight printable characters.
- Include the actions in each returned view where they should remain available; omitting them clears that view's custom menu entries.
- Custom entries work even when `results` is empty. KITE keeps its standard menu items; plugins add entries rather than replacing the host menu/theme.
- A new accepted plugin view dismisses an already-open menu, preventing obsolete commands from staying on screen.
- Optional `shortcut` registers one chord **only while that plugin is active and its current view publishes the item**. Bindings are removed on view/mode changes and disabled during calls/help/discard prompts. Back/cancel remains usable during calls and discard prompts. None are compositor-wide.
- Use canonical forms such as `Ctrl+S`, `Alt+I`, `Ctrl+Shift+S` or `F5`. Supported modifiers are Ctrl/Alt/Shift/Meta; keys are letters, digits, `?`, `/` or F1–F24. Typing keys require Ctrl/Alt/Meta. Strings are limited to 64 characters and duplicate normalized chords are rejected.
- Host navigation/help/editing chords are reserved, including Ctrl+K, Ctrl+L, Ctrl+N/P, Ctrl+?, Ctrl+A/C/V/X/Z/Y and Ctrl+Shift+C/I/F/D. Ctrl+? is host help: KITE manual at root, plugin manual inside its runtime. Ctrl+Shift+/ is its usual physical equivalent on US layouts.
- Drawing `Node('shortcut_hint', ...)` only displays keycaps; it does not create a binding.

Gopass uses this API for **Alt+I → Create Password** and **Ctrl+S → Sync passwords**. Neither binding exists at root; Sync is absent in creation forms but remains available while browsing even without a remote. The backend validates remote configuration at activation and returns a closeable error toast if needed. Creation instead offers **Ctrl+G → Cancel creation** through `Action('back', '')`, honoring dirty-form confirmation.

## Return a toast without replacing the screen

```python
from plugin_api import Toast


def handle_event(event, host):
    if event.node_id == 'sync':
        return Toast('Could not sync', 'Configure a Git remote, then retry.', 'error')
    return Toast('Done', tone='success')
```

`handle_event` may return `View | Toast`; `search` must return `View`. A standalone Toast preserves the current content, query and selection and adds no history step. To refresh content as well, include `toast=Toast(...)` in your returned View. Failed submitted-form toasts clear masked inputs while retaining nonsecret fields.

Toast title is required (128 characters); message is optional (512 characters); tone is `info`, `success` or `error`. Text is plain, not Markdown. The host supplies status icon, close button, theme and placement. Error toasts stay until closed; others disappear after four seconds. A new toast replaces the old one. Never send secrets or raw backend output. See [Toast component](frontend.md#13-toasts-closeable-status-notifications).

## Plugin settings

Add your plugin's settings beneath its ID in `~/.config/kite/config.yaml`:

```yaml
plugins:
  hello:
    enabled: true
    config:
      greeting: Hello
      limit: 20
```

Read them with:

```python
from plugin_api import plugin_config

settings = plugin_config(host, 'hello')
greeting = settings.get('greeting', 'Hello')
```

`plugin_config` returns `{}` when the plugin has no settings. The plugin owns defaults and validation for its keys; check types/ranges before using them. The mapping must be JSON-compatible and is capped at 16 KiB.

`host.config` is a per-call copy of the validated full configuration. Do not rewrite it to persist preferences; changes to that copy do not update the user's YAML.

## Host services

Use `host.call(name, arguments)` when your feature needs an existing KITE capability:

| Service | Arguments | Result |
| --- | --- | --- |
| `emoji.data` | `{}` | Bundled Unicode dataset text |
| `clipboard.list` | `{}` | cliphist listing lines |
| `clipboard.preview` | `{'value': item_id}` | Preview mapping containing type/content/size and optional dimensions |
| `clipboard.write` | `{'value': text}` | Empty list on success |
| `clipboard.restore` | `{'value': item_id}` | Empty list on success |
| `apps.list` | `{}` | Application provider records |
| `windows.list` | `{}` | Window provider records |
| `files.list` | `{'value': directory}` | File provider records |
| `math.calculate` | `{'query': expression}` | Calculator provider records |
| `apps.launch` | `{'value': desktop_file}` | Empty list on success |
| `windows.focus` | `{'value': window_address}` | Empty list on success |
| `web.suggestions` | `{'query': query}` or `{}` | Empty list in external workers; native web adapter receives supplied suggestions; never starts another request |

Example:

```python
items = host.call('files.list', {'value': '/home/example/Documents'})
```

Provider records use fields such as `id`, `title`, `subtitle`, `icon`, `kind`, `value`, `keywords`, `expression`, `glyph` and `accessibility_label`. Translate what you need into public `Result` objects; do not import private implementation classes.

External worker service calls are bridged asynchronously through bounded provider subprocesses, with a five-second service deadline and 64-KiB request budget. Services are serialized and must belong to the active request; do not save host callbacks for later background use. Unknown names/arguments fail at runtime—typed wrappers are not implemented. Native bundled adapters retain their existing direct/cached paths. External `web.suggestions` returns an empty list and never starts a network fetch. Missing commands/provider failures can be turned into useful View/Toast errors.

Clipboard copy Actions and `clipboard.write`/`clipboard.restore` services honor the host's `clipboard.copy_command` setting. Plugins pass content or an item ID, not a command override. The host handles session selection, raw bytes/MIME and stdin transport; no plugin-specific copy backend is needed.

Reading data in `search` is different from performing an action. Do not call write/launch/focus services on every keystroke; reserve side effects for explicit user actions.

## Threads, timeouts and cancellation

Search/event requests execute one at a time in the active plugin's isolated worker. Python functions use the bundled adapter; executable backends use the same [worker protocol](plugin-protocol.md). Do not create Qt widgets or import private launcher internals. A worker is reused across calls while its plugin is active, then stopped on leave or failure. Each call receives a fresh copied configuration and `host.cancelled()` flag.

A newer query/event or mode change invalidates old work. Check `host.cancelled()` around longer operations and put explicit timeouts on your own IO. For example:

```python
import subprocess

if host.cancelled():
    return View()
completed = subprocess.run(
    ['my-command', '--list'],
    capture_output=True,
    text=True,
    timeout=5,
    check=True,
)
if host.cancelled():
    return View()
```

Startup/import and search have five-second deadlines; events have 120 seconds. Superseding work invalidates its reply immediately. Cooperative cancellation has a 300-ms grace, followed by whole-unit termination with a one-second hard-stop grace—even for detached child processes. Keep your own IO timeouts as well.

Per-unit limits are 768 MiB resident memory, 256 tasks and one CPU's quota. Transport frames are capped at 4 MiB with JSON depth 64, in addition to DTO limits. Normal print output is redirected away from protocol stdout; do not print secrets anywhere.

Unhandled errors, crashes and deadlines retire the worker. Returning a normal error View/Toast reports a recoverable error without forcing a restart. Failed operations are never automatically replayed: creation or sync may already have committed. A new user action can start a fresh worker only after prior cleanup. Missing systemd user support produces a visible refusal, not weaker execution.

## Add a How to guide

Put user instructions in plugin-local **`HOWTO.md`**. Keep developer/setup internals in README and API docs. KITE prefers HOWTO, with README as a backward-compatible fallback.

**Actions → How to / Ctrl+?** is context-aware: root opens KITE's `docs/howto.md`, not the selected plugin's README. Inside a plugin it opens that plugin's manual; plugins without a guide fall back to KITE help.

Start with the first useful task in 3–4 steps, then common tasks, shortcuts and specific failure recovery. Avoid API vocabulary in user manuals. Use fenced code and optional GitHub-style callouts:

```markdown
> [!WARNING]
> Copy only after selecting the intended account.
```

The file must be UTF-8 and at most 128 KiB. Guides use KITE's shared [Markdown component](frontend.md#8-markdown-render-documentation), with safe explicit links and no embedded resource loading.

## Limits to keep in mind

| Data | Limit |
| --- | --- |
| Manifest / plugin config mapping | 16 KiB each |
| Node tree | Depth 16; at most 2,000 nodes |
| Node IDs | Unique, nonempty; at most 128 characters |
| Results | At most 5,000 per response |
| Result IDs | Unique, nonempty; at most 512 characters |
| Result title / subtitle | 2,048 characters each |
| Menu actions | At most 32; ID 128 chars, title 256 chars, glyph 8 printable chars, shortcut 64 chars |
| Toast | Nonempty title 128 chars, message 512 chars; info/success/error |
| Worker frame | 4 MiB including all nested content; JSON depth 64; no truncation |
| Worker deadlines | Startup/search 5 seconds, events 120 seconds |
| Worker unit | 768 MiB resident memory, 256 tasks, one CPU quota |
| Ordinary control text / string action values | 4,096 characters |
| `type_label` text | 256 characters |
| Select/list options | At most 1,000 strings, 512 characters each |
| Markdown | 128 KiB UTF-8 |
| Image preview | See [image limits](frontend.md#9-image_preview-show-a-content-image) |

Unknown components, properties or events are rejected. Check the [frontend guide](frontend.md) rather than guessing Qt property names.

## Existing examples

- [Gopass in the registry repository](https://github.com/Kabil777/kite_plugins/tree/main/plugins/gopass): package-relative backend, search results, explicit secret retrieval/copy and a masked create form. Uses stdin for secret writes and refuses overwriting an existing entry.
- [`main/bundled_plugins/clipboard/`](../main/bundled_plugins/clipboard/README.md): native catalog/filter rules over host clipboard services.
- [`main/bundled_plugins/emoji/`](../main/bundled_plugins/emoji/README.md): dataset-to-results conversion and grid catalog.
- [`main/bundled_plugins/web/`](../main/bundled_plugins/web/README.md): host-supplied suggestions and browser-search actions.

The gopass example is not automatically installed. A retrieved password is never shown in a preview; copying is a separate action. Python cannot guarantee secure erasure of secret strings, and clipboard retention is outside KITE's control.

## Older executable extensions

Prefix-based JSON extensions remain a separate integration option. A configured executable receives `{"version": 1, "query": "..."}` on stdin and returns a JSON result object on stdout. These keep their one-shot JSON-v1 process protocol; they are separate from API-v2 worker packages. They are also trusted code, with bounded output/time and argv-based execution. See [prefix-extension reference](extensions.md) and [`examples/uppercase.py`](../examples/uppercase.py).
