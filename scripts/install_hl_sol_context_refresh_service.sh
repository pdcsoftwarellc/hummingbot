#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_BIN="${CONDA_BIN:-$(command -v conda)}"
AWS_BIN="${AWS_BIN:-$(command -v aws)}"
LABEL="com.hyperion.hummingbot.hl-sol-context-refresh"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$LABEL.plist"
TEMPLATE_PATH="$REPO_DIR/scripts/services/$LABEL.plist"

mkdir -p "$LAUNCH_AGENTS_DIR" "$REPO_DIR/logs" "$REPO_DIR/data/context" "$REPO_DIR/data/s3/hyperliquid/asset_ctxs"

sed \
  -e "s#__REPO_DIR__#$REPO_DIR#g" \
  -e "s#__CONDA_BIN__#$CONDA_BIN#g" \
  -e "s#__AWS_BIN__#$AWS_BIN#g" \
  "$TEMPLATE_PATH" > "$PLIST_PATH"

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"

echo "Installed $LABEL"
echo "Schedule: day 3 monthly at 06:15 local time"
echo "Plist:    $PLIST_PATH"
echo "Merged:   $REPO_DIR/data/context/hyperliquid_SOL_merged_context.csv"
echo "Logs:     $REPO_DIR/logs/hyperliquid_sol_context_refresh.out.log"
echo "          $REPO_DIR/logs/hyperliquid_sol_context_refresh.err.log"
