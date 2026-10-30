# KITE user guide

This guide is for using KITE after installation. If you have not installed it yet, start with [download and setup](../README.md#setup-with-uv).

You do not need to configure a plugin or enable Rust search to open your first app. Run `~/.local/bin/kite`, type an app name, and press Enter.

## Open an app

1. Open KITE with your launcher shortcut, or run `kite` in a terminal.
2. Type an app's name.
3. Use ↑ / ↓ to choose a result, then press Enter.

Escape goes back one screen. At the main screen, Escape closes KITE.

## Find a tool

Search for **Clipboard**, **Emoji**, **Files**, **Windows**, **Calculator** or **Web Search**, then press Enter. Search inside the tool normally.

Two quick entries from the main screen:

- Type `;` to open Clipboard.
- Type `"` to open Emoji.

In Clipboard, select an item to preview it; press Enter to restore it. In Emoji, find an emoji and press Enter to copy it. Opening these tools does not paste into another application for you.

Web Search sends its suggestion queries to Google. Normal application search stays local by default; root suggestions can be enabled separately in settings.

## Calculate while typing

Type a calculation in the main search or open **Calculator**. Results update while you type; Enter copies the answer and keeps KITE open.

- `sin 30` → 1/2; `cos 60 + sin 30` → 1. Parentheses are optional for single-argument functions.
- `sqrt 81` → 9; `2(3+4)` → 14; `2pi` means 2 × pi.
- Angles use **degrees by default**, even with parentheses. For radians, add `r`: `sin 10r`, `sin (pi/2)r` or `sin(pi/2r)`.
- Inverse trig follows the same choice: `asin 0.5` → 30 degrees; `asin 0.5r` → pi/6 radians.
- Group longer arguments: `sin 10 * 2` doubles the sine of 10 degrees; `sin (10*2)` takes sine of 20 degrees.
- Multiple arguments still use parentheses: `round(pi,2)` or `solve(x^2-4,x)`.

Unfinished input such as `sin (` stays quiet. A real error appears in a notification after you pause typing. Editing dismisses that calculator error and cancels any pending one, without clearing unrelated notifications. Use `=` or Calculator mode if the main search does not recognize an expression.

## Switch to an open window

Open **Switch windows**, type part of a window title or app name, then choose it. KITE uses the interface available in your desktop session; it does not install helpers or change your desktop settings.

Hyprland, Sway and niri use their own commands. X11 desktops need `wmctrl`. KDE Plasma 6 Wayland needs recent `kdotool`; GNOME Wayland needs a compatible, enabled Window Commander extension. Other compatible Wayland compositors can use the limited `wlrctl` fallback, which cannot distinguish windows with identical app/title pairs.

If the interface/helper is missing, read the setup error rather than assuming no windows are open. Not every Wayland compositor allows external window switching. See [desktop requirements](desktop.md) for exact requirements and verification limits; new integrations have not been checked in live desktops during this change.

## See what you can do with a result

Select a result, then press **Ctrl+K** or click **Actions**.

Type part of an action's name to filter the menu. Use ↑ / ↓ and Enter to run it. Escape closes the menu without running anything.

The small symbols beside an action show its shortcut:

| Symbol | Key on your keyboard |
| --- | --- |
| ↩ | Enter |
| ⌃ | Ctrl |
| ⌥ | Alt |
| ⇧ | Shift |
| ⎋ | Escape |

These are display symbols, not changes to your Linux keyboard bindings.

## Keep useful apps close

Select an app or tool, then choose **Actions → Add to Favorites**. Favorites appear near the top when the main search is empty. The same menu lets you remove or move a favorite.

Copy Name / Result ID / Desktop ID and favorite-management shortcuts work while the Actions menu is open. Their keycaps show the exact binding. They do not take over normal typing in the main search.

## Install and manage plugins

1. Search **Plugin marketplace** in KITE. Installed shows packages without starting them.
2. Choose **Configure** in the search-bar dropdown. Paste `https://raw.githubusercontent.com/Kabil777/kite_plugins/main/index.json`, then choose **Save source**. This is the [official registry](https://github.com/Kabil777/kite_plugins); you only need to set the address once. Its catalog is currently empty until reviewed release packages are available, so an empty Browse screen is normal.
3. Choose **Browse** in that dropdown to fetch it. Select a plugin, then Enter or **Actions → Review install / Review update**.
4. Read the manual, requirements, publisher and source in the shared approval form. Choose **Approve install / Approve update** only if you trust that pinned artifact with your user's files, secrets and network access. A checksum is not a safety guarantee.
5. Open the plugin from KITE when ready. Install/enable never starts its Python code. Install any documented external commands/libraries yourself.

Installed rows use compact labels: **Installed · Marketplace** or **Installed · Local**. Browse rows say **Installed** or **Not installed**. Local means it was added outside the marketplace; it can still be used.

In **Installed**, select a package and press Enter for **Options**, or open **Actions (Ctrl+K)**. Choose **Allow in KITE / Disallow in KITE**, configure declared settings, read its manual, refresh, or uninstall a marketplace-managed package. Allow/Disallow changes KITE's separate manager preference; it does not edit `config.yaml`. An explicit `enabled: false` in YAML still prevents activation. Disable stops new calls and waits for cleanup; if that fails, the disabled setting remains, and cleanup still needs attention. **Uninstall** applies only to manager-owned packages and preserves settings, authored source and external stores. Edited/extra package files are retained in private `plugin-backups/`.

You can also use `kite plugins search`, `list`, `info`, `install`, `enable`, `disable`, `update` and `uninstall` in a terminal. **Close KITE for CLI changes**; read-only browsing/listing is allowed while it is open. Install/update/uninstall ask for approval; noninteractive use needs `--yes` without bypassing safety checks. No automatic updates, dependency installation or hooks run.

If an operation was interrupted, choose **Check cleanup / recover**, or close KITE, wait for worker cleanup and retry a CLI mutation. Recovery reports the actual journal outcome; Cancel does not undo a completed commit. Do not delete state or unlink unknown targets to bypass an ownership/cleanup error. See [package recovery and publishing](../catalog/README.md).

## Use Passwords

Passwords is optional and is **not installed by default**. Follow the [Gopass README in the registry](https://github.com/Kabil777/kite_plugins/blob/main/plugins/gopass/README.md) to add it first.

Then search for **Passwords** (the Gopass gopher mascot) and press Enter. This opens the Gopass plugin if it is installed and enabled. If it is missing from the main list, check **Plugin marketplace → Installed → Passwords → Options → Allow in KITE**; an explicit YAML disable still wins. Inside Passwords, press **Ctrl+?** for its step-by-step guide.

Password browsing shows names only. Unlocking, copying, creating and syncing are separate, explicit actions. Do not enter a password into KITE's main search.

## Fill in a form

Click a field or use Tab to move between fields. A password field stays masked. Use **Save**, or Enter inside a field, to submit.

**Cancel** or Escape returns to the previous screen. If you changed anything, choose **Discard changes** or **Keep editing**. Escape on that question keeps your work.

## Read an error or confirmation

Copying keeps KITE open and shows “Copied to clipboard”; the query and selection stay in place. This applies to passwords, calculator answers, emoji and clipboard-history items. Copy failures show an error notification instead.

Notifications appear above the bottom bar without replacing the screen. Click **×** to dismiss one. Errors stay until dismissed; confirmations disappear after a few seconds. Calculator errors also clear when you edit the expression.

If password sync says no Git remote is configured, open the Passwords guide for setup steps. KITE does not create a remote or sync automatically.

## Change settings

Choose **Actions → Edit YAML Settings**, or press **Ctrl+,**. Settings live in `~/.config/kite/config.yaml`. Close and reopen KITE to apply changes. Keep a backup before changing theme or provider settings.

## Get help without losing your place

**Ctrl+?** or **Actions → How to** opens this guide at the main screen, and the relevant guide inside a plugin. On a US keyboard, Ctrl+? means Ctrl+Shift+/.

Escape or Ctrl+? closes the guide and returns to your screen. Plugin action shortcuts stay inactive while reading help.

## If something does not work

- **A shortcut does nothing:** make sure KITE is focused and you are inside the tool that owns the action. Plugin shortcuts are not desktop-global.
- **A new plugin or setting is missing:** manager actions refresh discovery explicitly; for manual source/YAML changes, close KITE completely and reopen it. User-authored plugins belong in `~/.config/kite/plugin/`; installed packages belong in `~/.local/share/kite/plugins/`. Do not keep different packages with the same plugin ID in both places.
- **Package changes say cleanup is pending:** leave disabled flags in place and use Check cleanup / recover after resolving the reported worker/systemd failure. Authored/development packages are never adopted or uninstalled automatically.
- **External tools are unavailable:** check that your systemd user session is running; these packages require supervised workers. The optional Rust ranker uses its separate bounded direct-child setup, explained in its [guide](../examples/rust-ranking/HOWTO.md). Native tools still work. Older API-v1 packages need a v2 update from their maintainer; after fixing setup, restart KITE.
- **An installed app has not appeared yet:** KITE shows its cached list first, then refreshes it in the background. Installs, removals and edits normally update automatically. If the application monitor reports a problem, close and reopen KITE to run a fresh scan. Do not delete plugin-manager state to refresh applications.
- **Clipboard cannot copy:** install `wl-clipboard` on Wayland or `xclip` on X11 (`xsel` handles text only). For another tool, set `clipboard.copy_command` to an argv list in YAML and restart; see [copy settings](clipboard.md). No shell command strings are accepted. A failed copy may already have changed the clipboard; check before retrying.
- **Clipboard history is unavailable:** history still requires `cliphist` and its compatible watcher; changing the copy command does not replace that history backend.
- **Window switching is unavailable:** check the helper for your desktop in [desktop requirements](desktop.md). Wayland will not silently show only XWayland windows. Reopen the window list if a selected window has closed; identical app/title pairs cannot be focused safely through the generic wlroots fallback.
- **A trigonometric answer changed:** degrees are now the default. Add `r` for radians, for example `sin (pi/2)r`.
- **A plugin asks you to unlock a key:** finish its GPG/age or SSH prompt. Escape can cancel the current screen; an operation already sent may have completed.
- **An error remains visible:** read its message, fix the reported setup problem, then retry. Dismissing an error does not retry the operation.
