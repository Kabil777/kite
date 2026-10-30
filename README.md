# KITE — a native Qt launcher

![KITE icon](assets/icons/kite.png)

Open apps, find an emoji, use clipboard history, switch windows or do a quick calculation—all from one keyboard-friendly Linux window.

![KITE application search](assets/screenshots/preview-apps.png)

**New here?** Follow the setup below, run KITE, and type an app's name. You can leave all settings at their defaults. The optional Rust search engine and extra plugins can wait until the basics work.

- **Application:** [Kabil777/kite](https://github.com/Kabil777/kite)
- **Plugin registry:** [Kabil777/kite_plugins](https://github.com/Kabil777/kite_plugins)
- **Everyday help:** [User guide](docs/howto.md)

## Setup with uv

### 1. Install the basics

You need Linux, **system Python 3.14 or newer**, Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), system PyGObject/Gio bindings, and `xdg-utils`. KITE's installer downloads its Python dependencies with uv; it does not install system packages or use `sudo` for you.

On Arch Linux or Manjaro, for example:

```sh
sudo pacman -S --needed python python-gobject git uv xdg-utils
```

Check your system Python before continuing:

```sh
python3 --version
python3 -c 'from gi.repository import Gio; print("Gio is available")'
```

If your distribution ships an older Python, this version of KITE is not ready for that environment yet. A uv-managed Python on its own does not supply matching system Gio bindings.

### 2. Download and install KITE

```sh
git clone https://github.com/Kabil777/kite.git
cd kite
bash install.sh
```

Prefer Git over SSH? Use `git clone git@github.com:Kabil777/kite.git` instead. HTTPS is simpler if you have not set up a GitHub SSH key.

The installer creates `~/.local/bin/kite`, a desktop-menu entry, and KITE's private Python environment under `~/.local/share/kite/`. It preserves your existing configuration, plugins and launch history. Run it as your normal user—not with `sudo`.

### Installer

The checkout-based install above is recommended: you can inspect the script first and keep the checkout for updates.

To update later, **close KITE**, return to your checkout, and run:

```sh
git pull --ff-only
bash install.sh
```

Do not delete your config or plugin folders to update the app. See [development setup](docs/development.md) if you want to run directly from source rather than install.

## Run

```sh
~/.local/bin/kite
```

Type an application name, select it with **↑ / ↓**, and press **Enter**. **Escape** goes back or closes the main window. **Ctrl+K** opens actions for the selected result; **Ctrl+?** opens help.

If `kite` is already on your PATH, you can use that shorter command. Running it a second time toggles the existing window closed. You can also open KITE from your desktop's application menu.

For a global shortcut, open your desktop's keyboard settings and bind your preferred key combination to `~/.local/bin/kite`. KITE does not change your bindings automatically.

Optional binding in your active `~/.config/hypr/hyprbinds.lua`:

```lua
hl.bind("SUPER + SPACE", hl.dsp.exec_cmd('"$HOME/.local/bin/kite"'))
```

Apply binding with `hyprctl reload`. No binding installed automatically.

## Add optional tools and plugins

Start with the built-in search and calculator. Add only the extras you need:

| Feature | Extra setup |
| --- | --- |
| Copy on Wayland | Install `wl-clipboard` |
| Copy on X11 | Install `xclip`; text-only `xsel` is also supported |
| Clipboard history | Install `cliphist` and configure its compatible clipboard watcher |
| Switch windows | Follow the [requirements for your desktop](#window-switching-across-desktops) |
| Extra tool plugins | Systemd user services and cgroup v2; check the plugin's own guide |
| Rust search | Optional [build and setup guide](examples/rust-ranking/HOWTO.md); Python search remains the default |

To connect the official plugin registry, close KITE and run:

```sh
~/.local/bin/kite plugins source https://raw.githubusercontent.com/Kabil777/kite_plugins/main/index.json
~/.local/bin/kite plugins search
```

You can also paste that address into **Plugin marketplace → Configure → Save source**, then choose **Browse**.

**The marketplace catalog is currently empty.** No marketplace releases are advertised until reviewed ZIPs and real checksums are available. The registry already contains the optional [Passwords/Gopass source package](https://github.com/Kabil777/kite_plugins/tree/main/plugins/gopass); see the opt-in example below. The [Rust ranking example](examples/rust-ranking/HOWTO.md) remains in this checkout. Do not treat a repository source snapshot as a reviewed marketplace release.

Install, allow and open are separate actions. Review third-party code first: plugins can access your user's files and network. Read the [plugin registry guide](https://github.com/Kabil777/kite_plugins) before installing a package.

### Example: add Passwords with Gopass (optional)

**Gopass is not bundled with or installed by KITE's default installer.** It lives in the registry repository and is added only if you choose it. Existing user-installed plugins are preserved during KITE upgrades.

Install and configure `gopass` separately first. Close KITE, then use a persistent registry checkout; this example uses `~/Projects/kite_plugins`:

```sh
mkdir -p "$HOME/Projects"
git clone https://github.com/Kabil777/kite_plugins.git "$HOME/Projects/kite_plugins"
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/kite/plugin"
ln -sT -- "$HOME/Projects/kite_plugins/plugins/gopass" \
  "${XDG_CONFIG_HOME:-$HOME/.config}/kite/plugin/gopass"
```

If you already have that checkout, reuse it instead of cloning again. If Passwords is already installed or the link exists, do not overwrite it or add a second copy. Reopen KITE, search for **Passwords**, and use **Options → Allow in KITE** if needed. A YAML disable still takes precedence. Adding the link does not open the plugin, unlock entries or sync a store.

Read the plugin's [README](https://github.com/Kabil777/kite_plugins/blob/main/plugins/gopass/README.md) for setup and its [user guide](https://github.com/Kabil777/kite_plugins/blob/main/plugins/gopass/HOWTO.md) for browsing, copying and syncing. Keep your password store outside both repositories.

## Documentation

| Guide | Start here when you want to… |
| --- | --- |
| [User manual](docs/howto.md) | Get started, use tools, navigate and recover from errors |
| [Frontend](docs/frontend.md) | Use UI components, build forms, render Markdown or preview images |
| [Core](docs/core.md) | Understand queries, configuration, providers, state and execution |
| [Plugin integration](docs/plugins.md) | Create, configure and install a plugin |
| [Any-language backends](docs/plugin-protocol.md) | Wire backend executables to Python/Qt and replace root ranking |
| [Development](docs/development.md) | Run from source, build Rust, and understand the test commands |
| [Plugin registry](https://github.com/Kabil777/kite_plugins) | Browse published packages or contribute a reviewed release |

**You can stop here for a first install.** The sections below are optional settings and reference material.

## YAML configuration and theme

Edit **`~/.config/kite/config.yaml`**. Close/reopen launcher to apply. Full defaults/reference: **`~/.local/share/kite/main/defaults.yaml`**. Unspecified fields inherit defaults; unknown keys, incorrect types, bad colors and out-of-range dimensions report errors rather than silently breaking layout.

```yaml
window:
  width: 750
  height: 520
  padding: 8           # horizontal result-list inset; vertical inset is 4px
  radius: 8
search:
  height: 72
  padding: 18
  font_family: Adwaita Sans
  font_size: 20        # logical pixels; medium weight
  placeholder: Search for apps and commands…
results:
  row_height: 48
  layout: inline      # stacked also supported; raise row_height for two lines
  padding: 12
  gap: 0
  limit: 60
  font_family: JetBrainsMono Nerd Font
  font_size: 16        # logical pixels; semibold title, 14px secondary text
icons:
  size: 28            # themed app/file images; command glyphs use 19px
  search_size: 20
  theme: WhiteSur-dark
footer:
  visible: true
  height: 46
colors:
  background: '#232428'
  search: '#2a2b30'
  selected: '#373940'
  text: '#f1f2f5'
  muted: '#a1a4af'
  border: '#41434b'
  accent: '#8fb9ff'
math:
  enabled: true
  debounce_ms: 100
  timeout_ms: 2500
web:
  enabled: true              # Google suggestions; browser search remains available
  suggest_in_root: false    # see privacy below
  debounce_ms: 250
  timeout_ms: 2000
  min_characters: 2
  max_suggestions: 8
  suggestion_font_size: 16   # Google suggestion titles; logical pixels
emoji:
  columns: 8                # 4–12; cells fit the available window width
  font_size: 32             # color emoji size; logical pixels, 16–64
ranking:
  engine: builtin      # or an enabled role: ranking plugin ID
  sensitivity: medium # low accepts weak subsequences; high is stricter
  learn_queries: true # local app/command launch terms only; false disables learning/use
favorites:
  - cmd:clipboard     # ordered IDs, not display names
aliases:
  - {id: cmd:clipboard, alias: cb}
extensions: []
```

Dimensions and font sizes use **logical pixels** (font sizes formerly used Qt points). Main search uses **Adwaita Sans**; result/footer/action text keeps **JetBrainsMono Nerd Font** with larger sizes and medium/semibold weights. This readable profile supersedes the initial literal, smaller Oma-Cast type scale. There are no Omarchy brand logos in the header/footer.

**Recommended:** install a [Nerd Font](https://www.nerdfonts.com/font-downloads), such as **JetBrainsMono Nerd Font** or **Symbols Nerd Font**, for command/action/service icons. KITE prefers an installed Nerd Font only when it contains the requested glyph; otherwise it uses native theme/default icons, not missing-character boxes. No font is installed automatically; restart KITE after installing one. Ordinary app/file/window artwork still comes from `icons.theme` (default **WhiteSur-dark**, with native fallbacks), and emoji keep their color font.

Passwords uses the **Gopass gopher mascot**. Vault/account names get local service/category matches (for example GitHub, mail, SSH, work or banking), with generic folder/key defaults for unknown names. Account names are never sent to a favicon service or used for image downloads. [Artwork credits and licenses](assets/icons/README.md).

`web.suggestion_font_size` independently controls Google autocomplete titles; `results.font_size` controls local app/command suggestions. Selected icons are not recolored blue—only the result background/focus outline changes.

Web and plugin modes show one `Results · N` heading; plugin-defined headings override the default. Emoji keeps a `Results` label above its square tile grid, with name tooltips, a neutral focus outline, and a transient copy-confirmation pill. Copying an emoji keeps the picker open. Grid columns and emoji size are configurable under `emoji`.

Scrollbars are hidden, but wheel/keyboard scrolling still works. Tool context stays in the footer and placeholder, with no separate title strips. Pending mode/query work shows an indeterminate loading bar and, where the list is empty, `Loading…` instead of an empty-results error. Idle web/calculator views invite input rather than spinning forever. At startup, a valid `~/.cache/kite/applications.json` (honoring `XDG_CACHE_HOME`) supplies application metadata immediately while Gio refreshes the list in the background. Only a cache miss shows `Loading applications…` during indexing. Gio application-database notifications trigger debounced background refreshes for installs, removals and edits while KITE is open. Unchanged results do not rebuild the ranking catalog; refresh failures preserve cached applications. The cache contains metadata only, is bounded and atomically replaced, and is scoped to application directories, desktop and locale. Cached rows can be shown provisionally with Python ranking while the Rust child initializes; activation waits for its validated reply. There is no artificial wait.

## Search and controls

- Type an app/command name for immediate local ranked results.
- Type math directly into root search: result updates automatically; Enter copies the answer.
- Empty root groups Favorites → Plugins → Applications. Favorites never duplicate in other groups; the Plugins group includes built-in modes and installed plugin entries. Section headers cannot be selected.
- Root text has Google and DuckDuckGo fallback rows. Creating these rows sends nothing over the network.
- Root shortcuts: type **`;`** to open Clipboard or **`"`** to open Emoji. Text after the marker becomes the mode's search query; Escape returns to root. Markers are not intercepted inside plugins/tools.
- Shortcut hints use a shared frontend component with **↩** (Enter), **⌥** (Alt), **⌃** (Ctrl) and **⇧** (Shift); actual Linux modifier bindings are unchanged. **Ctrl+?** toggles context-aware How to: KITE's user manual at root, the plugin's manual inside its runtime. Plugin-specific bindings exist only while their current view publishes them; they do not leak into root. Built-in tool entries are labelled **Plugin**, like installed plugins.
- Press **Ctrl+L** or open **Search web** for live Google autocomplete. Legacy `? query` switches into web mode and removes `?` from the input; leading `=` similarly switches into Calculator and disappears. Actual equation operators such as `x^2=4` remain intact.
- **Ctrl+K** or footer **Actions** opens actions for selected result: primary action, add/remove/reorder Favorites, copy name/ID, copy desktop ID, or copy math question + answer; **Edit YAML Settings** opens `config.yaml` in `$EDITOR` (Kitty used when available). Filter actions with the panel's bottom search field. Escape/Ctrl+K or an outside click dismisses it without closing the launcher.
- Favorites are ordered in `$XDG_STATE_HOME/kite/favorites.sqlite` (stdlib SQLite); existing YAML `favorites:` IDs seed the database once. UI edits never rewrite YAML appearance overrides. Add/remove and move favorites with selected-result Actions. Aliases remain in YAML and match exactly or by prefix, not arbitrary subsequences. Get stable IDs through **Actions → Copy Result ID**.
- Up/Down or Ctrl+P/Ctrl+N: select result. Enter: activate.
- Tab on a web result: enter web mode and complete its plain text, without inserting a `?` marker or opening a browser.
- Ctrl+Enter: search current text in browser immediately.
- Escape closes the current overlay first, then unwinds navigation one screen at a time, restoring query/selection. Dirty forms ask before discarding; Escape on that prompt keeps editing. At root with no history, Escape closes KITE.
- Emoji: Left/Right moves one tile, Up/Down moves one grid row. Click/Enter copies and shows a toast without closing the picker; Escape returns to root.
- All copies—including calculator, clipboard history and passwords—keep KITE open and show a toast, preserving query/selection. Paste manually with Ctrl+V. Text and image clipboard items use existing cliphist watchers; no new clipboard service installed.
- Files browses home and selected directories. Backspace with empty search goes to parent. No recursive file index.
- Window switcher uses the current desktop's native interface or an optional helper. Missing support reports a setup error rather than pretending there are no windows.

Direct modes:

```sh
kite calc
kite emoji
kite clipboard
kite web
kite files
kite windows
```

`kite --dmenu` reads UTF-8 newline-separated choices from stdin, opens the Qt picker, and prints the selected line to stdout on Enter. Escape returns exit status 1 without output; successful selection returns 0. Empty lines are valid choices. Input is capped at 1MiB/10,000 choices and each choice at 2,048 characters. Diagnostics go to stderr; this mode opens independently rather than using launcher IPC.

```sh
printf 'one\ntwo\n' | kite --dmenu
```

## Window switching across desktops

Open **Switch windows** (or `kite windows`), search by title/app and choose a window. Listing and focusing share the same desktop backend; window actions carry a validated backend-qualified ID. The feature does not depend on Hyprland alone.

| Session | Interface / optional setup |
| --- | --- |
| Hyprland | Existing `hyprctl` JSON listing and Lua focus API |
| X11: GNOME, KDE, Xfce, Cinnamon, MATE, LXQt and other EWMH-compatible window managers | Install `wmctrl`; native window IDs select the exact target |
| Sway | `swaymsg` and a working Sway IPC socket; tiled and floating windows are included |
| niri | `niri msg` and working niri IPC |
| KDE Plasma 6 Wayland | Install a recent [kdotool](https://github.com/jinliu/kdotool) supporting `kwinscript`; KITE takes one JSON snapshot with a fixed script and activates by KWin UUID |
| GNOME Wayland | Enable a compatible [Window Commander](https://github.com/gnikolaos/window-commander) extension (`window-commander@gnikolaos.gr`). Inspected upstream v11 declares GNOME 49/50; older shells need a compatible release. KITE uses its list/details/activate D-Bus methods, never unsafe Shell Eval |
| Other Wayland sessions exposing wlroots foreign-toplevel management | Optional `wlrctl` with `toplevel list/focus`. Limited fallback: exact app/title selectors only; duplicate targets or malformed records are rejected |

Install optional system tools yourself with your distribution's package manager. KITE does not install or enable helpers/extensions, add a background service or change desktop settings. Session hints prioritize probes, but an installed command alone is not evidence of a working interface.

**Not every Wayland compositor permits external window control.** Missing/unsupported interfaces produce an actionable error; a Wayland session never silently falls back to an incomplete XWayland-only list. The wlroots fallback lacks stable window IDs and cannot safely distinguish identical app/title pairs. Compositor focus policies still apply; helper success acknowledges a request, not a guarantee that the compositor will grant focus.

**Verification:** these integrations are implemented but were not exercised against live desktops in this change. No window list was collected, no focus changed, no other DE installed and no KITE GUI launched. Do not treat the matrix as cross-desktop certification.

## Clipboard copying across Linux desktops

All copy actions—including calculator, emoji, passwords, result menus and plugin clipboard services—use the same setting in `~/.config/kite/config.yaml`:

```yaml
clipboard:
  copy_command: auto
```

`auto` chooses `wl-copy` for a Wayland session, otherwise `xclip` for an X11 display, then `xsel` for text only. Detection uses display/session environment, not GNOME/KDE/other desktop names. A Wayland session never silently falls back to XWayland. Missing tools/display support produce a visible error.

To override detection, use an argv list, never a shell command string:

```yaml
clipboard:
  copy_command: ["xclip", "-selection", "clipboard", "-in", "-target", "{mime}"]
```

`{mime}` is the only substitution; clipboard bytes always go through stdin, never argv. Automatic backends preserve raw bytes and infer MIME for restored history items. Custom commands without `{mime}` support text only; binary copying fails rather than assuming MIME support. Commands have a three-second completion timeout and failures are never retried automatically. A failed/timed-out command may already have changed the clipboard. Restart KITE after changing settings.

Copying, clipboard-history collection and window switching are separate capabilities with separate requirements. No new clipboard watcher/service is installed.

## Clipboard history

Clipboard mode has a history list, an in-panel content-type filter, and a preview/details pane. All Types, Text, Images, Files, Links and hex Colors filters stay local. Empty queries preserve cliphist's newest-first order. Single click selects; Enter, double-click or the footer copies the original item. No sidebar image thumbnails: image entries use a standard glyph.

Image previews preserve aspect ratio inside 10% insets on each side. Text previews are plain, scrollable, and padded by 14px; clipboard HTML is never interpreted. Information uses a bold header and compact, separator-free rows for content type, size and image dimensions. The filter supports mouse, arrows, Enter, Escape and outside-click dismissal without native Wayland popup grabs.

Only the selected item is decoded, after a 90ms debounce. New selections/mode changes cancel old preview work and stale callbacks are ignored. Preview data stays in memory; the existing cliphist database is neither migrated nor duplicated. Cliphist does not retain source-app names, original image filenames or copy timestamps, so those fields are not invented. Previews are bounded to 720×420 images and 12,000 text characters; copying still restores the full original.

## Math examples and limits

```text
(24 * 3) / 2
sqrt 81
sin 30
cos 60 + sin 30
sin 10r
sin (pi/2)r
sin(pi/2r)
asin 0.5
asin 0.5r
2pi
2(3+4)
2 sin 30
log10(1000)
round(pi,2)*100
20% of 150
x^2=4
solve(x^2-4,x)
diff(x^3,x)
integrate(2*x,x)
factor(x^2-4)
= simplify((x^2-1)/(x-1))
```

Supports exact arithmetic and approximate numeric previews, complex numbers (`i`), symbols `x y z t`, equations, common trig/log functions, derivatives, integrals, limits, factoring and expansion through SymPy. Unary functions accept shorthand; multiple arguments still need parentheses, such as `round(pi,2)` or `solve(x^2-4,x)`. Implicit multiplication accepts `2x`, `2pi`, `2(3+4)` and `2 sin 30`; explicit `2*x` remains valid. `%` alone means remainder, while `20% of 150` means percentage.

**Angles now default to degrees**, including existing parentheses: `sin(30)` is 1/2. This intentionally changes the previous radian default, including `math.calculate` host-service calls. Add `r` to request radians: `sin 10r`, `sin (pi/2)r` or `sin(pi/2r)`. The suffix marks the whole trig argument, not a free variable or arbitrary unit. Inverse trig returns degrees by default (`asin 0.5` → 30); `asin 0.5r` returns pi/6 radians.

Shorthand ends before an explicit outside operator: `sin 10 + cos 20` means `sin(10) + cos(20)`; `sin 10 * 2` doubles the result, while `sin (10*2)` takes sine of 20 degrees. Powers and implicit products stay inside the argument: `sin 2pi` means `sin(2*pi)` in degree mode. Use parentheses to make the intended grouping explicit. Separate numbers such as `1 2` are rejected rather than guessed.

Live results retain the existing 100ms debounce and stale-job cancellation. Unfinished input such as `sin (` or `2+` stays quiet. A genuine error becomes a closeable toast after a further 600ms pause; edits cancel pending errors and dismiss only calculator-owned error toasts, not unrelated copy/plugin notifications.

Not literally every math question: no general natural-language solver, unit/currency conversion or date/time arithmetic. Prefix `=` to force math detection; Calculator mode also attempts every input. Token normalization builds an allowlisted AST without calling `eval`, `parse_expr` or `sympify` on user text. Existing input/AST/literal/exponent/factorial limits and CPU/memory/wall-clock bounds remain. Heavy problems may time out intentionally.

Manual provider observations for this change: `sin 30 + cos 60 + 2(3+4)` → 15; `sin(pi/2r)` → 1; `asin 0.5r` → pi/6; `sin -30` → -1/2; `solve(x^2-4,x)` → [-2, 2]. `sin (` returned an incomplete-input error; `1/0` returned an undefined-result error. No automated tests were written/run, no clipboard operation occurred, and GUI toast timing remains unverified.

## Google autocomplete privacy

By default, app, emoji and clipboard queries stay local. Installed trusted plugins can perform their own network/IO; the suggestion privacy switch does not sandbox them. **Web mode (including entering it with `?`) sends the query to Google's suggestion endpoint while typing**, after a 250ms debounce and two characters. Selecting a search opens Google in your browser. Google may see your IP and query; no API key/cookies are supplied by this app.

Set `web.suggest_in_root: true` if you explicitly want ordinary root-search text sent as well. Math-like input and extension prefixes are excluded from that automatic path. Set `web.enabled: false` to disable suggestions entirely. Suggestions are cached only in memory, never saved. App/command launch counts, decaying frecency anchors, last-launch timestamps, and up to **20 successful launch terms per item** are stored locally in `~/.local/state/kite/usage.json` (new writes use mode 0600). Terms boost ranking for 17 days after last launch. This is **not** a log of every keystroke: only text used to activate an app or built-in command from root is learned.

Selecting an external `ranking.engine` shares root queries, candidate metadata, aliases and candidate usage with that executable; it also receives the standard copied configuration/environment. Only select trusted backends. Set **`ranking.learn_queries: false`** to stop learning and using launch terms; delete `usage.json` with launcher closed to erase existing history. Math, emoji, clipboard contents, web queries and extension queries are not saved. Existing cliphist history is independent. Old count/last history is read without destructive migration; unreadable/malformed history is preserved with writes disabled for that session.

## Plugin marketplace

Search **Plugin marketplace** to manage plugins inside KITE's existing window. Its search-bar dropdown offers **Installed / Browse / Configure**; **Actions (Ctrl+K)** contains package operations, refresh and cleanup/recovery. Source setup and package approval use the shared full-window forms, not separate dialogs. Configure `https://raw.githubusercontent.com/Kabil777/kite_plugins/main/index.json` as the source. The [official registry](https://github.com/Kabil777/kite_plugins) is currently empty until reviewed release archives and checksums are available; the local `catalog/index.json` is a development fixture, not the live registry. [Catalog and author publishing guide](catalog/README.md).

The same backend is available without loading Qt:

```sh
kite plugins source "$KITE_PLUGIN_CATALOG_URL"
kite plugins search
kite plugins list
kite plugins info hello
kite plugins install hello       # review manual/origin, then approve
kite plugins disable hello
kite plugins enable hello
kite plugins update hello
kite plugins uninstall hello
```

`hello` is a documentation ID, not a currently published catalog package. Close KITE for CLI mutations; search/list/info can run while it is open. Install/update/uninstall require interactive approval or deliberate `--yes`; no safety validation is bypassed. There is no live-CLI IPC, automatic update, dependency installation, hook execution or Git clone. Public GitHub Release ZIPs must be catalog-listed and match the approved SHA-256; manual/authored roots remain supported.

Installation enables a package but never imports or runs it. Opening starts its existing contained worker lazily. Effective enablement is **YAML permission AND manager permission**; GUI management never rewrites YAML or overrides an explicit disable. Containment limits failures, **not user permissions**; downloaded code can access files, secrets and network. A checksum identifies bytes, not safe code.

Updates/uninstall wait for worker and host-service cleanup, then commit versioned payload links with an fsynced registry/recovery journal. Unexpected links, authored/development conflicts, modified publisher/source identity and uncertain cleanup fail closed. Uninstall preserves settings and external stores; edited/extra payload files are retained in private `plugin-backups/`. Use **Check cleanup / recover** or close KITE and retry a CLI mutation after an interruption. Cancel cannot undo a durable commit. [Lifecycle, trust and recovery details](docs/plugins.md#marketplace-and-package-lifecycle).

Package-manager verification is one isolated check:

```sh
uv run --locked --no-sync python -m tests.check_plugin_manager
```

It uses temporary XDG roots, inert ZIPs and mocked network/systemd/cleanup; it does not launch KITE, activate real plugins, publish a catalog or access live stores. Native GUI interactions and real desktop/catalog activation require separate verification permission.

## Plugin API (v2)

User-authored plugins in any language are discovered directly under `${XDG_CONFIG_HOME:-~/.config}/kite/plugin/<id>/`; installed packages live under `${XDG_DATA_HOME:-~/.local/share}/kite/plugins/<id>/`. Each folder has `plugin.json` (`id`, `name`, `api_version: 2`) and either a Python entrypoint or `command` argv for a language-neutral backend. Links resolving to the same package are deduplicated; different packages with the same ID are rejected. They default to enabled unless disabled by YAML or manager preferences; each plugin reads its own values with `plugin_config(host, '<id>')`. After separately installing the optional Gopass package, for example:

```yaml
plugins:
  gopass:
    enabled: true
    config:
      store: '' # mounted-store alias; empty uses root
```

Set `enabled: false` to disable a user plugin; its Python entrypoint is not loaded. Plugin owns defaults/validation for its `config` keys. Gopass source is maintained in [kite_plugins/plugins/gopass](https://github.com/Kabil777/kite_plugins/tree/main/plugins/gopass), not in this application checkout. Use its explicit opt-in guide; the core installer neither copies nor registers it. Do not maintain two edited copies. Manager operations refresh discovery explicitly; restart after manual source/manifest changes. Uninstall manager-owned code before replacing its link with `--link-example`. Bundled modes remain host-managed. Start with [Plugin integration](docs/plugins.md); [Frontend](docs/frontend.md) documents every component and its properties/events.

Folder roles stay separate by lifecycle: `examples/` holds demos to copy (never auto-loaded), `plugins/` holds trusted user-installed code discovered at runtime, and `main/bundled_plugins/` holds first-party features shipped and managed by launcher.

Entrypoint exports `search(query, host) -> plugin_api.View` and optional `handle_event(event, host) -> View | Toast`. Optional `configuration` manifest fields declare `{key,label,type,default,placeholder}` text settings or `{key,label,type:"choice",default,options}` dropdowns. KITE uses the packaged defaults and lets users override them via Installed → Actions → Settings; overrides are stored under `plugins.<id>.config` in `config.yaml`. `plugin_api.py` is stable import surface; do not import launcher/core internals or subclass host classes. `View` contains a `Node` tree, `Result` list, optional title/error, `actions` for plugin-defined `MenuAction` entries in the host Actions menu, and optional `toast` status. Return a standalone `Toast` from an event to notify without replacing the screen. Nodes: `column`/`row` (`spacing`), `label` (`text`, `wrap`), `icon` (`name`, `size`), `button` (`text`, `activate` event), `text_input` (`text`, `placeholder`, `password`, `change` event), `select` (`options`, `change` event), `list` (`items`, `activate` event), `markdown` (`text`, up to 128 KiB), `image_preview` (`source`: absolute local raster path, `alt`), the shared borderless `type_label` (`text`), the full-width root `form` (title/description/error, field children, submit event), and display-only `shortcut_hint` (`keys`). Markdown does not load embedded images/resources or interpret raw HTML; explicit HTTP(S) links are allowed. Image previews use bounded decoding and retain aspect ratio when resized. Each node has stable `id`, children, event declarations, and accessibility label. Results carry `id`, `title`, `subtitle`, `icon`, and optional action: `copy`/`open` (string), `exec` (argv list), or `activate` (plugin event). `Event` carries node ID, name and payload. Host validates view trees/actions before rendering.

`host.config` exposes copied validated YAML; `host.cancelled()` lets work stop cooperatively. GUI discovery reads metadata only. External imports/calls normally run in a Python adapter or a declared backend executable inside a transient systemd user unit, with hard deadlines and whole-group cleanup. Executable rankers may explicitly choose `execution: child`; the Rust example runs as one persistent direct child with kernel owner-death cleanup, package leases, a 768-MiB virtual-address-space limit and no forks/threads, without a Rust binding or persistent helper. Startup/search allow 5 seconds, events 120 seconds; units are capped at 768 MiB resident memory, 256 tasks and one CPU quota. Stale replies are discarded and failed writes are never automatically retried. This is **fault containment, not a security sandbox**: plugins retain user privileges and desktop environment, so only install code you trust. Declarative UI controls rendering, not code access. Shared theme/fonts/colors/geometry come from existing YAML; plugins describe structure, not global styling. Clipboard, emoji and web are trusted bundled adapters using the current DTO contract with native host rendering, preserving their original YAML styling and behavior. Root ranking is a replaceable plugin role: the bundled policy remains default, while permitted `role: ranking` packages can replace matching, filtering and ordering via `ranking.engine`. See [the shared worker/ranking protocol](docs/plugin-protocol.md). Calculator, files and windows expose explicit host-service adapters. Existing JSON extensions remain compatible.

Example gopass plugin groups existing `domain/p1`, `domain/p2` folders as **Vaults**, without renaming paths, then shows accounts with shared borderless context labels. Browsing never decrypts secrets; selecting an entry unlocks it and offers explicit **Copy password** and **Copy path**. Escape returns through passwords → Vaults → launcher; no Back/Return rows or decrypted-view history are kept. Interactive decryption/creation and manual sync allow 120 seconds for authentication; metadata queries keep a five-second limit. **Actions → Sync passwords / Ctrl+S** stays available while browsing and never runs automatically. It syncs with a configured Git remote; otherwise a closeable error toast explains the setup problem; sync is no longer a search-result row. Inside Gopass, **Alt+I** or **Ctrl+K → Create Password** opens a full-width reusable Form with shared input/button styling and a masked password field. Save uses `gopass insert` via stdin, never argv or preview. Cancel/Escape confirms unsaved edits before leaving. Its package declares a mounted-store alias default and text-field option; override it from Installed → Passwords → Actions → Settings or under `plugins.gopass.config.store`; step-by-step usage and private remote setup: [Gopass user guide](https://github.com/Kabil777/kite_plugins/blob/main/plugins/gopass/HOWTO.md). Gopass exit 11 means decryption failed; check store recipients/key or pinentry. Tests use fake Gopass/copy commands; sync checks use temporary local Git repositories, never GitHub or a live password store. Bundled plugins also keep separate `backend.py` modules and local guide files; their module organization is not an external-worker fault boundary.

## Foreground verification

After setting up the uv environment, run focused checks for the area you changed. The broad historical suite has [known verification gaps](docs/development.md#known-verification-gaps); this command list is not a claim that every check currently passes.

```sh
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_frontend
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_plugin_runtime
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_plugin_baseline
uv run --locked --no-sync python -m tests.check_direct_ranking
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_applications
uv run --locked --no-sync python -m tests.check_docs
uv run --locked --no-sync python -m tests.check_clipboard
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_copy_feedback
uv run --locked --no-sync python -m tests.check_gopass_sync
```

Checks use temporary packages/state, fake clipboard/Gopass operations and owned transient worker units. Runtime containment checks require the systemd user manager; do not report them as passing when that environment is unavailable. No real credentials, live password stores or network Git access are needed. The sync fixture runs real local Git pull/rebase/push through a fake Gopass executable; it does not verify an installed Gopass version or GitHub authentication.

## Python extensions

Extensions are **trusted executable programs**, not a sandbox. Only install code you trust. No shell interpolation: commands are argv lists. Each extension runs only for its configured prefix; it receives a single JSON request on stdin and returns a single JSON object on stdout. Logs belong on stderr.

Example in `config.yaml`:

```yaml
extensions:
  - name: Uppercase
    prefix: 'upper '
    command:
      - /usr/bin/python3
      - /absolute/path/to/kite/examples/uppercase.py
```

Replace `/absolute/path/to/kite` with your installation directory. Extension commands are argv arrays: literal `~` and `$HOME` are not expanded by a shell.

Type `upper hello`. Supplied `examples/uppercase.py` returns a copy action. Protocol:

```json
{"version": 1, "query": "hello"}
```

Response:

```json
{
  "results": [{
    "title": "HELLO",
    "subtitle": "Enter to copy",
    "icon": "edit-copy",
    "action": {"type": "copy", "value": "HELLO"}
  }]
}
```

Actions: `copy` (string), `open` (HTTP(S) URL or local path), `exec` (argv list). Exec is deliberately powerful and only for trusted providers. Providers have a 2.5s timeout, 256KiB stdout budget and 100-result limit. Obsolete processes are killed when the query changes; results carry a query generation so stale replies cannot overwrite new ones. No extension can register arbitrary inline Python inside the YAML file.

## Files, checks, screenshots

- `install.sh`: local/remote user installer; `pyproject.toml` + `uv.lock`: Python environment.
- `main.py`: stable launcher entrypoint; implementation lives in `main/`.
- `main/`: launcher orchestration, providers, Qt-free plugin contracts/codec/worker, Qt process client, defaults and emoji data.
- `main/frontend/`: reusable Markdown/image-preview components, declarative renderer, and shared Qt widgets. Existing `main.widgets`/`main.plugin_ui` imports remain compatibility facades.
- `main/bundled_plugins/`: first-party clipboard/emoji/web packages.
- `plugin_api.py`: compatibility import for installed plugins (`from plugin_api import ...`).
- `tests/`: deterministic checks; `assets/screenshots/`: retained documentation captures.
- `examples/`: opt-in sample plugins; `plugins/`: runtime-discovered user installs.
- [`docs/frontend.md`](docs/frontend.md): every UI component, properties/events, examples and renderer ownership.
- [`docs/core.md`](docs/core.md): configuration, query flow, ranking, providers, state and privacy.
- [`docs/plugins.md`](docs/plugins.md): build/install a plugin, handle events, call host services and add a guide.
- Screenshots: [root](assets/screenshots/preview-oma-root.png), [math](assets/screenshots/preview-math.png), [web](assets/screenshots/preview-web.png), [actions](assets/screenshots/preview-actions.png), [panel](assets/screenshots/preview-panel.png), [apps](assets/screenshots/preview-apps.png), [loading](assets/screenshots/preview-loading.png), [Qt regression](assets/screenshots/preview-qt.png), [emoji](assets/screenshots/preview-emoji.png).

From a checkout with its uv environment prepared:

```sh
uv run --locked --no-sync python -m tests.check_docs
uv run --locked --no-sync python -m tests.check_ranking
QT_QPA_PLATFORM=offscreen uv run --locked --no-sync python -m tests.check_frontend
```

See [development setup and test requirements](docs/development.md) before running lifecycle or desktop checks. Do not share your entire configuration or password-store contents when reporting an issue.

Runtime Python dependencies are uv-managed under `.venv`: PySide6-Essentials, PyYAML and SymPy. Existing system PyGObject/Gio bindings remain available through system site-packages. Clipboard/window/browser operations use the configured copy command (auto: wl-copy or xclip/text-only xsel), cliphist, the appropriate optional desktop window interface and xdg-open. No browser engine, Node runtime or React dependency required.

## Remove

Close KITE first. Back up any local plugins or files you want to keep from its installation folder. Remove `~/.local/bin/kite`, `~/.local/share/kite/`, its `~/.local/share/applications/kite.desktop` entry and `~/.local/share/icons/hicolor/*/apps/kite.png` icons, plus any keybinding you added yourself.

Your settings and authored plugins in `~/.config/kite/`, launch history/favorites in `~/.local/state/kite/`, and cache in `~/.cache/kite/` can be kept for reinstalling. Delete them only if you want a full reset and have backed up anything important. Existing cliphist and Gopass stores are separate and are not removed by these steps. Custom XDG locations replace the default paths above.

## Acknowledgements

Some presentation patterns are inspired by [Oma-Cast](https://github.com/typecraft-dev/oma-cast). KITE's Python/Qt interface is independent; the optional Rust engine reuses only the explicitly licensed Flea matcher, with its [license and provenance](examples/rust-ranking/PROVENANCE.md) preserved. Icon credits are in [assets/icons/README.md](assets/icons/README.md).

Emoji data: Unicode 16.0, 3,781 fully-qualified entries. Source: https://unicode.org/Public/emoji/16.0/emoji-test.txt; terms: https://www.unicode.org/license.txt.
