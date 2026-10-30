# Frontend: building a view

KITE plugins describe what to show; KITE creates the Qt widgets. API-v2 external plugins execute in separate workers (Python or any executable language) and return validated data over private pipes. All backends use the same [language-neutral protocol](plugin-protocol.md) and declarative UI contract. Do **not** import GUI internals or create a window inside a plugin.

This guide explains every supported component. For a complete plugin package, start with [Plugin integration](plugins.md). For loading, cancellation and providers, see [Core](core.md).

## Two places to put content

A plugin returns a `View` with two independent parts:

- **`results`** fills the launcher's main searchable result list.
- **`nodes`** fills the plugin's custom detail pane, or the full content area when its only root node is `form`.

```python
from plugin_api import Action, Node, Result, View


def search(query, host):
    return View(
        nodes=(Node('label', 'hint', {'text': 'Select a result to copy it.'}),),
        results=(Result('example', 'Copy greeting', action=Action('copy', 'Hello!')),),
    )
```

`Result` is a result row, not a `Node`. Likewise, the embedded `list` component described below is not the launcher's main result list.

## Rules shared by every component

A component is a `Node(type, id, props, children, events, accessibility_label)`:

```python
Node(
    'button',
    'refresh',
    {'text': 'Refresh'},
    events={'activate': {'operation': 'reload'}},
    accessibility_label='Reload entries',
)
```

- Give each node a unique, stable ID. KITE uses it to route events and restore input focus/cursor position after a redraw.
- `props` contains only the properties documented for that component. Arbitrary Qt properties and CSS are not accepted.
- `children` is a **tuple**; `row`, `column` and the root `form` container accept it. A one-item tuple needs a trailing comma.
- `events` declares the interactions you want to handle. Values are JSON-compatible data, not Python callbacks.
- `accessibility_label` gives a useful name to assistive technology. Do not put secrets there.
- Each returned `View` replaces the previous view. Include everything that should remain visible.

Most text is plain text. Only the `markdown` component interprets Markdown; raw HTML is not enabled there either.

## 1. `column` and `row`: arrange components

Use a `column` for a vertical group or inline controls. Use the dedicated `form` component for full-width data-entry screens. Use a `row` for controls that belong beside each other.

```python
Node('column', 'form', {'spacing': 12}, children=(
    Node('label', 'heading', {'text': 'Find an entry'}),
    Node('row', 'controls', {'spacing': 8}, children=(
        Node('text_input', 'name', {'placeholder': 'Entry name'}),
        Node('button', 'find', {'text': 'Find'}, events={'activate': {}}),
    )),
))
```

`spacing` is an integer from **0 to 48** logical pixels; default is **8**. There are no layout events. Widths, colors and fonts remain host-controlled.

## 2. `label`: show plain text

Use a label for a field name, status message or short explanation.

```python
Node('label', 'status', {
    'text': 'No entries found. Try a different name.',
    'wrap': True,
})
```

Properties:

- `text`: string, up to 4,096 characters; default `''`.
- `wrap`: boolean; default `False`. Set it for sentences that may not fit on one line.

Labels have no events. `<b>Hello</b>` displays as text, not HTML. Use `markdown` if you want formatted content.

## 3. `icon`: show a small themed image

Use an icon beside a heading or control.

```python
Node('icon', 'folder-icon', {'name': 'folder', 'size': 24},
     accessibility_label='Folder')
```

- `name`: theme icon name or image path, up to 256 characters; default `application-x-executable`.
- `size`: integer from **12 to 96** pixels; default comes from `icons.size` in YAML.

This is a small image component, not a full preview. Use `image_preview` for content images. Font glyphs for launcher entries belong in the plugin manifest's `glyph` or `Result.glyph`, not an `icon` node property.

## 4. `button`: request an action

```python
Node('button', 'save', {'text': 'Save'},
     events={'activate': {'operation': 'save'}})
```

`text` is a string up to 4,096 characters. Optional `variant` is `primary`, `secondary` (default), or `danger`. Shared buttons use the global results font, consistent 40px height, clear focus indication and compact content-based widths. Primary actions use the accent fill; secondary actions stay neutral. Clicking emits `activate`. A button without a declared `activate` event is disabled; there is no separate `enabled` property.

The plugin handles it in `handle_event`:

```python
def handle_event(event, host):
    if event.node_id == 'save' and event.name == 'activate':
        name = event.payload['inputs'].get('name', '')
        # Validate the input, perform the operation, then return the next complete View.
        return View(nodes=(Node('label', 'saved', {'text': f'Saved {name}'}),))
    return View(error='Unsupported action')
```

The handler runs in the plugin's worker process, not in the GUI. See [the event contract](plugins.md#handling-events) before building a form.

