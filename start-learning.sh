#!/usr/bin/env bash
set -euo pipefail
learning_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for learning_command in gjs glib-compile-schemas; do
  command -v "$learning_command" >/dev/null || { echo "Missing: $learning_command" >&2; exit 1; }
done
glib-compile-schemas --strict "$learning_root/gnome-learning-extension/schemas"
exec gjs -m "$learning_root/app/learning-panel.js" "$@"
