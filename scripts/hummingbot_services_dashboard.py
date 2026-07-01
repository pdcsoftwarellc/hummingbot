"""
Generate a local dashboard for Hummingbot-related data services.

The dashboard is intentionally static: run it whenever you want a fresh view of
active LaunchAgents, forward collectors, historical backfills, and research data
assets.

Usage:
    conda run -n hummingbot python scripts/hummingbot_services_dashboard.py
"""
import argparse
import csv
import html
import os
import plistlib
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional


DEFAULT_OUTPUT = "reports/hummingbot_services_dashboard.html"
SERVICE_PREFIX = "com.hyperion.hummingbot."
MAX_SMALL_CSV_MB = 300


@dataclass
class ServiceInfo:
    label: str
    mode: str
    status: str
    purpose: str
    necessity: str
    stop_when: str
    outputs: List[str] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)
    command_hint: str = ""


SERVICE_CATALOG: Dict[str, ServiceInfo] = {
    "com.hyperion.hummingbot.hl-sol-context": ServiceInfo(
        label="com.hyperion.hummingbot.hl-sol-context",
        mode="Forward collector",
        status="Expected running",
        purpose="Collects live Hyperliquid SOL asset context: funding, premium, OI, mark/oracle/mid, spread, and depth.",
        necessity="Keep if we want live context after the latest S3 archive and fewer future gaps.",
        stop_when="Stop only if we no longer care about live SOL context or want to rely entirely on monthly S3 refreshes.",
        outputs=["data/context/hyperliquid_SOL_context.csv"],
        logs=["logs/hyperliquid_sol_context.out.log", "logs/hyperliquid_sol_context.err.log"],
        command_hint="scripts/install_hl_sol_context_service.sh",
    ),
    "com.hyperion.hummingbot.hl-sol-context-refresh": ServiceInfo(
        label="com.hyperion.hummingbot.hl-sol-context-refresh",
        mode="Scheduled S3 refresh",
        status="Optional",
        purpose="Monthly catch-up helper for Hyperliquid S3 asset context plus merged context rebuild.",
        necessity="Optional if live context collector stays healthy; useful for filling missed/archive-published months.",
        stop_when="Stop if forward collection is enough and monthly archive refresh is intentionally manual.",
        outputs=["data/context/hyperliquid_SOL_s3_context.csv", "data/context/hyperliquid_SOL_merged_context.csv"],
        logs=["logs/hyperliquid_sol_context_refresh.out.log", "logs/hyperliquid_sol_context_refresh.err.log"],
        command_hint="scripts/install_hl_sol_context_refresh_service.sh",
    ),
    "com.hyperion.hummingbot.hl-sol-l2-backfill": ServiceInfo(
        label="com.hyperion.hummingbot.hl-sol-l2-backfill",
        mode="Historical backfill",
        status="Expected temporary",
        purpose="Backfills Hyperliquid SOL L2 execution features from requester-pays S3 in monthly chunks.",
        necessity="Keep only until SOL L2 history from 2023-04-15 through 2026-06-01 is complete.",
        stop_when="Stop after manifest covers the target range, or pause if S3 cost/runtime is not worth it.",
        outputs=["data/microstructure/hyperliquid_l2_monthly/SOL/manifest.csv"],
        logs=["logs/hyperliquid_sol_l2_backfill.out.log", "logs/hyperliquid_sol_l2_backfill.err.log"],
        command_hint="conda run -n hummingbot python scripts/backfill_hyperliquid_s3_l2_monthly.py --coin SOL --start 2023-04-15 --end 2026-06-01",
    ),
    "com.hyperion.hummingbot.hl-sol-l2-forward": ServiceInfo(
        label="com.hyperion.hummingbot.hl-sol-l2-forward",
        mode="Forward collector",
        status="Expected running",
        purpose="Collects live Hyperliquid SOL rich L2 execution features using the same schema as S3 L2 backfills.",
        necessity="Keep if we want current/future L2 liquidity, imbalance, and slippage features without waiting for monthly S3 archives.",
        stop_when="Stop only if rich L2 execution features are not needed or API polling cost/noise is not worth it.",
        outputs=["data/microstructure/hyperliquid_SOL_l2_execution_live_1m.csv"],
        logs=["logs/hyperliquid_sol_l2_forward.out.log", "logs/hyperliquid_sol_l2_forward.err.log"],
        command_hint="scripts/install_hl_sol_l2_forward_service.sh",
    ),
    "com.hyperion.hummingbot.hl-sol-trades": ServiceInfo(
        label="com.hyperion.hummingbot.hl-sol-trades",
        mode="Forward collector",
        status="Not installed yet",
        purpose="Would collect live public Hyperliquid SOL trades into true aggressive flow and CVD features.",
        necessity="Install if we want true CVD from now forward; S3 historical trade files have not been found in checked prefixes.",
        stop_when="Stop if trade-flow/CVD is not needed for strategy research.",
        outputs=["data/microstructure/hyperliquid_SOL_trades_1m.csv"],
        logs=["logs/hyperliquid_sol_trades.out.log", "logs/hyperliquid_sol_trades.err.log"],
        command_hint="conda run -n hummingbot python scripts/collect_hyperliquid_trades.py --coin SOL",
    ),
}


