#!/usr/bin/env python3
"""Settle pending investment-debate decisions with data from the configured MCP servers.

memory-log.md describes the settlement: fetch closes for the symbol and its benchmark, write them to a CSV,
check the holding window with memory_log.py, write a 2-4 sentence reflection, then settle. This script does
the data part deterministically; only the reflection is left to the model.

  prepare  For every pending entry: find its holding period and benchmark (from its own run's
           0_settings.json), skip the fetch when the window cannot have elapsed yet (weekdays are an upper
           bound on sessions), otherwise fetch daily closes through yahoo-finance get_historical_stock_prices
           (shared cache), write <dir>/0_settle_<SAFE>_<DATE>.csv exactly from them, and report whether the
           window has traded, with the raw return and alpha.
  apply    For every entry whose closes file and reflection file exist, settle it with memory_log.settle,
           then rewrite RUN/0_past_context.md (when --run is given and the portfolio manager has not run).

<dir> is RUN when --run is given, otherwise memory/settlements/. Reflections go to
<dir>/0_reflection_<SAFE>_<DATE>.md, as memory-log.md says.

Usage:
  python3 .github/skills/investment-debate/scripts/settle_pending.py prepare [--run RUN] [--log LOG]
  python3 .github/skills/investment-debate/scripts/settle_pending.py apply   [--run RUN] [--log LOG] [--max-entries N]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "mcp"))
import memory_log  # noqa: E402
import run_plan  # noqa: E402
from data_formats import parse_json, session_day  # noqa: E402
from prepare_run import period_covering, rel, safe_symbol, yahoo_ticker_for  # noqa: E402


def weekdays_between(start: str, end: str) -> int:
    """Weekdays strictly after start and strictly before end: an upper bound on trading sessions."""
    day, stop, count = date.fromisoformat(start) + timedelta(days=1), date.fromisoformat(end), 0
    while day < stop:
        count += day.weekday() < 5
        day += timedelta(days=1)
    return count


def entry_settings(reports: Path, symbol: str, decision_date: str) -> dict:
    """The run settings of a past decision, with its Yahoo ticker. Runs set up before prepare_run.py recorded the
    ticker only as a 'Yahoo ticker:' line in 0_instrument.md."""
    base = reports / safe_symbol(symbol)
    for run in [base / decision_date, *sorted(base.glob(f"{decision_date}-*"))]:
        settings = run_plan.load_settings(run)
        if settings:
            if not settings.get("yahoo_ticker") and (run / "0_instrument.md").exists():
                match = re.search(r"Yahoo ticker:\s*`?([^\s`,;]+)", (run / "0_instrument.md").read_text(encoding="utf-8"))
                if match:
                    settings["yahoo_ticker"] = match.group(1)
            return {**settings, "_run": run}
    return {}


def closes(text: str | None) -> dict[str, float]:
    bars = parse_json(text)
    if not isinstance(bars, list):
        return {}
    return {session_day(b["Date"]): float(b["Close"]) for b in bars
            if isinstance(b, dict) and b.get("Date") and isinstance(b.get("Close"), (int, float))}


def files_for(folder: Path, symbol: str, decision_date: str) -> tuple[Path, Path]:
    stem = f"{safe_symbol(symbol)}_{decision_date}"
    return folder / f"0_settle_{stem}.csv", folder / f"0_reflection_{stem}.md"


def prepare(log: Path, folder: Path, reports: Path, today: str, default_days: int = 20, servers_factory=None) -> dict:
    pending = [e for e in memory_log.load(log) if e["pending"]]
    plans, to_fetch = [], []
    for entry in pending:
        symbol, decided = entry["symbol"], entry["date"]
        settings = entry_settings(reports, symbol, decided)
        days = int(settings.get("holding_days") or default_days)
        item = {"date": decided, "symbol": symbol, "rating": entry["rating"], "holding_days": days}
        if not settings:
            item["note"] = f"run settings not found; holding_days defaults to {days}"
        ticker = settings.get("yahoo_ticker") or yahoo_ticker_for(symbol)
        benchmark = settings.get("benchmark") or memory_log.benchmark_for(symbol)
        sessions = weekdays_between(decided, today)
        if not ticker or not benchmark:
            item.update(status="skipped", reason=f"no {'Yahoo ticker' if not ticker else 'benchmark'} for {symbol}; "
                                                 "settle it by hand as memory-log.md describes")
        elif sessions <= days:
            item.update(status="not yet settleable",
                        reason=f"at most {sessions} sessions since the decision; the window needs {days + 1}")
        else:
            item.update(ticker=ticker, benchmark=benchmark)
            to_fetch.append(item)
        if settings.get("_run") and (Path(settings["_run"]) / "5_portfolio" / "decision.md").exists():
            item["decision_file"] = rel(Path(settings["_run"]) / "5_portfolio" / "decision.md")
        plans.append(item)

    if to_fetch:
        from stdio_client import LocalServers, result_text  # noqa: PLC0415
        calls = []
        for item in to_fetch:
            period = period_covering(item["date"], today, 10)
            calls += [("yahoo-finance", "get_historical_stock_prices", {"ticker": item["ticker"], "period": period, "interval": "1d"}),
                      ("yahoo-finance", "get_historical_stock_prices", {"ticker": item["benchmark"], "period": period, "interval": "1d"})]
        factory = servers_factory or (lambda names: LocalServers(names))
        with factory(["yahoo-finance"]) as servers:
            results = servers.call_many(calls)
        folder.mkdir(parents=True, exist_ok=True)
        for n, item in enumerate(to_fetch):
            stock, bench = results[2 * n], results[2 * n + 1]
            if isinstance(stock, Exception) or isinstance(bench, Exception):
                item.update(status="skipped", reason=f"price fetch failed: {stock if isinstance(stock, Exception) else bench}")
                continue
            stock_closes, bench_closes = closes(result_text(stock)), closes(result_text(bench))
            if not stock_closes:
                item.update(status="skipped", reason=f"no bars for {item['ticker']}")
                continue
            closes_csv, reflection = files_for(folder, item["symbol"], item["date"])
            with closes_csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["date", "close", "bench_close"])
                for day in sorted(stock_closes):
                    if day > item["date"]:
                        writer.writerow([day, repr(stock_closes[day]), repr(bench_closes[day]) if day in bench_closes else ""])
            window = memory_log.window_from_csv(closes_csv, item["date"], item["holding_days"], today)
            item["closes_csv"] = rel(closes_csv)
            if window is None:
                item.update(status="not yet settleable", reason="the holding window has not finished trading")
                continue
            raw = window["exit_close"] / window["entry_close"] - 1
            bench_return = window["bench_exit"] / window["bench_entry"] - 1
            item.update(status="ready", raw=round(raw, 6), benchmark_return=round(bench_return, 6),
                        alpha=round(raw - bench_return, 6), **window, reflection_file=rel(reflection))
    ready = [p for p in plans if p["status"] == "ready"]
    out = {"log": rel(log), "today": today, "pending": len(pending), "ready": len(ready), "entries": plans}
    if ready:
        out["next_step"] = ("Write each ready entry's reflection to its reflection_file with the reflection prompt in "
                            "references/memory-log.md, then run settle_pending.py apply with the same --run and --log.")
    return out


def apply(log: Path, folder: Path, reports: Path, today: str, run: Path | None, max_entries: int | None,
          default_days: int = 20) -> dict:
    settled, waiting = [], []
    for entry in [e for e in memory_log.load(log) if e["pending"]]:
        closes_csv, reflection = files_for(folder, entry["symbol"], entry["date"])
        if not closes_csv.exists():
            continue
        days = int(entry_settings(reports, entry["symbol"], entry["date"]).get("holding_days") or default_days)
        window = memory_log.window_from_csv(closes_csv, entry["date"], days, today)
        if window is None:
            continue
        if not reflection.exists() or not reflection.read_text(encoding="utf-8").strip():
            waiting.append({"date": entry["date"], "symbol": entry["symbol"], "reflection_file": rel(reflection)})
            continue
        result = memory_log.settle(log, entry["symbol"], entry["date"], window, days,
                                   reflection.read_text(encoding="utf-8"), max_entries)
        settled.append({"date": entry["date"], "symbol": entry["symbol"], **result})
    out = {"log": rel(log), "settled": settled, "missing_reflections": waiting}
    if run is not None and settled:
        settings = run_plan.load_settings(run)
        if settings and not (run / "5_portfolio" / "decision.md").exists():
            context = memory_log.past_context(log, settings["symbol"], settings["as_of"])
            (run / "0_past_context.md").write_text(context.rstrip("\n") + "\n", encoding="utf-8")
            out["past_context"] = rel(run / "0_past_context.md")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("prepare", "apply"))
    parser.add_argument("--run", type=Path, help="current run folder: files go there and its past context is refreshed")
    parser.add_argument("--log", type=Path, default=memory_log.DEFAULT_LOG)
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--holding-days", type=int, default=20, help="used when an entry's run settings are missing")
    parser.add_argument("--max-entries", type=int)
    parser.add_argument("--today", default=date.today().isoformat())
    args = parser.parse_args(argv)
    folder = args.run if args.run else ROOT / "memory" / "settlements"
    if args.command == "prepare":
        out = prepare(args.log, folder, args.reports_dir, args.today, args.holding_days)
    else:
        out = apply(args.log, folder, args.reports_dir, args.today, args.run, args.max_entries, args.holding_days)
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
