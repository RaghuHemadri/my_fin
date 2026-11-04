#!/usr/bin/env python3
"""Precompute investment-debate runs for your holdings and watchlist, so questions get answered from a ready report.

Run it after the markets close (for example from Windows Task Scheduler with
tools/schedule/register-windows-task.ps1, or from cron). For each symbol in portfolio/holdings.csv and
portfolio/watchlist.csv that has no finished report for today, it runs the headless debate runner
(.github/skills/investment-debate/scripts/run_debate.py). When you ask about one of them later, the
"Follow-ups and reuse" rule in the investment-debate skill answers from that report instead of starting a
new 20-40 minute debate.

The analysis is the same as a run started in chat; only the time it runs changes. Tapetide allows 50 calls a
day and each run uses up to about 10, so --max-runs (default 4) leaves room for questions during the day.
The Copilot CLI must be logged in once (`copilot`, then `/login`).

portfolio/watchlist.csv (optional, git-ignored; see watchlist.example.csv): symbol[,holding_days][,note]

Usage:
  python3 tools/precompute.py [--max-runs 4] [--parallel 2] [--holding-days 20] [--no-holdings] [--dry-run]
                              [-- <extra run_debate.py options, e.g. --model ... --quick-effort high>]
Writes a summary to reports/precompute/<date>.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "skills" / "investment-debate" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def read_symbols(path: Path, default_days: int) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol or symbol.startswith("#"):
                continue
            days = (row.get("holding_days") or "").strip()
            rows.append({"symbol": symbol, "holding_days": int(days) if days.isdigit() else default_days,
                         "source": path.name})
    return rows


def has_report_today(reports: Path, symbol: str, today: str) -> bool:
    folder = reports / symbol.replace(":", "_")
    return any((run / "complete_report.md").exists() for run in folder.glob(f"{today}*") if run.is_dir())


def select(holdings: Path, watchlist: Path, reports: Path, today: str, default_days: int, max_runs: int,
           include_holdings: bool = True) -> tuple[list[dict], list[dict]]:
    """(to_run, skipped). Holdings come first, then the watchlist; duplicates keep their first entry."""
    candidates = (read_symbols(holdings, default_days) if include_holdings else []) + read_symbols(watchlist, default_days)
    seen, to_run, skipped = set(), [], []
    for item in candidates:
        if item["symbol"] in seen:
            continue
        seen.add(item["symbol"])
        if has_report_today(reports, item["symbol"], today):
            skipped.append({**item, "reason": "a report for today already exists"})
        elif len(to_run) >= max_runs:
            skipped.append({**item, "reason": f"over --max-runs {max_runs} (Tapetide: 50 calls a day)"})
        else:
            to_run.append(item)
    return to_run, skipped


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[: argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--holdings", type=Path, default=ROOT / "portfolio" / "holdings.csv")
    parser.add_argument("--watchlist", type=Path, default=ROOT / "portfolio" / "watchlist.csv")
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--holding-days", type=int, default=20, help="for symbols whose row gives none")
    parser.add_argument("--max-runs", type=int, default=4)
    parser.add_argument("--parallel", type=int, default=2)
    parser.add_argument("--no-holdings", action="store_true", help="only the watchlist")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--today", default=date.today().isoformat(), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    to_run, skipped = select(args.holdings, args.watchlist, args.reports_dir, args.today, args.holding_days,
                             args.max_runs, not args.no_holdings)
    summary = {"date": args.today, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "to_run": to_run, "skipped": skipped, "runs": []}
    if args.dry_run or not to_run:
        print(json.dumps(summary, indent=1))
        return 0

    import run_debate  # noqa: PLC0415
    groups: dict[int, list[str]] = {}
    for item in to_run:  # one runner call per holding period, instruments in parallel inside it
        groups.setdefault(item["holding_days"], []).append(item["symbol"])
    exit_code = 0
    for days, symbols in groups.items():
        code = run_debate.main([*symbols, "--holding-days", str(days), "--parallel", str(args.parallel), *extra])
        summary["runs"].append({"symbols": symbols, "holding_days": days, "exit_code": code})
        exit_code = exit_code or code
        if code == 3:  # not logged in: the other groups would fail the same way
            break
    summary["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = args.reports_dir / "precompute" / f"{args.today}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
