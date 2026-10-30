# Core: how KITE works

KITE is a native Qt launcher. Its job is to turn a query into useful results, then perform the action the user explicitly selects—without blocking typing or showing results from an old query.

For UI building blocks, see [Frontend](frontend.md). For extending KITE, see [Plugin integration](plugins.md). Everyday tasks and shortcuts are in the [user manual](howto.md); installation is in the [main README](../README.md).

## A map of the application

```text
kite command
  └─ main.py
      └─ main/launcher.py — window, navigation, search coordination
          ├─ core.py — configuration, calculator and OS providers
          ├─ ranking.py — reusable local matching and usage ranking
          ├─ bundled_plugins/ranking/ — default search policy plugin
          ├─ ranking_plugin.py / root_ranking.py — full-policy contract and selection
          ├─ applications.py — application metadata cache and Gio change monitor
          ├─ favorites.py — ordered favorites
          ├─ plugin_api.py — Qt-free plugin data contracts and validation
          ├─ plugin_runtime.py — asynchronous Qt worker supervisor/client
          ├─ plugin_protocol.py — Qt-free JSON-RPC framing and DTO codec
          ├─ plugin_worker.py — Qt-free Python adapter and any-language systemd supervisor
          ├─ host_services.py — controlled provider/action adapters
          └─ frontend/ — widgets, Markdown, image previews, node renderer
```

A provider gets data or performs an operation. A frontend component displays data. The launcher connects them. A component should not import the launcher just to get its colors, query or clipboard content: those inputs should be passed in.

This separation is practical, not a claim that the entire core has no Qt imports. For example, the clipboard provider lazily uses Qt image decoding to produce thumbnails; the GUI-side worker client uses Qt process primitives. The **public plugin data contract, wire codec and Python worker** are Qt-free. The provider core as a whole is not.

## Files and directories

| Location | What it contains |
| --- | --- |
| `~/.local/share/kite/` | Installed application |
| `~/.config/kite/config.yaml` | User settings |
| `~/.local/state/kite/usage.json` | Local launch usage and learned search terms |
| `~/.local/state/kite/favorites.sqlite` | Ordered favorites |
| `~/.cache/kite/applications.json` | Disposable application metadata cache; honors `XDG_CACHE_HOME` |
| `~/.config/kite/plugin/<id>/` | User-authored packages, discovered directly |
| `~/.local/share/kite/plugins/<id>/` | Installed trusted packages; development links are supported |
| `main/bundled_plugins/` | Shipped clipboard, emoji and web plugins |
| `examples/` | Opt-in examples, not automatically loaded |
| `main/defaults.yaml` | Full default configuration |
| `assets/icons/kite.png` | KITE window/footer icon |

Configuration/user-authored packages honor `XDG_CONFIG_HOME`; state honors `XDG_STATE_HOME`; installed packages honor `XDG_DATA_HOME`. The user-authored directory is singular `plugin`, the installed directory plural `plugins`. The installer also places the command under `~/.local/bin/kite` and a user desktop entry under the XDG data directory.

Do not store plugin-specific credentials in the application's shared usage history. That history is for launcher ranking, not a general plugin database.

## Startup and a second invocation

`main.py` loads the launcher entry point. Before creating the window, the launcher parses command-line options and validates configuration.

A normal invocation uses a per-user local socket, `kite-qt`, beneath `XDG_RUNTIME_DIR`:

- No existing instance: show valid cached application metadata immediately and index applications in the background.
- Existing instance, plain `kite`: close that window.
- Existing instance, `kite clipboard` or another direct mode: switch its mode.

A lock prevents two simultaneous startups from creating competing servers. `--dmenu` is intentionally separate and does not toggle the main instance or start application indexing/monitoring.

### Application cache and freshness

`main/applications.py` reads at most 2 MiB of versioned, owned regular-file JSON. It validates unique application IDs, string metadata, application-only kinds and desktop-file action paths; it never caches executable commands, plugin actions or secrets. Cache identity includes XDG application directories, desktop, home and locale. Missing, corrupt, incompatible or symlinked caches are misses. Saves reuse the existing private atomic JSON writer.

Every normal startup performs the existing bounded Gio provider refresh, even when a cache was loaded. Refresh errors keep the last usable list/cache. Only changed metadata replaces the list, saves the cache and invalidates the ranking catalog; enumeration-order changes alone are ignored. A valid empty list is cached as empty rather than treated as a miss.

