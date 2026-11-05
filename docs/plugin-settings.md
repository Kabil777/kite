# Contract-driven plugin Settings

KITE builds native Settings pages from `plugin.json`. The same contract drives a tool's optional quick controls and Options panel. Python and executable backends use identical metadata; rendering Settings never imports or starts the backend.

**Compatibility:** these typed controls and preview extensions require current KITE main; the original v0.1.0 release does not include them. Existing API-v2 text/choice plugins and executable wire-v1 backends remain supported. No release assets are rewritten.

## Declare fields

```json
{
  "id": "photo-tool",
  "name": "Photos",
  "api_version": 2,
  "entry": "__init__.py",
  "configuration": [
    {"key":"directories","label":"Folders","type":"directory_list","default":[],"max_items":16},
    {"key":"enabled","label":"Enable feature","type":"boolean","default":false},
    {"key":"duration","label":"Duration (seconds)","type":"number","optional":true,"default":null,"min":0,"max":120,"step":0.1},
    {"key":"fit","label":"Fit","type":"choice","options":["crop","fit"],"default":"crop"}
  ]
}
```

Open **Plugin marketplace → Installed → Options → Settings**, or **Plugin Settings** in a tool's Actions. Folder/file lists have Add, Remove, Clear and Browse controls; item text is editable. No JSON entry is required.

| Type | Stored value and editor |
| --- | --- |
| `text` | String; text input |
| `choice` | String from `options`; native combo |
| `boolean` | Boolean; checkbox |
| `number` | Finite number; spin box (`integer: true` for whole numbers) |
| `directory`, `file` | String; path input with native Browse |
| `directory_list`, `file_list` | Array of paths; editable list with Browse |
| `string_list` | Array of strings; editable list |
| `color` | RGB/RGBA hex string, with optional `#`; native color picker |

Paths use absolute names or `~/`. Existence is not automatically checked on the GUI thread: a removable folder may be offline. Backends should report unavailable resources clearly.

## Metadata and validation

- Required: `key` (unique lowercase identifier, up to 64 characters), `label` (up to 128), `type`.
- `default`: correctly typed value. If absent, defaults are empty text/path/list, false, zero, the first choice, or opaque black for a color.
- `optional: true`: permits `null`, displayed as **Backend default**. It is distinct from `false`, zero or an empty string. Defaults to null if no default is supplied. `required` and `optional` cannot both be true.
- `required: true`: rejects empty/null values on Save. A required field without an explicit default may be presented empty for the user to complete.
- `help`, `placeholder`, `group`: bounded plain text (up to 1,024 characters). `advanced: true` places a field in the collapsible advanced section.
- Numbers: `min`, `max`, positive `step`, `integer`, and `decimals` (0–6). Bounds must be finite and within ±1 billion; omitted range is ±1 billion.
- Text/path values: `max_length` (up to 4,096). Default is 1,024 for scalar text and 4,096 per list item.
- Lists: `max_items` (default 16, maximum 1,000); duplicate values normalize to one entry. Empty items and NUL characters are rejected.
- Choices: 1–64 unique string options, at most 256 characters each.

At most 32 fields and 16 KiB of per-plugin configuration are supported. Unknown metadata/field kinds, malformed defaults, nonfinite numbers and invalid value types are rejected. There are no executable validators, arbitrary regex/expression programs or plugin-created Qt widgets. Use ordinary numeric fields for compound backend values, then validate the combination in the backend before acting.

Legacy `text` and `choice` declarations retain their behavior. A valid JSON-array string can migrate to a new list type in memory; only explicit Save writes the array. Malformed legacy data is shown as repairable input, not silently discarded. Undeclared stored keys and unrelated plugin preferences are preserved.

## Read values in a backend

```python
from plugin_api import plugin_config, validate_settings

values = plugin_config(host, 'photo-tool')
# fields is the reviewed configuration list from this package's manifest.
values = validate_settings(fields, values)
```

`plugin_config` merges packaged defaults with user values; it does not replace backend semantic validation. `validate_settings(fields, values, partial=False)` returns declared, normalized values and enforces the payload bound. `partial=True` validates only provided declared keys. Backends must still validate cross-field rules and resource identities before external operations.

The worker takes its configuration snapshot at initialization. A successful host Settings save retires only the affected session, then a new session receives the updated snapshot; copying a worker's config per call is not a live reload. Do not modify `host.config` to persist values.

## Quick controls and explicit defaults

A tool can reference its declared fields:

```python
Catalog(
    layout='list_detail',
    settings_keys=('fit', 'duration'),  # compact controls
    option_keys=('fit', 'duration'),    # complete Options panel
    primary_label='Apply',
)
```

Both key lists must reference declared fields; quick keys must be a subset of option keys (option keys default to quick keys). Changes are host-owned session overrides, not YAML writes. **Reset** restores declared defaults in that session. **Save defaults** explicitly persists the current option snapshot using the same Settings save path. Folder setup can remain exclusively in Plugin Settings by omitting it from these lists.

The host injects a validated current snapshot as `event.payload['settings']` when dispatching a tool event, alongside `query` and ordinary form `inputs`. These host snapshots override static event fields. Executable backends receive normal JSON values, including booleans, numbers, arrays and nulls. Validate before building argv; never turn preferences into a shell command.

## Save guarantees

The host validates before queuing a write, merges declared changes into the latest YAML, preserves unrelated values/permission flags and file mode, refuses a symlink or differently owned target, detects concurrent edits and replaces the file atomically. A failed write leaves both disk and the editable draft intact. Busy state is updated without rebuilding the form. A completed form is retired, so hidden dirty state cannot block Back/Close.

**Saved; reload needed** means persistence succeeded but subsequent refresh failed. An already committed save is not silently rolled back. The host does not execute a plugin merely to generate its settings page.
