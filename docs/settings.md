# Change KITE settings

Settings live in `~/.config/kite/config.yaml` (or your `XDG_CONFIG_HOME`). Open them with **Actions → Edit YAML Settings** or **Ctrl+,**. Close and reopen KITE after editing.

You only need to write the values you want to change; other values use defaults. For example:

```yaml
window:
  width: 750
  height: 520
results:
  limit: 60
  font_size: 16
ranking:
  sensitivity: medium
```

The complete reference is [main/defaults.yaml](../main/defaults.yaml). Keep a backup before changing several settings. Unknown keys, invalid types and out-of-range values report an error rather than being silently accepted.

## Common changes

| You want to change… | Setting |
| --- | --- |
| Window dimensions | `window.width`, `window.height` |
| Result count | `results.limit` |
| Result text | `results.font_family`, `results.font_size` |
| Row layout | `results.layout`: `inline` or `stacked` |
| Application icons | `icons.theme`, `icons.size` |
| Colors | The `colors` section |
| Emoji grid | `emoji.columns`, `emoji.font_size` |
| Copy backend | [Clipboard settings](clipboard.md) |
| Search engine | `ranking.engine`; `builtin` is the default |

Dimensions and font sizes use logical pixels. Fonts and icon themes are optional preferences, not extra Python dependencies. An installed [Nerd Font](https://www.nerdfonts.com/font-downloads) improves symbol coverage; KITE uses native fallbacks when unavailable. It does not install fonts or themes automatically.

## Favorites and aliases

Use **Actions → Add to Favorites** for an app/tool; the same menu can remove or reorder it. Favorites are stored separately from appearance settings.

To add a short search alias, copy the result's ID from Actions and add it to YAML:

```yaml
aliases:
  - {id: cmd:clipboard, alias: cb}
```

Plugin-specific settings belong to the plugin's guide. API/component details are in the [plugin guide](plugins.md) and [frontend reference](frontend.md), not required for ordinary configuration.