A daemon thread owns a private GLib context and `Gio.AppInfoMonitor`. It primes/re-arms Gio's application database and sends Qt notifications only; it does not render or launch applications. Notifications coalesce for 200 ms. A change during an in-flight scan schedules a follow-up instead of cancelling that scan. Initial monitor readiness also requests reconciliation, covering setup races. Application indexing retains a five-second deadline and a 2-MiB response bound. No loader is shown merely for a background cache refresh. Shutdown stops monitoring/timers and ignores late indexing replies. If monitoring is unavailable, the unconditional startup refresh still works and the failure is reported on stderr.

While an external ranker initializes, cached rows are provisionally ranked by the built-in policy so the list can appear before the first IPC reply; activation remains blocked until the current validated answer. This is distinct from caching search results or executing stale cached launch commands.

The popup size comes from YAML and is clamped to available screen space. KITE does not force the desktop into fullscreen. A root `form` node uses the full content width within that popup, keeping header/footer; its main query is read-only while editing.

## Configuration: defaults plus overrides

KITE merges the user's YAML overrides with `main/defaults.yaml`. You do not need to copy every default setting into your own file.

For example:

```yaml
window:
  width: 850
  height: 620
ranking:
  sensitivity: medium
web:
  suggest_in_root: false
```

Unknown settings, invalid types, malformed colors and out-of-range dimensions are rejected rather than silently ignored. Configuration errors are reported before normal startup.

For application maintenance code:

```python
from main.core import load_config

config = load_config()  # Validated defaults merged with the normal user configuration.
```