DATA_ASSETS = [
    ("Forward context", "data/context/hyperliquid_SOL_context.csv", "Live SOL context collector output."),
    ("S3 context", "data/context/hyperliquid_SOL_s3_context.csv", "Historical Hyperliquid asset context from S3."),
    ("Merged context", "data/context/hyperliquid_SOL_merged_context.csv", "Canonical context input for research; rerun merge to include latest live rows."),
    ("Trade flow", "data/microstructure/hyperliquid_SOL_trades_1m.csv", "True CVD/aggressive flow collector output; expected missing until service is installed."),
    ("Old L2 sample", "data/microstructure/hyperliquid_SOL_l2_1m_20260501_20260601.csv", "Earlier compact May-June L2 feature sample."),
    ("Rich L2 sample", "data/microstructure/hyperliquid_SOL_l2_execution_1m_20260501_20260601.csv", "Richer May-June L2 execution feature sample."),
    ("Live rich L2", "data/microstructure/hyperliquid_SOL_l2_execution_live_1m.csv", "Forward-collected L2 execution features with the same schema as S3 L2 backfills."),
    ("Monthly L2 manifest", "data/microstructure/hyperliquid_l2_monthly/SOL/manifest.csv", "Progress tracker for resumable historical SOL L2 backfill."),
    ("1m Binance candles", "data/candles/binance_perpetual_SOL-USDT_1m.csv", "Long-history price proxy and timing data."),
    ("5m Binance candles", "data/candles/binance_perpetual_SOL-USDT_5m.csv", "Long-history price proxy and timing data."),
    ("1h Binance candles", "data/candles/binance_perpetual_SOL-USDT_1h.csv", "Long-history regime backbone."),
    ("5m joined research", "data/research/sol_5m_joined_research.csv", "Current 5m joined research table."),
    ("1m joined research", "data/research/sol_1m_joined_research_2025_2026.csv", "Bounded 1m joined research table."),
    ("1m execution research", "data/research/sol_1m_execution_research_20260501_20260602.csv", "L2-rich May-June execution research table."),
]