## 5. `text_input`: collect text or a password

```python
Node('text_input', 'username', {
    'text': '',
    'placeholder': 'Username',
}, events={'change': {'field': 'username'}})

Node('text_input', 'password', {
    'placeholder': 'Password',
    'password': True,
})
```

- `text`: initial/current value; default `''`.
- `placeholder`: prompt shown when empty; default `''`.
- `password`: boolean, default `False`; masks displayed characters when enabled.
- `change`: optional event. User edits add `event.payload['text']`.

Text and placeholder are each limited to 4,096 characters. The field also caps entered text at 4,096 characters. All plugin fields use the shared 42px-high input style, theme surface/border colors, 14px text and an accent focus border.

You do not need a `change` event just to submit a form: button events already include every current input in `event.payload['inputs']`. Omitting per-keystroke events often makes a form simpler.

Masking is not encryption. Passwords still exist in host/worker memory and transient private-pipe data; do not put them in labels, logs, result titles or command-line arguments.

## 6. `select`: choose one option

```python
Node('select', 'scope', {
    'options': ['Personal', 'Work'],
    'value': 'Work',
}, events={'change': {}})
```

- `options`: list of at most 1,000 strings, each up to 512 characters.
- `value`: optional selected string; it must belong to `options`. Without it, the first option is selected when available.
- `change`: optional event with `event.payload['value']`.

The selected value is also available as `event.payload['inputs']['scope']` when another component emits an event. No event is sent merely because the initial value is set.

## 7. `list`: choose from an embedded list

```python
Node('list', 'accounts', {
    'items': ['Personal', 'Work'],
    'value': 'Personal',
}, events={'activate': {'operation': 'choose-account'}})
```

`items` and `value` follow the same size/membership rules as `select`. Activating an item emits `activate` with `event.payload['value']`. The currently selected item is included in the form-input snapshot as well.

Use this for a list **inside the detail pane**. For search results with icons, subtitles and actions, return `View.results` instead. Scrollbars are hidden, but the list still scrolls.

## 8. `markdown`: render documentation

````python
Node('markdown', 'instructions', {'text': '''## Getting started

Select an entry, then choose **Copy**.

> [!NOTE]
> Copying is an explicit action.

```sh
example-command --help
```
'''})
````

`text` defaults to `''` and is limited to **128 KiB of UTF-8**, not 128,000 characters. There are no plugin events.

The shared renderer provides:

- Monospace text and the configured launcher palette.
- 22/18/16px heading levels over a 14px body.
- Padded, bordered fenced code blocks and accented inline code.
- GitHub-style `NOTE`, `TIP`, `IMPORTANT`, `WARNING` and `CAUTION` callouts. Their following quoted paragraphs/lists stay inside the callout.
- Explicit HTTP(S) links. File, data and script links do not open.
- Hidden scrollbars with keyboard/wheel scrolling retained.

Raw HTML and embedded image/resource loading are disabled. Use a separate `image_preview` node for images rather than `![image](...)`.

### Automatic plugin guides

A plugin-local `HOWTO.md` does not require a `markdown` node. **Actions → How to / Ctrl+?** opens it inside the plugin; README is a compatibility fallback. At root, How to opens KITE's own user manual, regardless of selected result.

The guide uses the same renderer across the content panel, keeping the footer visible and the outer window border clear. Plugin name is on the left, **How to** on the right. Escape returns to results. KITE removes a leading Markdown title when it duplicates the plugin name. Guide files are also limited to 128 KiB.

## 9. `image_preview`: show a content image

```python
Node('image_preview', 'screenshot', {
    'source': '/absolute/path/screenshot.png',
    'alt': 'Preview of the selected screenshot',
})
```

- `source` is required: an **absolute local file path**, up to 4,096 characters. URLs and relative paths are not accepted; expand `~` yourself before returning it.
- `alt` is optional descriptive text, up to 512 characters.
- No plugin events are emitted.

The preview preserves aspect ratio, uses 80% of the available area and rescales when the component changes size. Invalid/missing files display `Image preview unavailable`.

Limits: a regular file, at most **16 MiB compressed**, **32 million pixels**, and **32,768 pixels per side**. PNG, JPEG, WebP, GIF, BMP, ICO, TIFF, AVIF and HEIF are accepted when the Qt decoder is available. SVG is deliberately excluded because it may reference other resources. No image is downloaded automatically.

## 10. `type_label`: a reusable type/context marker

Use this for the short right-hand marker in a row—not for its name or description:

```python
Node('row', 'account-row', children=(
    Node('icon', 'account-icon', {'name': 'dialog-password', 'size': 24}),
    Node('label', 'account-name', {'text': 'Work account'}),
    Node('type_label', 'account-type', {'text': 'Password'}),
))
```

