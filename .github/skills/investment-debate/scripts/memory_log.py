#!/usr/bin/env python3
"""Decision memory log for investment-debate: store, pending, settle, context, summarize, benchmark.

Port of TradingAgents memory/log.py (format, past context, rotation), memory/settlement.py (entry at the first
close after the decision date, exit holding-days sessions later, alpha against a benchmark) and backtest.py
(summary by rating). Local file processing only: the agent fetches closes from the Yahoo Finance MCP and passes them in.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_LOG = ROOT / "memory" / "decision-log.md"
SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
REVIEW = "REVIEW"
DIRECTION = {"Buy": 1, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -1}
# Yahoo tickers, because settlement closes come from the Yahoo Finance MCP.
BENCHMARKS = {"NSE": "^NSEI", "BSE": "^BSESN"}
US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS", "CBOE", "OTC", "NYSEARCA"}


def benchmark_for(symbol: str) -> str:
    exchange = symbol.split(":", 1)[0].upper()
    if exchange in BENCHMARKS:
        return BENCHMARKS[exchange]
    return "^GSPC" if exchange in US_EXCHANGES else ""


def parse_entry(raw: str) -> dict | None:
    lines = raw.strip().splitlines()
    if not lines or not (lines[0].startswith("[") and lines[0].endswith("]")):
        return None
    fields = [f.strip() for f in lines[0][1:-1].split("|")]
    if len(fields) < 4:
        return None
    resolved = None
    if fields[-1].startswith("resolved:"):
        resolved = fields.pop()[len("resolved:"):].strip()
    pending = fields[3] == "pending"
    body = "\n".join(lines[1:]).strip()
    decision, _, reflection = body.partition("\nREFLECTION:\n")
    return {
        "date": fields[0], "symbol": fields[1], "rating": fields[2], "pending": pending,
        "raw": None if pending else fields[3],
        "alpha": fields[4] if not pending and len(fields) > 4 else None,
        "holding": fields[5] if not pending and len(fields) > 5 else None,
        "resolved": resolved,
        "decision": decision.removeprefix("DECISION:\n").strip(),
        "reflection": reflection.strip(),
    }


def read_blocks(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [b for b in log.read_text(encoding="utf-8").split(SEPARATOR) if b.strip()]


def load(log: Path) -> list[dict]:
    return [e for e in (parse_entry(b) for b in read_blocks(log)) if e]


def write_blocks(log: Path, blocks: list[str]) -> None:
    """Atomic rewrite, so a crash mid-write never corrupts the log."""
    log.parent.mkdir(parents=True, exist_ok=True)
    text = SEPARATOR.join(b.strip() for b in blocks) + (SEPARATOR if blocks else "")
    fd, tmp = tempfile.mkstemp(dir=log.parent, prefix=".decision-log-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, log)


def store(log: Path, symbol: str, run_date: str, rating: str, decision: str) -> bool:
    """Append a pending entry unless one already exists for this date and symbol."""
    prefix = f"[{run_date} | {symbol} |"
    if any(b.strip().splitlines()[0].startswith(prefix) for b in read_blocks(log)):
        return False
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"[{run_date} | {symbol} | {rating} | pending]\n\nDECISION:\n{decision.strip()}{SEPARATOR}")
    return True


def replace_pending(log: Path, symbol: str, run_date: str, rating: str, decision: str) -> bool:
    """Replace the pending entry for this date and symbol with a new decision; settled entries are never changed.
    finalize_run.py uses it when a full run follows a same-day fast run, so the log tracks the fuller decision."""
    blocks = read_blocks(log)
    for i, block in enumerate(blocks):
        entry = parse_entry(block)
        if entry and entry["pending"] and entry["date"] == run_date and entry["symbol"] == symbol:
            blocks[i] = f"[{run_date} | {symbol} | {rating} | pending]\n\nDECISION:\n{decision.strip()}"
            write_blocks(log, blocks)
            return True
    return False


def window_from_csv(path: Path, decision_date: str, holding_days: int, today: str) -> dict | None:
    """Entry at the first close after the decision date; exit holding_days sessions later. None until it has traded."""
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            day = (row.get("date") or "").strip()[:10]
            close, bench = (row.get("close") or "").strip(), (row.get("bench_close") or "").strip()
            if not day or not close or not bench or day >= today:
                continue  # today's bar is still trading; missing closes are no price at all
            if float(close) <= 0 or float(bench) <= 0:
                continue
            rows.append((day, float(close), float(bench)))
    rows.sort()
    after = [r for r in rows if r[0] > decision_date]
    if len(after) <= holding_days:
        return None
    entry, exit_ = after[0], after[holding_days]
    return {"entry_date": entry[0], "entry_close": entry[1], "bench_entry": entry[2],
            "exit_date": exit_[0], "exit_close": exit_[1], "bench_exit": exit_[2]}


def rotate(blocks: list[str], max_entries: int | None) -> list[str]:
    """Drop the oldest-dated resolved entries beyond max_entries; pending entries are always kept."""
    if not max_entries or max_entries <= 0:
        return blocks
    parsed = [(b, parse_entry(b)) for b in blocks]
    resolved = sorted((e["date"], i) for i, (_, e) in enumerate(parsed) if e and not e["pending"])
    drop = {i for _, i in resolved[: max(0, len(resolved) - max_entries)]}
    return [b for i, (b, _) in enumerate(parsed) if i not in drop]


def settle(log: Path, symbol: str, run_date: str, window: dict, holding_days: int, reflection: str,
           max_entries: int | None = None) -> dict:
    raw = window["exit_close"] / window["entry_close"] - 1
    bench = window["bench_exit"] / window["bench_entry"] - 1
    alpha = raw - bench
    blocks, updated = read_blocks(log), False
    for i, block in enumerate(blocks):
        entry = parse_entry(block)
        if not updated and entry and entry["pending"] and entry["date"] == run_date and entry["symbol"] == symbol:
            tag = (f"[{run_date} | {symbol} | {entry['rating']} | {raw:+.1%} | {alpha:+.1%} | {holding_days}d"
                   f" | resolved:{window['exit_date']}]")
            blocks[i] = f"{tag}\n\nDECISION:\n{entry['decision']}\n\nREFLECTION:\n{reflection.strip()}"
            updated = True
    if not updated:
        raise SystemExit(f"no pending entry for {symbol} on {run_date}")
    write_blocks(log, rotate(blocks, max_entries))
    return {"raw": round(raw, 6), "benchmark_return": round(bench, 6), "alpha": round(alpha, 6), **window}


def past_context(log: Path, symbol: str, as_of: str | None, n_same: int = 5, n_cross: int = 3) -> str:
    """Same-symbol decisions in full, then cross-symbol reflections; only outcomes known by as_of."""
    entries = [e for e in load(log) if not e["pending"]]
    if as_of:
        entries = [e for e in entries if e["resolved"] and e["resolved"] <= as_of]
    newest = sorted(range(len(entries)), key=lambda i: (entries[i]["date"], i), reverse=True)
    same = [entries[i] for i in newest if entries[i]["symbol"] == symbol][:n_same]
    cross = [entries[i] for i in newest if entries[i]["symbol"] != symbol][:n_cross]
    if not same and not cross:
        return "No resolved past decisions yet."
    parts = []
    if same:
        parts.append(f"Past analyses of {symbol} (most recent first):")
        for e in same:
            tag = f"[{e['date']} | {e['symbol']} | {e['rating']} | {e['raw']} | {e['alpha']} | {e['holding']}]"
            parts.append("\n\n".join(filter(None, [tag, f"DECISION:\n{e['decision']}",
                                                   e["reflection"] and f"REFLECTION:\n{e['reflection']}"])))
    if cross:
        parts.append("Recent cross-ticker lessons:")
        for e in cross:
            tag = f"[{e['date']} | {e['symbol']} | {e['rating']} | {e['raw']}]"
            text = e["reflection"] or (e["decision"][:300] + ("..." if len(e["decision"]) > 300 else ""))
            parts.append(f"{tag}\n{text}")
    return "\n\n".join(parts)


def summarize(log: Path) -> str:
    """Decision quality by rating: count, direction hit rate, mean alpha (TradingAgents backtest summary)."""
    entries = load(log)
    resolved = []
    for e in entries:
        if e["pending"] or e["rating"] == REVIEW:
            continue
        try:
            resolved.append((e, float((e["alpha"] or "").rstrip("%")) / 100))
        except ValueError:
            continue
    unscored = sum(e["rating"] == REVIEW for e in entries)
    pending = sum(e["pending"] for e in entries if e["rating"] != REVIEW)
    head = f"Resolved cells: {len(resolved)} · pending: {pending}" + (f" · unscored: {unscored}" if unscored else "")
    lines = [head]
    for rating in dict.fromkeys(e["rating"] for e, _ in resolved):
        alphas = [a for e, a in resolved if e["rating"] == rating]
        direction = DIRECTION.get(rating, 0)
        called = (f"called the direction {sum(a * direction > 0 for a in alphas) / len(alphas):.0%}"
                  if direction else "no direction claimed")
        lines.append(f"- {rating}: n={len(alphas)}, {called}, mean alpha {sum(alphas) / len(alphas):+.2%} vs the benchmark")
    windows = sorted({f"{e['holding'][:-1]} trading days" for e, _ in resolved if (e["holding"] or "").endswith("d")})
    lines.append("")
    if pending:
        lines.append("Pending cells are not scored above; settle them and summarize again.")
    lines.append(f"Alpha is measured over {', '.join(windows) or 'the configured window'} from the first close after "
                 "each analysis date. One model sampling per cell, and news feeds and current snapshots are not "
                 "point-in-time archives, so these figures are indicative rather than repeatable.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="memory log path (default memory/decision-log.md)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("store", help="append a pending decision")
    p.add_argument("--symbol", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--rating", required=True)
    p.add_argument("--decision-file", required=True, type=Path)

    p = sub.add_parser("pending", help="list pending decisions as JSON")
    p.add_argument("--symbol")

    p = sub.add_parser("settle", help="score a pending decision and add its reflection")
    p.add_argument("--symbol", required=True)
    p.add_argument("--date", required=True, help="decision date")
    p.add_argument("--closes-csv", required=True, type=Path,
                   help="CSV with columns date,close,bench_close (daily closes after the decision date)")
    p.add_argument("--holding-days", type=int, default=20)
    p.add_argument("--reflection-file", type=Path, help="2-4 sentence reflection; omit to only compute returns")
    p.add_argument("--today", default=date.today().isoformat())
    p.add_argument("--max-entries", type=int)

    p = sub.add_parser("context", help="past context for the portfolio manager")
    p.add_argument("--symbol", required=True)
    p.add_argument("--as-of", help="only outcomes resolved on or before this date")
    p.add_argument("--out", type=Path)

    sub.add_parser("summarize", help="score settled decisions by rating")

    p = sub.add_parser("benchmark", help="default benchmark for a symbol")
    p.add_argument("--symbol", required=True)

    args = parser.parse_args(argv)
    if args.command == "store":
        added = store(args.log, args.symbol, args.date, args.rating, args.decision_file.read_text(encoding="utf-8"))
        print(json.dumps({"memory_entry": "added" if added else "already present", "log": str(args.log)}))
    elif args.command == "pending":
        rows = [{"date": e["date"], "symbol": e["symbol"], "rating": e["rating"],
                 "benchmark": benchmark_for(e["symbol"]) or "look up the home-market index's Yahoo ticker"}
                for e in load(args.log) if e["pending"] and (not args.symbol or e["symbol"] == args.symbol)]
        print(json.dumps(rows, indent=2))
    elif args.command == "settle":
        window = window_from_csv(args.closes_csv, args.date, args.holding_days, args.today)
        if window is None:
            print(json.dumps({"status": "not yet settleable", "reason":
                              f"fewer than {args.holding_days + 1} completed sessions after {args.date}"}))
            return 0
        if not args.reflection_file:
            raw = window["exit_close"] / window["entry_close"] - 1
            alpha = raw - (window["bench_exit"] / window["bench_entry"] - 1)
            print(json.dumps({"status": "ready", "raw": round(raw, 6), "alpha": round(alpha, 6), **window}, indent=2))
            return 0
        result = settle(args.log, args.symbol, args.date, window, args.holding_days,
                        args.reflection_file.read_text(encoding="utf-8"), args.max_entries)
        print(json.dumps({"status": "settled", **result}, indent=2))
    elif args.command == "context":
        text = past_context(args.log, args.symbol, args.as_of)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
    elif args.command == "summarize":
        print(summarize(args.log))
    elif args.command == "benchmark":
        print(benchmark_for(args.symbol) or "unknown: look up the home-market index's Yahoo ticker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