def run_text(command: List[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        return f"ERROR: {exc}"
    return result.stdout if result.returncode == 0 else f"ERROR: {result.stderr.strip() or result.stdout.strip()}"


def human_size(size: Optional[int]) -> str:
    if size is None:
        return "-"
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def parse_launchctl_print(text: str) -> Dict[str, str]:
    if text.startswith("ERROR:"):
        return {"loaded": "no", "raw_error": text}
    fields = {"loaded": "yes"}
    for key in ["state", "path", "pid", "runs", "last exit code", "last terminating signal"]:
        match = re.search(rf"^\s*{re.escape(key)} = (.+)$", text, flags=re.MULTILINE)
        if match:
            fields[key] = match.group(1).strip()
    stdout_match = re.search(r"^\s*stdout path = (.+)$", text, flags=re.MULTILINE)
    stderr_match = re.search(r"^\s*stderr path = (.+)$", text, flags=re.MULTILINE)
    if stdout_match:
        fields["stdout"] = stdout_match.group(1).strip()
    if stderr_match:
        fields["stderr"] = stderr_match.group(1).strip()
    return fields


def launchctl_status(label: str) -> Dict[str, str]:
    uid = str(os.getuid())
    return parse_launchctl_print(run_text(["launchctl", "print", f"gui/{uid}/{label}"]))


def candidate_plist_paths() -> Iterable[Path]:
    roots = [
        Path.home() / "Library" / "LaunchAgents",
        Path("scripts/services"),
        Path("logs"),
    ]
    for root in roots:
        if not root.exists():
            continue
        yield from root.glob(f"{SERVICE_PREFIX}*.plist")


def read_plists() -> Dict[str, Dict]:
    plists = {}
    for path in candidate_plist_paths():
        try:
            with open(path, "rb") as file:
                payload = plistlib.load(file)
        except (OSError, plistlib.InvalidFileException):
            continue
        label = payload.get("Label")
        if label:
            payload["_path"] = str(path)
            plists[label] = payload
    return plists


def tail_lines(path: str, limit: int = 6) -> List[str]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", errors="replace") as file:
            lines = file.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-limit:]]


def last_nonempty_line(path: str) -> Optional[str]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, "rb") as file:
        file.seek(0, os.SEEK_END)
        position = file.tell()
        buffer = b""
        while position > 0:
            read_size = min(8192, position)
            position -= read_size
            file.seek(position)
            buffer = file.read(read_size) + buffer
            lines = [line for line in buffer.splitlines() if line.strip()]
            if len(lines) >= 2 or position == 0:
                return lines[-1].decode("utf-8", errors="replace") if lines else None
    return None


def first_data_line(path: str) -> Optional[str]:
    try:
        with open(path, "r", newline="", errors="replace") as file:
            reader = csv.reader(file)
            next(reader, None)
            row = next(reader, None)
    except OSError:
        return None
    if row is None:
        return None
    output = []
    with open(path, "r", newline="", errors="replace") as file:
        next(file, None)
        return next(file, "").rstrip("\n") or None


def parse_timestamp(value: str) -> str:
    value = str(value).strip()
    if not value:
        return "-"
    try:
        numeric = float(value)
        unit = "ms" if numeric > 10_000_000_000 else "s"
        parsed = datetime.fromtimestamp(numeric / 1000 if unit == "ms" else numeric, tz=timezone.utc)
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value[:32]