`text` is optional, defaults to `''`, and is limited to 256 characters. There are no events or per-node styling properties. The component uses the global results font family at 12px/medium weight, muted theme color, right alignment and elision. **No border, background pill or padding is drawn.**

Native result rows use the same component's painter, so their type/domain markers follow that style too:

```text
<icon> <name> <short description>                  <type>
```

`Result.tag`, when set, replaces the usual type caption with that context—for example `example.com`. The name/description remain part of the existing row renderer; they are not wrapped in another new component. Alias key hints and keyboard keycaps remain separate accessories.

## 11. `form`: a full-width input screen

A form is a **single root node**, not a half-width detail-pane column. It replaces the result area while keeping the launcher header/footer. The main query is read-only while the form is active.

```python
View(nodes=(Node('form', 'create-entry', {
    'title': 'Create entry',
    'description': 'Existing entries are never overwritten.',
    'submit_text': 'Save entry',
    'error': '',
}, children=(
    Node('label', 'name-label', {'text': 'Entry name'}),
    Node('text_input', 'name', {'placeholder': 'Name'}),
    Node('label', 'secret-label', {'text': 'Password'}),
    Node('text_input', 'secret', {'password': True}),
), events={'submit': {}}),))
```

Properties: `title` (256 characters), `description` (2,048), `error` (512), and `submit_text` (128, default `Save`). Children are normal label/input/select/layout nodes. A form cannot be nested or combined with sibling root nodes or result rows.

The form supplies its own shared **Save / Cancel** controls; do not add another duplicate button row. Declaring `submit` enables Save. Enter in a text field or the native footer's primary control also submits. Your handler receives `Event('create-entry', 'submit', ...)` with current inputs.

Cancel and Escape use host navigation. User edits mark the form dirty. Leaving a dirty form shows an **inline discard confirmation**, not a separate popup: Keep editing retains current values; Discard clears password fields and leaves. Escape while that confirmation is open dismisses it rather than discarding silently.

While a submit call is running, fields and Save are disabled; Cancel remains available. A failed submit should return the same form ID with an error, current nonsecret values, and empty password fields. A successful submit should return the refreshed browsing view. Scrolling remains available for longer forms without visible scrollbars.

