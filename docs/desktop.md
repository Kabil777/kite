# Switch between open windows

Open **Switch windows** in KITE, or run `kite windows`, then search for a title or app name and press Enter.

Install only the helper needed for your desktop:

| Desktop/session | Requirement |
| --- | --- |
| Hyprland | `hyprctl` and its working IPC interface |
| Sway | `swaymsg` and a working Sway IPC socket |
| niri | `niri msg` and working niri IPC |
| X11 desktops | `wmctrl` |
| KDE Plasma 6 Wayland | A recent [kdotool](https://github.com/jinliu/kdotool) supporting `kwinscript` |
| GNOME Wayland | A compatible, enabled [Window Commander](https://github.com/gnikolaos/window-commander) extension |
| Other supported wlroots sessions | Optional `wlrctl` with foreign-toplevel list/focus support |

KITE does not install helpers, enable extensions or change your desktop settings. An installed command alone does not prove its interface works in the current session.

Not every Wayland compositor permits external window control. KITE reports missing support rather than showing an incomplete XWayland-only list. The generic wlroots fallback cannot distinguish two windows with the same app/title pair; it rejects ambiguous targets. Compositor focus policies still apply.

If you see a setup error, check your desktop's helper before retrying. These interfaces have fixture coverage, not certification against every live desktop/version. Inspected Window Commander v11 declares GNOME 49/50; use an extension release compatible with your shell.

Back to the [user guide](howto.md).
