#!/usr/bin/env python3
"""List, and optionally stop, idle my_fin MCP server processes left by old chats.

Each Copilot chat session starts its own technicals, yahoo-finance and tradingmcp servers (plus the caching
proxy and uv wrappers). The VS Code agent host keeps a session's servers running while it keeps the session
loaded, so old chats leave groups of idle processes behind (27 processes, about 4 GB, were seen on one machine).
This tool groups them by the chat that started them.

Usage:
  python3 mcp/servers.py                        list the groups (age, memory, servers)
  python3 mcp/servers.py --stop-older-than 24   stop groups older than 24 hours (SIGTERM)

Stopping a group only affects the chat that started it: if you return to that chat, restart its servers with
"MCP: List Servers" in VS Code, or start a new chat. Linux and WSL only (reads ps output).
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys

MARKERS = ("market_technicals.py", "bin/yahoo-finance-mcp", "bin/tradingview-mcp", "cache_proxy.py",
           "mcp/launch.sh", "yahoo-finance-mcp@", "tradingview-mcp-server==")
NAMES = {"market_technicals.py": "technicals", "yahoo-finance-mcp": "yahoo-finance", "tradingview-mcp": "tradingmcp"}


def processes() -> dict[int, dict]:
    out = subprocess.run(["ps", "-eo", "pid=,ppid=,etimes=,rss=,args="], capture_output=True, text=True).stdout
    rows = {}
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) == 5:
            pid, ppid, age, rss, args = parts
            rows[int(pid)] = {"pid": int(pid), "ppid": int(ppid), "age_s": int(age), "rss_kb": int(rss), "args": args}
    return rows


def groups(rows: dict[int, dict]) -> list[dict]:
    """One group per chat session: the server trees a host started within a minute of each other."""
    mine = {pid: r for pid, r in rows.items() if any(m in r["args"] for m in MARKERS) and pid != os.getpid()}
    trees: dict[int, list[int]] = {}
    for pid in mine:
        root = pid
        while rows.get(root, {}).get("ppid") in mine:
            root = rows[root]["ppid"]
        trees.setdefault(root, []).append(pid)
    found: list[dict] = []
    for root in sorted(trees, key=lambda r: (rows[r]["ppid"], -rows[r]["age_s"])):
        host, age = rows[root]["ppid"], rows[root]["age_s"]
        group = found[-1] if found and found[-1]["host"] == host and abs(found[-1]["_age"] - age) <= 60 else None
        if group is None:
            group = {"host": host, "_age": age, "age_h": round(age / 3600, 1), "pids": [], "rss_kb": 0, "servers": set()}
            found.append(group)
        for pid in trees[root]:
            group["pids"].append(pid)
            group["rss_kb"] += rows[pid]["rss_kb"]
            for marker, name in NAMES.items():
                if marker in rows[pid]["args"]:
                    group["servers"].add(name)
    return sorted(found, key=lambda g: -g["_age"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stop-older-than", type=float, metavar="HOURS", help="stop groups older than this many hours")
    args = parser.parse_args(argv)
    found = groups(processes())
    total = sum(g["rss_kb"] for g in found)
    print(f"{len(found)} chat session(s) with servers, {sum(len(g['pids']) for g in found)} processes, {total / 1024:.0f} MB")
    for g in found:
        stop = args.stop_older_than is not None and g["age_h"] > args.stop_older_than
        print(f"  host {g['host']:>7}: started {g['age_h']:7.1f} h ago, {g['rss_kb'] / 1024:6.0f} MB, "
              f"{len(g['pids'])} processes, {', '.join(sorted(g['servers'])) or '?'}" + ("  -> stopping" if stop else ""))
        if stop:
            for pid in g["pids"]:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
    if args.stop_older_than is None and found:
        print("Run with --stop-older-than HOURS to stop old groups.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