For menu-driven creation, use `Action('activate', 'create', push=True)` so the previous browsing query/selection can be restored. `Action('back', '')` is available for a custom Cancel menu item. See [navigation in plugins](plugins.md#navigation-and-escape).

## 12. `shortcut_hint`: reusable shortcut keycaps

```python
Node('shortcut_hint', 'sync-keys', {'keys': 'Ctrl+S'}, accessibility_label='Control S')
```

`keys` is a display string up to 128 characters, default `''`. The component renders the shared Mac-style symbols (↩ Enter, ⌥ Alt, ⌃ Ctrl, ⇧ Shift) as compact keycaps. It has no events and **does not register a keyboard binding**.

Footer hints and Actions-menu keycaps use this same component, including its shared painter. Application-side code can use `ShortcutHint(keys, family, colors)` directly; `key_hint(...)` remains a compatibility factory. Declare an actual plugin binding with `MenuAction.shortcut`, not by drawing a hint.

## 13. Toasts: closeable status notifications

To notify without replacing the current screen, return a Qt-free `Toast` from your event handler:

```python
from plugin_api import Toast


def handle_event(event, host):
    return Toast('Sync unavailable', 'Configure a Git remote, then retry.', 'error')
```

To update content and notify at the same time, return `View(..., toast=Toast('Saved', tone='success'))`. Search still returns `View`, not a standalone Toast.

| Field | Meaning |
| --- | --- |
| `title` | Required, nonempty plain text, up to 128 characters |
| `message` | Optional plain text, up to 512 characters |
| `tone` | `info` (default), `success`, or `error` |

`ToastBanner` is the reusable application-side component. It uses configured colors and the search/UI font, with equal 28-pixel status/dismiss slots vertically centered around a left-aligned, wrapping text block. A compact single-line confirmation grows only when text needs more height. The **×** close button is keyboard-accessible. It floats above the footer inside the existing popup, does not steal focus, and does not create a compositor popup. Errors remain until dismissed; info/success disappear after four seconds. A new notification replaces the previous one. Changing mode clears it.

A standalone Toast preserves the current query, rows, selection and form; a failed submitted form clears masked fields. It creates no navigation step. Notifications are not stored in history or logs. **Never send passwords or private keys in a toast.**

## 14. Actions menu: the plugin-facing frontend API

Plugins do not construct Qt menus. Publish menu items alongside the view:

```python
from plugin_api import Action, MenuAction, Toast, View


def search(query, host):
    return View(actions=(MenuAction('refresh', 'Refresh', Action('activate', 'refresh'),
                                   glyph='↻', shortcut='Ctrl+R'),))


def handle_event(event, host):
    if event.node_id == 'refresh' and event.name == 'activate':
        return Toast('Refreshed', tone='success')
    return View()
```

The host shows these entries in **Actions / Ctrl+K**, even with no result rows. Clicking or pressing a declared shortcut follows the same action/event route. Bindings exist only for the active plugin's current view; drawing `shortcut_hint` alone never binds a key. Omit `shortcut` if you do not want a direct binding. Full limits, reserved keys and callback payloads: [Plugin integration](plugins.md#plugin-actions-menu).

Application-side `ActionPanel.add_action(title, callback, glyph='', keys='', bind_shortcut=False)` uses the same keycap painter. `bind_shortcut=True` installs a **menu-local** binding, valid only while focus is inside the visible panel. Built-in Copy/Favorites actions use it; plugin runtime bindings and host Help/Settings bindings are installed by the controller, not duplicated in the panel.

## Main result rows and catalog settings

Use `Result` for the shared search surface:

```python
from plugin_api import Action, Catalog, Result, View

View(
    results=(Result(
        'account:work',
        'Work account',
        subtitle='Copy account name',
        glyph='\uf084',
        keywords='office login',
        tag='example.com',
        action=Action('copy', 'work'),
    ),),
    catalog=Catalog(
        placeholder='Search accounts…',
        footer='Select an account',
        heading='Accounts',
        empty='No accounts found',
        layout='list',
    ),
)
```

`tag` supplies the borderless right-hand `type_label`, useful for domains or other short context; it is limited to 256 characters and elides visually when needed. Root built-in modes use the same **Plugin** label as installed plugins. Keycaps display Mac-style symbols (↩ Enter, ⌥ Alt, ⌃ Ctrl, ⇧ Shift), without changing Linux key bindings.

A catalog describes the native surface, not a new window:

| Field | Meaning |
| --- | --- |
| `placeholder` | Main search field prompt |
| `footer` | Context shown in the bottom bar |
| `heading` | Result-section label |
| `empty` | Message when there are no results |
| `layout` | `list`, `grid` or `list_detail` using host-owned layouts |
| `filters` | Tuple of filter labels; changes emit `Event('catalog', 'filter', ...)` with the selected value |
| `preserve_order` | Keep supplied order for an empty query rather than applying normal ranking |

## Where the frontend code lives

| File | Responsibility |
| --- | --- |
| `main/frontend/renderer.py` | Convert validated nodes into widgets and collect form values |
| `main/frontend/markdown.py` | `MarkdownView`: documentation rendering and safe links/resources |
| `main/frontend/image_preview.py` | `ImagePreview`: bounded image loading and scaling |
| `main/frontend/widgets.py` | Shared controls, Actions panel, type filter, icon/font helpers |
| `main/frontend/type_label.py` | `TypeLabel`: borderless widget and shared native-row type/context painting |
| `main/frontend/controls.py` | `TextInput` and `Button`: shared input/button appearance |
| `main/frontend/form.py` | `Form`: full-width fields, submit/cancel and inline discard confirmation |
| `main/frontend/shortcut_hint.py` | `ShortcutHint`: shared widget/keycap painter, symbols and typography |
| `main/frontend/toast.py` | `ToastBanner`: nonmodal status, close button, timeout and placement |
| `main/launcher.py` | Window composition, navigation and wiring data to components |

`main.plugin_ui` and `main.widgets` remain compatibility imports. Application-side Qt code can reuse `Form(props, config)`, `TextInput(text, placeholder, password, config)` and `Button(text, config, variant='secondary')`, or `MarkdownView(colors)` with `set_markdown(text)`, `ImagePreview(colors)` with `set_source(path, alt)` / `set_data(image_bytes)`, or `TypeLabel(text, config)` for shared type markers. Pass the existing `config['colors']` mapping. Plugins should use the declarative nodes instead of importing these internal Qt classes.

## How existing plugins use the frontend

| Plugin | Presentation |
| --- | --- |
| Clipboard | Catalog with `list_detail` and type filters. Launcher composes history/text/metadata; images use `ImagePreview`. |
| Emoji | Grid catalog and copyable results; no custom nodes. |
| Web | List catalog and search results; no custom nodes. |
| Gopass example | Vault/password results and copy choices; full-width `form` with labels/masked inputs for creation; Sync in Actions. Escape restores safe browsing states, not cached secret views. |

Clipboard's text viewer, metadata rows and split-pane composition still live in the launcher. Extracting reusable Markdown/image rendering does **not** mean every native surface is already a declarative node.
