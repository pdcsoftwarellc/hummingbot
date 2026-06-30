#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_BIN="${CONDA_BIN:-$(command -v conda)}"
AWS_BIN="${AWS_BIN:-$(command -v aws)}"

COIN="${COIN:-SOL}"
START_DATE="${START_DATE:-2023-05-20}"
END_DATE="${END_DATE:-}"
S3_CACHE_DIR="${S3_CACHE_DIR:-data/s3/hyperliquid/asset_ctxs}"
S3_CONTEXT_CSV="${S3_CONTEXT_CSV:-data/context/hyperliquid_SOL_s3_context.csv}"
LIVE_CONTEXT_CSV="${LIVE_CONTEXT_CSV:-data/context/hyperliquid_SOL_context.csv}"
MERGED_CONTEXT_CSV="${MERGED_CONTEXT_CSV:-data/context/hyperliquid_SOL_merged_context.csv}"

cd "$REPO_DIR"
mkdir -p data/context "$S3_CACHE_DIR" logs

if [[ -z "$END_DATE" ]]; then
  latest_key="$("$AWS_BIN" s3 ls s3://hyperliquid-archive/asset_ctxs/ --request-payer requester | awk '/[0-9]{8}\.csv\.lz4$/ { key=$4 } END { print key }')"
  if [[ -z "$latest_key" ]]; then
    echo "Could not find latest Hyperliquid asset_ctxs archive" >&2
    exit 1
  fi
  END_DATE="${latest_key:0:4}-${latest_key:4:2}-${latest_key:6:2}"
fi

echo "Refreshing Hyperliquid $COIN context: $START_DATE -> $END_DATE"
"$CONDA_BIN" run -n hummingbot python scripts/backfill_hyperliquid_s3_context.py \
  --coin "$COIN" \
  --start "$START_DATE" \
  --end "$END_DATE" \
  --cache-dir "$S3_CACHE_DIR" \
  --output "$S3_CONTEXT_CSV"

echo "Merging S3 context with live collector context"
"$CONDA_BIN" run -n hummingbot python scripts/merge_hyperliquid_context.py \
  --coin "$COIN" \
  --s3-csv "$S3_CONTEXT_CSV" \
  --live-csv "$LIVE_CONTEXT_CSV" \
  --output "$MERGED_CONTEXT_CSV"

echo "Merged context: $REPO_DIR/$MERGED_CONTEXT_CSV"
