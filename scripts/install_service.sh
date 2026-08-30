#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/subway-e-ink-tracker}"
UNIT_SRC="$REPO_DIR/systemd/subway-display.service"
UNIT_DST=/etc/systemd/system/subway-display.service

if [ ! -f "$UNIT_SRC" ]; then
  echo "missing $UNIT_SRC" >&2
  exit 1
fi

sudo cp "$UNIT_SRC" "$UNIT_DST"
sudo systemctl daemon-reload
sudo systemctl enable --now subway-display.service
sudo systemctl --no-pager --full status subway-display.service
