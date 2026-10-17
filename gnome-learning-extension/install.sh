#!/usr/bin/env bash
set -euo pipefail
# Copy only. A prior installation is moved to a preserved backup, never deleted.
# Installation does not enable an extension or restart GNOME.
extension_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
extension_uuid="screenshot-translator-learning@amariichi"
extension_data="${XDG_DATA_HOME:-$HOME/.local/share}"
extension_target="$extension_data/gnome-shell/extensions/$extension_uuid"
for required_command in gnome-shell glib-compile-schemas python3; do
  command -v "$required_command" >/dev/null || { echo "Missing command: $required_command" >&2; exit 1; }
done
python3 - "$extension_source/metadata.json" "$(gnome-shell --version)" <<'PY'
import json, re, sys
metadata = json.load(open(sys.argv[1]))
version = re.search(r'(\d+)\.', sys.argv[2])
if not version or version[1] not in metadata['shell-version']:
    sys.exit('Unsupported GNOME Shell version: ' + sys.argv[2])
PY
# Validate sources before moving an existing target.
glib-compile-schemas --strict --dry-run "$extension_source/schemas"
mkdir -p -- "$(dirname -- "$extension_target")"
if [[ -e "$extension_target" || -L "$extension_target" ]]; then
  backup_root="$extension_data/screenshot-translator/learning-extension-backups"
  mkdir -p -- "$backup_root"
  extension_backup="$backup_root/$extension_uuid-$(date -u +%Y%m%dT%H%M%S)-$$"
  mv -- "$extension_target" "$extension_backup"
  echo "Previous installation preserved: $extension_backup"
fi
mkdir -p -- "$extension_target/schemas"
cp -- "$extension_source/extension.js" "$extension_source/metadata.json" "$extension_source/stylesheet.css" "$extension_target/"
cp -- "$extension_source/../gnome-extension/legacy-capture.js" "$extension_target/legacy-capture.js"
cp -- "$extension_source/schemas/"*.gschema.xml "$extension_target/schemas/"
cp -- "$extension_source/../gnome-extension/schemas/"*.gschema.xml "$extension_target/schemas/"
glib-compile-schemas --strict "$extension_target/schemas"
# Configure an explicit menu action only; never start the panel or capture here.
panel_argv="$(python3 -c 'import json,os,sys; print(json.dumps([os.path.realpath(sys.argv[1])]))' "$extension_source/../start-learning.sh")"
gsettings --schemadir "$extension_target/schemas" set org.gnome.shell.extensions.screenshot-translator-learning panel-command "$panel_argv"
echo "Installed: $extension_target"
echo "Enable explicitly: gnome-extensions enable $extension_uuid"
echo "If GNOME already imported this UUID, use a new isolated test session for verification."
echo "No logout, restart, or automatic enable was performed."
echo "Unified version: keep only $extension_uuid enabled; the legacy UUID/settings/files are preserved."
