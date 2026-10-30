# Copying and clipboard history

## Copy and paste

KITE copies to the normal **clipboard**, which applications paste with **Ctrl+V**. It does not use X11's PRIMARY selection (commonly pasted with the middle mouse button).

Install a copy tool for your session:

| Session | Tool | Selection used by KITE |
| --- | --- | --- |
| Wayland | `wl-clipboard` | `wl-copy`, without `--primary` |
| X11 | `xclip` | Explicit `-selection clipboard` |
| X11, text-only fallback | `xsel` | Explicit `--clipboard` |

In `~/.config/kite/config.yaml`, the default is:

```yaml
clipboard:
  copy_command: auto
```

Automatic detection uses the display/session environment. A Wayland session does not silently fall back to XWayland. Missing tools produce an error rather than a success message.

Copying does not paste into another app for you. Select the copy action, wait for confirmation, focus the destination, then press Ctrl+V. KITE stays open after a copy.

## Custom copy commands

If you need another backend, use an argument list—not a shell command string:

```yaml
clipboard:
  copy_command: ["xclip", "-selection", "clipboard", "-in", "-target", "{mime}"]
```

Data is passed through stdin, not command-line arguments. `{mime}` is the only substitution. Automatic backends preserve text/image bytes; custom commands without `{mime}` support text only. Restart KITE after changing the setting.

Commands have a three-second timeout. A failure may occur after the clipboard has changed, so check the destination before retrying. KITE does not automatically retry uncertain copies.

## Clipboard history is separate

**`cliphist` is not present on every distribution and is not installed by KITE.** Copying and collecting history are separate: history needs `cliphist` plus a watcher that records changes; ordinary copy actions do not need cliphist, only the copy tool listed above.

On Arch/Manjaro, Wayland users can install both tools with:

```sh
sudo pacman -S --needed wl-clipboard cliphist
```

In a regular terminal, these commands start temporary text/image watchers for your session:

```sh
wl-paste --type text --watch cliphist store &
wl-paste --type image --watch cliphist store &
```

Do not start duplicates if your desktop already runs these watchers. For automatic startup, put the same commands in your compositor/session's startup configuration; terminal jobs alone are not a permanent setup. For other distributions, check their repositories or [cliphist's installation guide](https://github.com/sentriz/cliphist). On X11, `xclip`/`xsel` provide copying, not an automatic cliphist collector; history needs a separately configured compatible watcher.

KITE does not install or start watchers, migrate your existing history database, or create another persistent clipboard database.

## Copy the same history item again

A watcher may re-store a restored item under a new cliphist ID. KITE keeps the exact decoded bytes in a bounded memory cache while the current view is open, so repeated copies and previews do not depend on the obsolete ID. The copy command still runs on every explicit copy.

The cache is cleared on a mode/list change or window close, never written to disk, and limited to 500 snapshots with a 64-MiB payload/preview budget. Each restorable item is limited to 32 MiB; oversized items are rejected, not truncated. If an uncached entry was removed or has already expired from the cache, reopen Clipboard to refresh its IDs. Do not delete your history database to fix a stale row.

Clipboard managers may retain sensitive content you copy. KITE does not automatically expire or clear clipboard contents; configure your history manager according to your privacy needs. Do not paste real passwords into a terminal, issue report or diagnostic command to test copying.

## Verification

`tests.check_clipboard` tests backend selection and data forwarding with mocks and temporary capture programs, never the live clipboard. Optional plugin-copy integration checks also forward only a synthetic fixture value and verify the selected clipboard backend. `tests.check_clipboard_repeat` uses an isolated cliphist database and a fake copy program to reproduce ID replacement and verify repeated text/image restoration. It requires the cliphist executable but never opens the real database or clipboard. These checks do not inspect users' passwords or confirm compositor behavior in a live desktop session.
