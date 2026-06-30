#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_BIN="${CONDA_BIN:-$(command -v conda)}"
LABEL="com.hyperion.hummingbot.hl-sol-context"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
TEMPLATE_PATH="$REPO_DIR/scripts/services/$LABEL.plist"

mkdir -p "$LAUNCH_AGENTS_DIR" "$REPO_DIR/logs" "$REPO_DIR/data/context"

sed \
  -e "s#__REPO_DIR__#$REPO_DIR#g" \
  -e "s#__CONDA_BIN__#$CONDA_BIN#g" \
  "$TEMPLATE_PATH" > "$PLIST_PATH"

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "Installed and started $LABEL"
echo "Plist: $PLIST_PATH"
echo "CSV:   $REPO_DIR/data/context/hyperliquid_SOL_context.csv"
echo "Logs:  $REPO_DIR/logs/hyperliquid_sol_context.out.log"
echo "       $REPO_DIR/logs/hyperliquid_sol_context.err.log"
