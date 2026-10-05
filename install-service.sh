#!/bin/bash
# Install/refresh the DasTwitchy systemd service. Safe to re-run.
set -e
SRC="/home/hatch/workspace/dastwitchy/dastwitchy.service"
DST="/etc/systemd/system/dastwitchy.service"
sudo cp "$SRC" "$DST"
sudo systemctl daemon-reload
if ! sudo systemctl is-active --quiet dastwitchy; then
  sudo systemctl enable --now dastwitchy
fi
echo "dastwitchy: $(sudo systemctl is-active dastwitchy)"
