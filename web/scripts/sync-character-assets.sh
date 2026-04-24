#!/usr/bin/env bash
# Sync character avatar PNGs from assets/character/saessagi/ → web/public/avatars/
# Run automatically via package.json predev / prebuild hooks.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$WEB_DIR/../assets/character/saessagi"
DST_DIR="$WEB_DIR/public/avatars"

if [ ! -d "$SRC_DIR" ]; then
  echo "[sync-character-assets] source not found: $SRC_DIR — skipping"
  exit 0
fi

mkdir -p "$DST_DIR"
rsync -a --include="*.png" --exclude="*" "$SRC_DIR/" "$DST_DIR/"
echo "[sync-character-assets] synced $(ls "$DST_DIR"/*.png 2>/dev/null | wc -l | tr -d ' ') PNGs → $DST_DIR"
