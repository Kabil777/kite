#!/usr/bin/env bash
# User-local installation from a checkout or an explicitly selected remote repository.
set -euo pipefail

fail() { printf 'KITE: %s\n' "$*" >&2; exit 1; }
repo=''
link_examples=()
while (($#)); do
    case "$1" in
        --repo) repo=${2:?--repo requires a Git URL}; shift 2 ;;
        --link-example) link_examples+=("${2:?--link-example requires an example ID}"); shift 2 ;;
        -h|--help)
            printf 'Usage: bash install.sh [--repo HTTPS_OR_SSH_GIT_URL] [--link-example ID]\n'
            exit 0 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done

[[ -z "$repo" || ${#link_examples[@]} == 0 ]] || fail '--link-example requires a persistent local checkout, not --repo'
for example in "${link_examples[@]}"; do
    [[ "$example" =~ ^[a-z][a-z0-9_-]{0,63}$ ]] || fail 'Invalid example ID'
done

command -v uv >/dev/null || fail 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
python=$(command -v python3) || fail 'System Python 3.14+ is required'
"$python" -c 'import sys; assert sys.version_info >= (3, 14)' \
    || fail 'System Python 3.14+ is required (including system PyGObject/Gio bindings)'
work=''
trap '[[ -z "$work" ]] || rm -rf -- "$work"' EXIT
if [[ -n "$repo" ]]; then
    case "$repo" in
        https://*|ssh://*|git@*) ;;
        *) fail '--repo must be an HTTPS or SSH Git URL' ;;
    esac
    command -v git >/dev/null || fail 'git is required for remote installation'
    work=$(mktemp -d)
    GIT_TERMINAL_PROMPT=0 git clone --depth 1 -- "$repo" "$work/source"
    source_dir="$work/source"
elif [[ -f "${BASH_SOURCE[0]:-}" ]]; then
    source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
else
    fail 'Piped installation requires --repo URL; no repository is assumed'
fi
[[ -f "$source_dir/main/launcher.py" && -f "$source_dir/uv.lock" \
    && -f "$source_dir/assets/icons/kite.png" ]] || fail 'Source is not a complete KITE checkout'

data=${XDG_DATA_HOME:-"$HOME/.local/share"}
config=${XDG_CONFIG_HOME:-"$HOME/.config"}/kite
app="$data/kite"
bin="$HOME/.local/bin"
mkdir -p "$app" "$config" "$bin" "$data/applications"
app=$(cd -- "$app" && pwd -P)
if [[ "$source_dir" != "$app" ]]; then
    # Runtime payloads/backups and environments must never enter checkout overlays.
    tar -C "$source_dir" --exclude='./.git' --exclude='./.venv' \
        --exclude='./plugins' --exclude='./plugin-packages' --exclude='./.plugin-staging' \
        --exclude='./plugin-backups' --exclude='./.plugin-backup.*' \
        --exclude='./.memsearch' --exclude='__pycache__' -cf - . \
        | tar -C "$app" -xf -
fi
mkdir -p "$app/plugins"
for example in "${link_examples[@]}"; do
    package="$source_dir/examples/$example"
    [[ -f "$package/plugin.json" ]] || fail "Not a plugin package: $example"
    (cd -- "$source_dir" && "$python" -B -c \
        'from pathlib import Path; import sys; from main.plugin_api import _read_manifest; _read_manifest(Path(sys.argv[1]))' \
        "$package") || fail "Invalid plugin package: $example"
    package=$(cd -- "$package" && pwd -P)
    destination="$app/plugins/$example"
    if [[ -L "$destination" && $(readlink -f -- "$destination") == "$package" ]]; then
        continue
    fi
    backup=''
    if [[ -e "$destination" || -L "$destination" ]]; then
        backup=$(mktemp -d "$app/.plugin-backup.XXXXXX")
        mv -- "$destination" "$backup/$example"
        printf 'Previous plugin retained: %s\n' "$backup/$example"
    fi
    if ! ln -s -- "$package" "$destination"; then
        [[ -z "$backup" ]] || mv -- "$backup/$example" "$destination"
        fail "Could not link example: $example"
    fi
    printf 'Development plugin linked: %s\n' "$destination"
done
if [[ ! -e "$config/config.yaml" ]]; then
    cp "$app/main/defaults.yaml" "$config/config.yaml"
fi
if [[ ! -x "$app/.venv/bin/python" ]]; then
    uv venv --python "$python" --system-site-packages "$app/.venv"
fi
uv sync --project "$app" --locked --python "$python"
"$app/.venv/bin/python" -c 'from gi.repository import Gio' \
    || fail 'Install system PyGObject/Gio and create .venv using uv venv --system-site-packages'

printf '#!/usr/bin/env bash\nunset QT_QPA_PLATFORM_PLUGIN_PATH QT_PLUGIN_PATH\nexport LC_ALL=C.UTF-8\nexec %q %q "$@"\n' \
    "$app/.venv/bin/python" "$app/main.py" > "$bin/kite"
chmod +x "$bin/kite"
cat > "$data/applications/kite.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=KITE
GenericName=Qt Launcher
Comment=A native Qt launcher
Exec="$bin/kite"
Icon=kite
Terminal=false
Categories=Utility;
Keywords=kite;launcher;calculator;emoji;clipboard;
StartupWMClass=kite
EOF
"$app/.venv/bin/python" - "$app/assets/icons/kite.png" "$data/icons/hicolor" <<'PY'
from pathlib import Path
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
image = QImage(sys.argv[1])
if image.isNull():
    raise SystemExit('KITE: icon could not be loaded')
for size in (16, 24, 32, 48, 64, 128, 256, 512):
    path = Path(sys.argv[2]) / f'{size}x{size}' / 'apps/kite.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation).save(str(path), 'PNG'):
        raise SystemExit(f'KITE: could not save {path}')
PY
if command -v update-desktop-database >/dev/null; then
    update-desktop-database "$data/applications" || true
fi
if command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -f -t "$data/icons/hicolor" || true
fi
printf 'KITE installed: %s\nConfiguration preserved: %s\nRun: %s\n' "$app" "$config/config.yaml" "$bin/kite"