def csv_range(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {"exists": "no"}
    size = os.path.getsize(path)
    summary = {"exists": "yes", "size": human_size(size)}
    try:
        with open(path, "r", newline="", errors="replace") as file:
            header = next(csv.reader(file), [])
    except (OSError, StopIteration):
        return summary
    if "timestamp" not in header:
        return summary
    timestamp_index = header.index("timestamp")
    first_line = first_data_line(path)
    last_line = last_nonempty_line(path)
    for label, line in [("first", first_line), ("last", last_line)]:
        if not line:
            continue
        try:
            row = next(csv.reader([line]))
        except csv.Error:
            continue
        if timestamp_index < len(row):
            summary[label] = parse_timestamp(row[timestamp_index])
    if size <= MAX_SMALL_CSV_MB * 1024 * 1024:
        try:
            with open(path, "r", errors="replace") as file:
                summary["rows"] = f"{max(0, sum(1 for _ in file) - 1):,}"
        except OSError:
            pass
    else:
        summary["rows"] = "large file"
    return summary


def manifest_summary(path: str) -> Dict[str, str]:
    if not os.path.exists(path):
        return {"exists": "no"}
    try:
        with open(path, newline="", errors="replace") as file:
            rows = list(csv.DictReader(file))
    except OSError:
        return {"exists": "yes", "summary": "unreadable"}
    statuses: Dict[str, int] = {}
    total_rows = 0
    for row in rows:
        statuses[row.get("status", "unknown")] = statuses.get(row.get("status", "unknown"), 0) + 1
        try:
            total_rows += int(float(row.get("rows") or 0))
        except ValueError:
            pass
    last = rows[-1] if rows else {}
    return {
        "exists": "yes",
        "chunks": str(len(rows)),
        "statuses": ", ".join(f"{key}: {value}" for key, value in sorted(statuses.items())) or "-",
        "total_rows": f"{total_rows:,}",
        "last_chunk": f"{last.get('start', '-') } -> {last.get('end', '-') } {last.get('status', '')}".strip(),
    }


def tmp_outputs(directory: str) -> List[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(str(path) for path in Path(directory).glob("*.tmp"))


def service_rows() -> List[Dict]:
    plists = read_plists()
    labels = sorted(set(SERVICE_CATALOG) | set(plists))
    rows = []
    for label in labels:
        catalog = SERVICE_CATALOG.get(label, ServiceInfo(label, "Unknown", "Discovered", "", "", ""))
        status = launchctl_status(label)
        plist = plists.get(label, {})
        command = " ".join(plist.get("ProgramArguments", [])) or catalog.command_hint
        logs = catalog.logs[:]
        for key in ["StandardOutPath", "StandardErrorPath"]:
            if plist.get(key) and plist[key] not in logs:
                logs.append(plist[key])
        rows.append({
            "label": label,
            "mode": catalog.mode,
            "expected": catalog.status,
            "loaded": status.get("loaded", "no"),
            "state": status.get("state", "-"),
            "pid": status.get("pid", "-"),
            "runs": status.get("runs", "-"),
            "last_exit": status.get("last exit code", status.get("last terminating signal", "-")),
            "purpose": catalog.purpose,
            "necessity": catalog.necessity,
            "stop_when": catalog.stop_when,
            "command": command,
            "outputs": catalog.outputs,
            "logs": logs,
            "plist": plist.get("_path", status.get("path", "-")),
        })
    return rows


def asset_rows() -> List[Dict]:
    rows = []
    for name, path, note in DATA_ASSETS:
        summary = manifest_summary(path) if path.endswith("manifest.csv") else csv_range(path)
        rows.append({"name": name, "path": path, "note": note, **summary})
    return rows


def render_status_badge(row: Dict) -> str:
    loaded = row.get("loaded")
    state = row.get("state")
    if loaded == "yes" and state == "running":
        klass = "ok"
        text = "running"
    elif loaded == "yes":
        klass = "warn"
        text = state or "loaded"
    else:
        klass = "muted"
        text = "not loaded"
    return f'<span class="badge {klass}">{html.escape(text)}</span>'


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def render_dashboard(output_path: str):
    services = service_rows()
    assets = asset_rows()
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    l2_tmp = tmp_outputs("data/microstructure/hyperliquid_l2_monthly/SOL")

    service_cards = []
    for row in services:
        log_blocks = []
        for log_path in row["logs"]:
            lines = tail_lines(log_path)
            if lines:
                log_blocks.append(
                    f"<details><summary>{esc(log_path)}</summary><pre>{esc(chr(10).join(lines))}</pre></details>"
                )
        outputs = "".join(f"<li><code>{esc(path)}</code></li>" for path in row["outputs"]) or "<li>-</li>"
        service_cards.append(f"""
        <section class="card">
          <div class="card-title">
            <h2>{esc(row['label'])}</h2>
            {render_status_badge(row)}
          </div>
          <p class="meta">{esc(row['mode'])} | expected: {esc(row['expected'])} | pid: {esc(row['pid'])} | runs: {esc(row['runs'])}</p>
          <p><strong>Purpose:</strong> {esc(row['purpose'])}</p>
          <p><strong>Necessity:</strong> {esc(row['necessity'])}</p>
          <p><strong>Stop when:</strong> {esc(row['stop_when'])}</p>
          <p><strong>Plist:</strong> <code>{esc(row['plist'])}</code></p>
          <p><strong>Command:</strong> <code>{esc(row['command'])}</code></p>
          <p><strong>Outputs:</strong></p>
          <ul>{outputs}</ul>
          {''.join(log_blocks)}
        </section>
        """)

    asset_rows_html = []
    for row in assets:
        asset_rows_html.append(f"""
        <tr>
          <td>{esc(row['name'])}</td>
          <td><code>{esc(row['path'])}</code></td>
          <td>{esc(row.get('exists', '-'))}</td>
          <td>{esc(row.get('size', '-'))}</td>
          <td>{esc(row.get('rows', row.get('total_rows', '-')))}</td>
          <td>{esc(row.get('first', row.get('last_chunk', '-')))}</td>
          <td>{esc(row.get('last', row.get('statuses', '-')))}</td>
          <td>{esc(row['note'])}</td>
        </tr>
        """)

    tmp_html = "".join(f"<li><code>{esc(path)}</code></li>" for path in l2_tmp) or "<li>None</li>"
    body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Hummingbot Data Services Dashboard</title>
  <style>
    :root {{ color-scheme: light dark; --border:#d6d6d6; --muted:#666; --ok:#12833b; --warn:#9a6700; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 28px; line-height: 1.45; }}
    h1 {{ margin-bottom: 0; }}
    .meta {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 16px; }}
    .card {{ border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
    .card-title {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .card h2 {{ font-size: 16px; margin: 0; overflow-wrap: anywhere; }}
    .badge {{ border-radius: 999px; padding: 3px 10px; font-size: 12px; color: white; white-space: nowrap; }}
    .badge.ok {{ background: var(--ok); }}
    .badge.warn {{ background: var(--warn); }}
    .badge.muted {{ background: #777; }}
    code {{ font-size: 12px; overflow-wrap: anywhere; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid var(--border); border-radius: 6px; padding: 10px; max-height: 220px; overflow: auto; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
    th, td {{ border: 1px solid var(--border); padding: 8px; vertical-align: top; text-align: left; }}
    th {{ position: sticky; top: 0; background: Canvas; }}
    .section {{ margin-top: 28px; }}
  </style>
</head>
<body>
  <h1>Hummingbot Data Services Dashboard</h1>
  <p class="meta">Generated {esc(generated)} from <code>{esc(os.getcwd())}</code>.</p>

  <div class="section">
    <h2>Active And Known Services</h2>
    <div class="grid">
      {''.join(service_cards)}
    </div>
  </div>

  <div class="section">
    <h2>Data Assets</h2>
    <table>
      <thead>
        <tr><th>Name</th><th>Path</th><th>Exists</th><th>Size</th><th>Rows</th><th>First / Last chunk</th><th>Last / Statuses</th><th>Note</th></tr>
      </thead>
      <tbody>{''.join(asset_rows_html)}</tbody>
    </table>
  </div>

  <div class="section">
    <h2>Partial Outputs</h2>
    <p class="meta">Temporary files usually mean a chunk is currently being written or was interrupted before atomic rename.</p>
    <ul>{tmp_html}</ul>
  </div>
</body>
</html>
"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as file:
        file.write(body)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate Hummingbot data services dashboard")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main():
    args = parse_args()
    render_dashboard(args.output)
    print(f"Wrote dashboard: {args.output}")


if __name__ == "__main__":
    main()