Plugins should use `host.config` / `plugin_config(...)`, not import this loader or read launcher internals. See [plugin configuration](plugins.md#plugin-settings).

## Navigation and forms

Escape unwinds the current overlay, then returns through a bounded in-memory navigation stack. Each entry contains mode, query, selected result ID and optional file-directory path. No `View`, action value, form input or decrypted password is stored there. Restoring a screen reruns normal search and restores selection rather than reviving a secret-bearing view.

Mode transitions record browsing context. Plugins opt in to deeper steps with `Action('query', value, push=True)` or `Action('activate', value, push=True)`; activation history is committed only for a successful current response. `Action('back', '')` requests the same guarded back behavior as Escape. Pending navigation calls can be cancelled back to their original browsing screen.

Forms are owned by `main/frontend/form.py`, with shared controls in `controls.py`. A dirty form shows an inline discard prompt before back/window-close/mode changes; Keep editing retains current fields, while Discard clears masked fields before leaving. Successful submit returns a refreshed browsing view and removes the completed form step; failed submit stays in the form with nonsecret values and empty password inputs. This is not a claim of secure erasure of Python/Qt memory.

## What happens when you type

```text
Search text changes
  ├─ invalidate earlier query-dependent work
  ├─ rank via the default policy, or await the selected ranking plugin
  ├─ math input → debounce → calculator subprocess
  ├─ web mode → debounce → HTTPS suggestions
  └─ extension prefix → debounce → executable extension
                       ↓
             accept only the current response
                       ↓
             render results and preserve deliberate selection
```

Mode loading is a separate operation. Loading clipboard/files/windows should not restart every time the user types: the returned index is filtered against the current query. Switching modes cancels the preceding mode job.

When the user has deliberately moved through results, KITE tries to preserve the selected result's stable ID during asynchronous refreshes. Section labels are not selectable results.

With an empty root query, the current groups are **Favorites → Plugins → Applications**, omitting empty groups. Favorites are not repeated in later groups. Search adds ranked matches, applicable calculator/extension results, and browser-search fallbacks.

## Ranking, in plain language

The default bundled ranking plugin reuses `main/ranking.py`, combining matching quality with useful local history. It remains the fallback; users can select another matching/filtering/ordering policy through `ranking.engine`. Tool and ranking backends share [the language-neutral worker protocol](plugin-protocol.md).

The default policy:

1. Normalize accents and selected equivalent letters, while remembering word/camel-case boundaries.
2. Look for query characters in order. They may have gaps, but missing or substituted characters do not count as matches.
3. Prefer exact names, aliases, useful boundaries and adjacent characters.
4. Apply configured sensitivity to reject weak matches.
5. Use launch frequency/recency to order relevant candidates, not to admit unrelated ones.

Aliases match exactly or by prefix. Whole words receive a boost so a real word inside a longer title can compete with a similar-looking prefix.

The matching algorithm uses dynamic programming: time grows with query length × candidate length, while working memory grows with candidate length. Target normalization is cached. Large external datasets still need bounded search or their own index rather than returning everything to the GUI.

### Usage and privacy

Only successful root application/built-in-command activations contribute learned query terms. The store retains at most 20 terms per item; their boost expires 17 days after that item's last launch. Frequency scores decay with a ten-day half-life.

Clipboard content, math expressions, web queries and plugin queries are not written to this usage store. `ranking.learn_queries: false` disables learning and use of those terms. Delete `usage.json` with KITE closed to erase the store. Unreadable/malformed history is preserved and writes are disabled for that session.

Favorites use SQLite and keep explicit order. Existing YAML favorite IDs seed the database once; later UI changes do not rewrite appearance settings.

## Providers and actions

`main/core.py` contains the existing provider/action entry point. The launcher can call it in a subprocess using JSON on stdin/stdout.

| Feature | Where its data/operation comes from |
| --- | --- |
| Applications | System Gio desktop application information |
| Calculator | Restricted expression parser and SymPy |
| Clipboard history | `cliphist list` |
| Clipboard preview | Decode the selected item, produce bounded image/text data |
| Clipboard copy/restore | Shared `copy_bytes`: configured argv or session-selected `wl-copy`/`xclip`/text-only `xsel`, bytes on stdin |
| Files | Directory listing, bounded to the supported result count |
| Windows | Qt-free `main/windows.py`: native Hyprland/Sway/niri, X11 EWMH, optional KDE/GNOME/wlroots helpers |
| Browser search | Encoded HTTP(S) URL opened by the host |

Arguments are passed as argv, not interpolated shell strings. Failures become visible errors rather than successful-looking actions.

Window snapshots normalize title/app/workspace plus a backend-qualified target into the existing `Result` shape. Focus validates the target and rechecks that the window still exists with that same backend. Hyprland's legacy hexadecimal focus value remains accepted on Wayland; X11 uses EWMH IDs, Sway/niri/GNOME use numeric IDs and KWin uses UUIDs. The generic wlroots CLI fallback has no stable ID and rejects duplicate app/title selectors rather than choosing arbitrarily.

Desktop environment names only prioritize capability probes. Wayland never silently falls back to an XWayland-only snapshot. Helper subprocesses use a 1.5-second timeout; native provider CPU/memory bounds and parent wall-clock deadlines still apply. GNOME uses typed, time-limited Gio D-Bus calls to an explicitly enabled Window Commander extension; KDE uses a fixed KWin JSON-snapshot script through recent kdotool, not interpolated user/title code. No extensions or helpers are installed automatically. See [requirements and verification limits](../README.md#window-switching-across-desktops). All new desktop activation paths remain runtime-unverified.

`clipboard.copy_command` is `auto` or a validated argv list (1–32 arguments, at most 4096 characters each, 16 KiB serialized total, no NUL). Jobs injects this trusted host setting into copy/restore provider requests, including reverse services from external workers. Native host adapters pass the same setting. Plugin service arguments cannot override it.

`auto` checks Wayland environment first, then X11 DISPLAY; it never switches a Wayland copy to XWayland because a tool is missing. The custom-command `{mime}` substitution carries only MIME metadata, never clipboard contents. Text uses UTF-8; history restoration detects MIME with the existing Qt MIME database. xsel and custom commands without `{mime}` reject binary content. Exact original multi-format clipboard offers are not retained by cliphist and are not reconstructed.

The provider envelope is bounded to 128 KiB to accommodate trusted command metadata alongside existing service arguments; reverse-service arguments retain their 64-KiB limit. Copy commands retain the three-second subprocess timeout. Their stdout/stderr go to the null device: clipboard owners such as xclip may fork, so capture pipes must not keep the provider waiting after the launcher command exits. Success is reported only after successful completion, with no retry after failure/timeout because the clipboard may already have changed.

Desktop application launch uses Gio's manager API with child stdout/stderr redirected to the null device, keeping application logs out of the provider JSON pipe. Launch requests do not impose the helper's 3-second CPU/768-MiB address-space limits on the desktop application. Request/output bounds and the launch helper's parent wall-clock deadline remain; non-launch providers retain their CPU/memory limits. `tests.check_app_launch` exercises both native and host-service launch paths using only a synthetic desktop fixture.

The internal provider `Result` and public `plugin_api.Result` have different jobs. `host_services.py` translates between them. A plugin should return public `Result` objects and use [documented host services](plugins.md#host-services), not depend on private provider classes. The same host action routing handles plugin `MenuAction` entries; menus are declared in `View.actions`, independently of searchable results.

## Clipboard frontend and data flow

```text
Clipboard plugin supplies results + Catalog(layout='list_detail')
  ↓
Launcher builds history list, type filter and detail pane
  ↓
Selection changes → 90ms debounce → background preview provider
  ↓
Image bytes → reusable ImagePreview
Text       → plain read-only text viewer
Metadata   → content type, size and optional dimensions
```

Only the selected item is decoded. Selection/mode changes cancel pending work, and stale callbacks cannot replace the current preview. Images from the provider are capped at 720×420; text is capped at 12,000 characters. Copying restores the original item, not the shortened preview.

KITE does not invent source-app names, original filenames or timestamps that cliphist did not retain. The cliphist database remains independent of KITE's own state.

## Calculator safety and scope

The calculator tokenizes a restricted expression grammar, normalizes shorthand/implicit multiplication into an allowlisted Python AST, then constructs SymPy expressions directly. Numeric tokens alone use `ast.parse`; arbitrary source is never evaluated with `eval`, `parse_expr` or `sympify`. Attributes, indexing, strings, unknown names and arbitrary function execution remain unavailable.

Inputs, syntax-tree size, numeric literals, exponents and factorials have limits. Computation runs outside the GUI with CPU, memory and wall-clock bounds. Editing the query cancels obsolete work; a timeout is preferable to freezing typing.

It supports arithmetic, symbols, equations and supported symbolic functions. Unary shorthand accepts `sqrt 9` and `sin 30`; implicit products accept `2pi`, `2x`, `2(3+4)` and `2 sin 30`. Explicit function calls and multiplication remain valid. Shorthand consumes powers/implicit products but stops at outside explicit operators: `sin 10 * 2` is `sin(10)*2`, whereas `sin (10*2)` applies the function to the whole product. Multi-argument calls retain parentheses.

Trig now defaults to degrees, including parenthesized calls and `math.calculate` host services. A trig-only `r` suffix requests radians: `sin 10r`, `sin (pi/2)r`, `sin(pi/2r)`. Inverse trig returns degrees unless marked, so `asin 0.5` is 30 and `asin 0.5r` is pi/6. This is an intentional change from the former radian default; `r` is not a new symbol or general units system.

`IncompleteMathInput` remains an error to provider/service clients but marks the internal error envelope with `incomplete: true`. The launcher suppresses notifications for it. Stable genuine errors enter an owned 600ms timer after the calculation response, guarded by mode, query and revision and coalesced by error key. Editing cancels pending error notifications and dismisses only a calculator-owned toast; other notifications keep their normal lifecycle. Success still uses the same result/approximation and copy-action path.

This is not a general natural-language solver, currency converter or units/date engine. Manual synthetic provider examples were observed; no automated tests or KITE GUI were launched, so live UI timing and cross-desktop activation remain unverified.

## Network boundary

Normal root, emoji and clipboard queries stay local by default. Opening web mode—including `?` input—opts into Google suggestions while typing. `web.suggest_in_root: true` separately enables root suggestions; `web.enabled: false` disables live suggestions.

HTTPS responses are bounded and validated; suggestions are plain text and cached only in memory. Offline/error states retain ordinary browser-search fallback rows. Creating those fallback rows does not itself send a request.

Trusted plugins can perform their own network access. The launcher's web privacy setting does not sandbox plugin Python.

## Plugin shortcut scope

Menu actions may publish a single validated `shortcut` chord. The launcher installs Qt window-scoped bindings only for the active plugin's current view, removes old objects on mode/view changes, and guards callbacks against stale views. Pending calls, guide overlays and discard prompts disable ordinary custom actions; back/cancel remains available during calls/prompts. Ctrl+? is host help: KITE manual at root, plugin manual inside its runtime. HOWTO.md takes precedence over legacy README guides. There are no compositor-wide registrations.

`main/frontend/shortcut_hint.py` owns keycap/symbol rendering for footer, menu rows and declarative hints. A displayed hint is not itself a binding; `MenuAction.shortcut` is the opt-in runtime API.

## Notifications

`main/frontend/toast.py` supplies one closeable, nonmodal `ToastBanner` inside the existing popup. Plugin handlers may return a validated `Toast` alone to keep the current screen, or `View.toast` alongside refreshed content. A standalone Toast does not commit pending navigation; failed submitted forms clear masked fields. Search must still return View. Error notifications remain until dismissed, other tones expire after four seconds, and mode changes clear the banner. Notifications are not logged or persisted.

Built-in menu actions have explicit bindings. Copy/Favorites bindings are local to the open Actions panel; Help and Settings are host-window bindings. Plugin bindings are scoped to their published runtime view. There is no automatic assignment of arbitrary shortcut keys.

## Plugin execution and cancellation

The GUI reads bounded manifests without importing external plugin modules. It deduplicates canonical paths and rejects ambiguous/reserved IDs. Opening a tool or requesting ranking from a selected ranking plugin lazily starts its worker. Python entries use the bundled adapter; declared executable backends can use any language. Python imports and external executables never run in the GUI. Bundled presentation helpers remain trusted application code in the host.

By default, each external worker runs in a transient systemd **user** unit: 768 MiB resident-memory cap, 256 tasks and one CPU's quota, with whole-unit OOM cleanup. Control-group termination includes detached descendants. A separate stdlib supervisor watches the launcher pidfd; unexpected owner death stops its unit. Normal close waits asynchronously for cleanup; Python-unwind cleanup is also registered. At most one active and one retiring session exist per runtime, and the same plugin cannot restart until its prior process has exited.

Executable rankers may explicitly declare `execution: child`. The Rust example does: QProcess launches its compiled binary as a direct child with no persistent helper or binding. Exec-only Linux utilities enforce parent-death SIGKILL, a package-use lease, 768 MiB virtual address space, no forks/threads, 64 descriptors and no core dumps. Cancellation escalates SIGTERM to SIGKILL after 300 ms and cleanup waits for actual exit. This mode is for single-threaded rankers only; it does not change tool-plugin supervision. See [direct execution requirements](plugin-protocol.md#cancellation-lifecycle-and-trust).

Startup/import and search each allow five seconds; events allow 120 seconds. New work invalidates old revisions immediately. Cancellation gives 300 ms for cooperative cleanup, then stops the unit with a one-second hard-stop grace. Repeated typing does not extend that deadline. No shared plugin thread pool can be exhausted by two stuck calls.

Workers and host exchange newline-framed JSON-RPC 2.0 over private pipes. API major 2 and wire version 1 are separate identifiers. Frames/incomplete buffers are capped at 4 MiB, JSON nesting at 64, and bursts/floods are bounded. Only known DTOs are reconstructed and validated; no pickle or arbitrary class loading. Python stdout is redirected away from the protocol; raw stderr and frames are not persisted.

Reverse host-service calls are asynchronous in Qt and scoped to a live worker generation and parent request. They reuse bounded provider processes; there is no persistent provider pool. Secret values/config/environment travel through pipes, not argv or temporary files. User plugins still receive copied application settings and desktop environment: this is **fault containment, not a security sandbox**.

Unavailable systemd user support disables supervised external execution with a visible error, never an in-process fallback. Explicit direct-child rankers use their separate checked Linux requirements and do not launch a systemd unit. Native launcher tools remain usable. Crashes/timeouts never replay create/copy/sync; an operation may already have committed. Failures keep safe browsing/form context and clear submitted masked inputs.

Typed service wrappers, plugin-defined configuration schemas, provider Qt removal, file-watch reload and non-Python adapters remain deferred. Existing Gopass-specific config validation is still a known core coupling.

## `--dmenu`

```sh
printf 'one\ntwo\n' | kite --dmenu
```

UTF-8 newline-separated choices go in; Enter writes the original selected line and exits **0**. Escape writes nothing and exits **1**. Blank lines are valid choices. Input is capped at 1 MiB / 10,000 choices, each at most 2,048 characters; invalid UTF-8 and NUL are rejected. Diagnostics go to stderr.

This picker is independent of normal single-instance IPC and does not turn choices into plugin/OS actions.

## Environment and installation

`pyproject.toml` declares runtime dependencies; `uv.lock` pins their resolution. `.venv/` is generated using system Python and system site-packages so existing PyGObject/Gio bindings remain available.

`install.sh` supports a local checkout or an explicit remote Git URL. It installs the user command, desktop entry and icon sizes, preserves configuration/plugins/state, and does not install system packages or enable examples. See [installation instructions](../README.md#setup-with-uv).

## Reference material

KITE's search/UI implementation was informed by inspectable public references, not proprietary code recovery:

- [Oma-Cast reference snapshot, commit 103aa67](https://github.com/typecraft-dev/oma-cast/tree/103aa67), including its ranking, frecency and root-search organization.
- [Omarchy style reference](https://github.com/basecamp/omarchy/blob/2fca7fe073e5ef71cd066ddb0d2294ad067deaf5/shell/Commons/Style.qml), for shared visual roles; current YAML/user choices take precedence.
- [Raycast search](https://manual.raycast.com/search-bar), [calculator](https://manual.raycast.com/calculator), [settings](https://manual.raycast.com/settings) and [extension concepts](https://developers.raycast.com/basics/create-your-first-extension), as public behavior references only.

No Oma-Cast runtime modules, QML or assets are redistributed as KITE runtime. This is an independent Python implementation, not a claim of identical behavior or recovered private algorithms. The earlier inspection did not find a license file in that reference snapshot; do not assume permission to copy its source/assets.
