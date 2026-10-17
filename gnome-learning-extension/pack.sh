#!/usr/bin/env bash
set -euo pipefail
extension_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
extension_output="${1:-$PWD}"
mkdir -p -- "$extension_output"
extension_output="$(cd -- "$extension_output" && pwd)"
extension_stage="$(mktemp -d "${TMPDIR:-/tmp}/screenshot-translator-pack.XXXXXX")"
mkdir -p -- "$extension_stage/schemas"
cp -- "$extension_source/extension.js" "$extension_source/metadata.json" "$extension_source/stylesheet.css" "$extension_stage/"
cp -- "$extension_source/../gnome-extension/legacy-capture.js" "$extension_stage/legacy-capture.js"
cp -- "$extension_source/schemas/"*.gschema.xml "$extension_source/../gnome-extension/schemas/"*.gschema.xml "$extension_stage/schemas/"
gnome-extensions pack --force --extra-source=legacy-capture.js --out-dir="$extension_output" "$extension_stage"
echo "Packaging sources preserved: $extension_stage"
