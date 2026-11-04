#!/usr/bin/env python3
"""A tiny stdio MCP server for tests (standard library only).

It behaves like the FastMCP servers this project proxies: each tool result is sent twice, as text content and
as structuredContent {"result": <same text>}, and the tools carry an outputSchema. It counts calls per tool in
a file given by FAKE_MCP_COUNTS, so a test can tell a cache hit from an upstream call.

Tools: get_stock_info(ticker), get_historical_stock_prices(ticker, period, interval), failing_tool(),
slow_tool(seconds).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

TOOLS = [
    {"name": "get_stock_info", "description": "Fake stock info.",
     "inputSchema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
     "outputSchema": {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}},
    {"name": "get_historical_stock_prices", "description": "Fake daily bars.",
     "inputSchema": {"type": "object", "properties": {"ticker": {"type": "string"}, "period": {"type": "string"},
                                                      "interval": {"type": "string"}}, "required": ["ticker"]},
     "outputSchema": {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}},
    {"name": "failing_tool", "description": "Always returns an error payload.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "slow_tool", "description": "Sleeps, then answers.",
     "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}}},
]
_lock = threading.Lock()


def count(tool: str) -> None:
    path = os.environ.get("FAKE_MCP_COUNTS")
    if not path:
        return
    with _lock:
        counts = json.load(open(path)) if os.path.exists(path) else {}
        counts[tool] = counts.get(tool, 0) + 1
        with open(path, "w") as handle:
            json.dump(counts, handle)


def send(message: dict) -> None:
    with _lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def tool_result(name: str, args: dict) -> dict:
    count(name)
    if name == "get_stock_info":
        if args.get("ticker") == "BAD":
            text = json.dumps({"error": "symbol not found"})
            return {"content": [{"type": "text", "text": text}], "structuredContent": {"result": text}, "isError": False}
        text = json.dumps({"symbol": args.get("ticker"), "currency": "USD", "longName": "Fake Corp",
                           "quoteType": "EQUITY", "sector": "Technology", "industry": "Software",
                           "exchange": "NMS", "regularMarketPrice": 101.5})
        return {"content": [{"type": "text", "text": text}], "structuredContent": {"result": text}, "isError": False}
    if name == "get_historical_stock_prices":
        bars = [{"Date": f"2026-10-{d:02d}T04:00:00.000Z", "Open": 100.0 + d, "High": 101.0 + d, "Low": 99.0 + d,
                 "Close": 100.5 + d, "Volume": 1000 * d, "Dividends": 0.0, "Stock Splits": 0.0} for d in range(1, 6)]
        text = json.dumps(bars)
        return {"content": [{"type": "text", "text": text}], "structuredContent": {"result": text}, "isError": False}
    if name == "failing_tool":
        return {"content": [{"type": "text", "text": json.dumps({"error": "API key not configured"})}], "isError": False}
    if name == "slow_tool":
        time.sleep(float(args.get("seconds", 0.5)))
        return {"content": [{"type": "text", "text": "done"}], "isError": False}
    return {"content": [{"type": "text", "text": f"Unknown tool {name}"}], "isError": True}


def handle(message: dict) -> None:
    method, mid = message.get("method"), message.get("id")
    if mid is None:
        return  # notifications
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": message["params"].get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fake", "version": "1.0"}, "instructions": "Fake server for tests."}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = message.get("params") or {}
        send({"jsonrpc": "2.0", "id": mid, "result": tool_result(params.get("name"), params.get("arguments") or {})})
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if line:
            threading.Thread(target=handle, args=(json.loads(line),), daemon=True).start()


if __name__ == "__main__":
    main()
