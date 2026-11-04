#!/usr/bin/env python3
"""Inspect or reset the shared MCP cache and run history (standard library only).

Usage:
  python3 mcp/cache_admin.py path                      where the database is
  python3 mcp/cache_admin.py stats [--hours 24]        calls, hits and misses per tool
  python3 mcp/cache_admin.py clear [--server S] [--tool T]
  python3 mcp/cache_admin.py prune                     drop expired entries and old call logs
  python3 mcp/cache_admin.py bars TICKER [--interval 1d] [--start D] [--end D]
  python3 mcp/cache_admin.py runs [--symbol EXCHANGE:TICKER] [--limit 20]
  python3 mcp/cache_admin.py stages RUN_ID
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from datastore import Store, default_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, help=f"database path (default {default_path()})")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("path")
    p = sub.add_parser("stats")
    p.add_argument("--hours", type=float, default=24)
    p = sub.add_parser("clear")
    p.add_argument("--server")
    p.add_argument("--tool")
    sub.add_parser("prune")
    p = sub.add_parser("bars")
    p.add_argument("ticker", help="Yahoo ticker, e.g. TATAPOWER.NS or MSFT")
    p.add_argument("--interval", default="1d")
    p.add_argument("--start")
    p.add_argument("--end")
    p = sub.add_parser("runs")
    p.add_argument("--symbol")
    p.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("stages")
    p.add_argument("run_id")
    args = parser.parse_args(argv)

    if args.command == "path":
        print(args.db or default_path())
        return 0
    with Store(args.db) as store:
        if args.command == "stats":
            rows = store.stats(time.time() - args.hours * 3600)
            hits = sum(r["calls"] for r in rows if r["outcome"] in ("hit", "coalesced"))
            total = sum(r["calls"] for r in rows)
            print(json.dumps({"hours": args.hours, "calls": total, "served_from_cache": hits,
                              "hit_rate": round(hits / total, 3) if total else None, "by_tool": rows}, indent=1))
        elif args.command == "clear":
            print(json.dumps({"deleted": store.clear(args.server, args.tool)}))
        elif args.command == "prune":
            print(json.dumps(store.prune()))
        elif args.command == "bars":
            print(json.dumps(store.get_bars(args.ticker, args.interval, args.start, args.end), indent=1))
        elif args.command == "runs":
            print(json.dumps(store.runs(args.symbol, args.limit), indent=1))
        elif args.command == "stages":
            print(json.dumps(store.stages(args.run_id), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
