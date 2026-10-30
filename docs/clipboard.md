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

History requires `cliphist` and a compatible watcher configured by you. A working copy command alone does not set up history. KITE does not install a new clipboard service or duplicate the cliphist database.

Clipboard managers may retain sensitive content you copy. KITE does not automatically expire or clear clipboard contents; configure your history manager according to your privacy needs. Do not paste real passwords into a terminal, issue report or diagnostic command to test copying.

## Verification

`tests.check_clipboard` tests backend selection and data forwarding with mocks and temporary capture programs, never the live clipboard. Optional plugin-copy integration checks also forward only a synthetic fixture value and verify the selected clipboard backend. These checks do not inspect users' passwords or confirm compositor behavior in a live desktop session.
