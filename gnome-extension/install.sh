#!/usr/bin/env bash
set -euo pipefail

# Copy installation also works on Wayland, where symlink updates can be cached.
extension_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
extension_uuid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["uuid"])' "$extension_source/metadata.json")"
extension_target="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$extension_uuid"

for required_command in gnome-shell glib-compile-schemas; do
    if ! command -v "$required_command" >/dev/null; then
        echo "Missing command: $required_command" >&2
        exit 1
    fi
done

shell_version="$(gnome-shell --version)"
if ! python3 - "$extension_source/metadata.json" "$shell_version" <<'PY'
import json, re, sys
metadata = json.load(open(sys.argv[1]))
match = re.search(r'(\d+)\.', sys.argv[2])
if not match or match.group(1) not in metadata['shell-version']:
    sys.exit(f'Unsupported {sys.argv[2]}; supported Shell versions: {", ".join(metadata["shell-version"])}')
PY
then
    exit 1
fi

mkdir -p -- "$(dirname -- "$extension_target")"
if [[ -L "$extension_target" ]]; then
    # Move the link itself, never delete or modify the repository it points to.
    # Keep backups outside extensions/ so GNOME cannot load a duplicate UUID.
    backup_root="${XDG_DATA_HOME:-$HOME/.local/share}/screenshot-translator/extension-backups"
    mkdir -p -- "$backup_root"
    link_backup="$backup_root/${extension_uuid}.symlink-backup-$(date +%s)-$$"
    mv -- "$extension_target" "$link_backup"
    echo "Previous symlink preserved: $link_backup"
fi

mkdir -p -- "$extension_target/schemas"
cp -- "$extension_source/extension.js" "$extension_source/metadata.json" \
    "$extension_source/stylesheet.css" "$extension_target/"
cp -- "$extension_source/schemas/"*.gschema.xml "$extension_target/schemas/"
glib-compile-schemas --strict "$extension_target/schemas"

echo "Installed $extension_uuid for $shell_version"
echo "Location: $extension_target"
echo "On Wayland, log out and log back in to load the updated extension."
echo "Then run: gnome-extensions enable $extension_uuid"
echo "Check with: gnome-extensions info $extension_uuid"
