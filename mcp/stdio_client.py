#!/usr/bin/env python3
"""Minimal MCP stdio client for this project's scripts (standard library only).

Scripts call the configured local data servers through it, so their data comes from the same MCP servers
(and the same shared cache) the agents use; nothing calls a market-data API directly. A server is started with
`bash mcp/launch.sh <name>`, which puts it behind the caching proxy unless MY_FIN_CACHE=0.

Library use:
    with LocalServers(["yahoo-finance", "technicals"]) as servers:
        result = servers.call("yahoo-finance", "get_stock_info", {"ticker": "TCS.NS"})
        results = servers.call_many([("technicals", "price_statistics", {"symbol": "NSE:TCS"}), ...])

Command line:
    python3 mcp/stdio_client.py call yahoo-finance get_stock_info '{"ticker": "MSFT"}' [--raw]
    python3 mcp/stdio_client.py tools tradingmcp
    python3 mcp/stdio_client.py health

`tapetide` is a remote server with Google sign-in. Scripts cannot call it; agents do.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

MCP_DIR = Path(__file__).resolve().parent
LOCAL_SERVERS = ("technicals", "yahoo-finance", "tradingmcp")
PROTOCOL_VERSION = "2025-06-18"


class MCPError(RuntimeError):
    """A JSON-RPC error, a timeout or a server that stopped."""


def launch_command(server: str) -> list[str]:
    if server not in LOCAL_SERVERS:
        raise ValueError(f"{server} is not a local server ({', '.join(LOCAL_SERVERS)}); tapetide needs an agent")
    return ["bash", str(MCP_DIR / "launch.sh"), server]


def result_text(result: dict) -> str:
    """The text of a tool result: its text blocks joined by blank lines."""
    return "\n\n".join(b.get("text", "") for b in result.get("content") or [] if b.get("type") == "text")


def cache_status(result: dict) -> str:
    return ((result.get("_meta") or {}).get("my_fin/cache") or {}).get("status", "")


class StdioMCP:
    """One MCP server process spoken to over stdin/stdout. Thread-safe; requests may run concurrently."""

    def __init__(self, command: list[str], name: str = "server", cwd: str | Path | None = None,
                 env: dict | None = None, start_timeout: float = 240, call_timeout: float = 240,
                 stderr: Any = subprocess.DEVNULL):
        self.name = name
        self.call_timeout = call_timeout
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._closed = False
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                     text=True, encoding="utf-8", bufsize=1, cwd=cwd, env=env)
        self._reader = threading.Thread(target=self._read_loop, name=f"mcp-{name}-reader", daemon=True)
        self._reader.start()
        init = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                           "clientInfo": {"name": "my_fin-scripts", "version": "1"}},
                            timeout=start_timeout)
        self.server_info = init.get("serverInfo") or {}
        self.instructions = init.get("instructions") or ""
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message: dict) -> None:
        with self._write_lock:
            if self.proc.stdin is None or self.proc.stdin.closed:
                raise MCPError(f"{self.name}: connection closed")
            try:
                self.proc.stdin.write(json.dumps(message) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MCPError(f"{self.name}: server stopped ({exc})") from exc

    def _read_loop(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "method" in message and "id" in message:  # a request from the server
                reply = {"jsonrpc": "2.0", "id": message["id"]}
                if message["method"] == "ping":
                    reply["result"] = {}
                else:
                    reply["error"] = {"code": -32601, "message": "not supported by this client"}
                try:
                    self._send(reply)
                except MCPError:
                    pass
            elif "id" in message:
                with self._lock:
                    slot = self._pending.get(message["id"])
                if slot:
                    slot["message"] = message
                    slot["event"].set()
        with self._lock:
            self._closed = True
            for slot in self._pending.values():
                slot["message"] = {"error": {"code": -32000, "message": "server stopped"}}
                slot["event"].set()

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        with self._lock:
            if self._closed:
                raise MCPError(f"{self.name}: server stopped")
            self._next_id += 1
            request_id = self._next_id
            slot = self._pending[request_id] = {"event": threading.Event(), "message": None}
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
            if not slot["event"].wait(timeout or self.call_timeout):
                raise MCPError(f"{self.name}: {method} timed out after {timeout or self.call_timeout:.0f}s")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        message = slot["message"]
        if "error" in message:
            raise MCPError(f"{self.name}: {method}: {message['error'].get('message')}")
        return message.get("result") or {}

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(page.get("tools") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, tool: str, arguments: dict | None = None, timeout: float | None = None) -> dict:
        return self.request("tools/call", {"name": tool, "arguments": arguments or {}}, timeout)

    def close(self) -> None:
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self._reader.join(timeout=5)
        if self.proc.stdout and not self.proc.stdout.closed and not self._reader.is_alive():
            self.proc.stdout.close()

    def __enter__(self) -> "StdioMCP":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class LocalServers:
    """Start several local servers in parallel and call their tools, concurrently if asked."""

    def __init__(self, names: Iterable[str], env: dict | None = None, factory=None, max_workers: int = 8):
        self.names = list(dict.fromkeys(names))
        self.env = env
        self.factory = factory or (lambda name: StdioMCP(launch_command(name), name=name, env=self.env))
        self.max_workers = max_workers
        self.clients: dict[str, StdioMCP] = {}
        self.errors: dict[str, str] = {}

    def __enter__(self) -> "LocalServers":
        def start(name: str) -> None:
            try:
                self.clients[name] = self.factory(name)
            except Exception as exc:  # report, and let callers record the gap
                self.errors[name] = str(exc)

        threads = [threading.Thread(target=start, args=(n,)) for n in self.names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return self

    def __exit__(self, *exc) -> None:
        for client in self.clients.values():
            client.close()

    def call(self, server: str, tool: str, arguments: dict | None = None) -> dict:
        client = self.clients.get(server)
        if client is None:
            raise MCPError(f"{server} is not available: {self.errors.get(server, 'not started')}")
        return client.call_tool(tool, arguments)

    def call_many(self, calls: list[tuple[str, str, dict]]) -> list[dict | Exception]:
        """Run calls concurrently. Each item is a result dict or the exception that call raised."""
        def one(call: tuple[str, str, dict]) -> dict | Exception:
            try:
                return self.call(*call)
            except Exception as exc:
                return exc

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            return list(pool.map(one, calls))


def marketaux_configured() -> bool:
    """Whether the secrets file sets a non-empty MARKETAUX_API_TOKEN (the value is never read out)."""
    if os.environ.get("MARKETAUX_API_TOKEN"):
        return True
    path = Path(os.environ.get("MY_FIN_SECRETS") or Path.home() / ".config" / "my_fin" / "secrets.env")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"^\s*(export\s+)?MARKETAUX_API_TOKEN\s*=\s*['\"]?[^'\"\s#]+", text, re.M))


def health() -> dict:
    report: dict[str, Any] = {"servers": {}}
    started = time.perf_counter()
    with LocalServers(LOCAL_SERVERS) as servers:
        for name in LOCAL_SERVERS:
            client = servers.clients.get(name)
            if client is None:
                report["servers"][name] = {"ok": False, "error": servers.errors.get(name)}
                continue
            report["servers"][name] = {"ok": True, "tools": len(client.list_tools()),
                                       "server": client.server_info.get("name")}
    report["start_seconds"] = round(time.perf_counter() - started, 2)
    report["marketaux_key"] = "configured" if marketaux_configured() else (
        "not configured: news and sentiment tools return a gap (add MARKETAUX_API_TOKEN to secrets.env)")
    report["tapetide"] = "remote server with Google sign-in; checked when an agent first calls it"
    report["cache"] = "off (MY_FIN_CACHE=0)" if os.environ.get("MY_FIN_CACHE", "1").lower() in ("0", "off", "false", "no") \
        else "on (python3 mcp/cache_admin.py stats)"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call this project's local MCP servers from the shell")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("call", help="call one tool and print its text")
    p.add_argument("server", choices=LOCAL_SERVERS)
    p.add_argument("tool")
    p.add_argument("arguments", nargs="?", default="{}", help="JSON object")
    p.add_argument("--raw", action="store_true", help="print the whole result as JSON")
    p = sub.add_parser("tools", help="list a server's tools")
    p.add_argument("server", choices=LOCAL_SERVERS)
    sub.add_parser("health", help="start every local server and report")
    args = parser.parse_args(argv)

    if args.command == "health":
        report = health()
        print(json.dumps(report, indent=1))
        return 0 if all(s["ok"] for s in report["servers"].values()) else 1
    with StdioMCP(launch_command(args.server), name=args.server) as client:
        if args.command == "tools":
            for tool in client.list_tools():
                print(f"{tool['name']}: {' '.join((tool.get('description') or '').split())[:100]}")
            return 0
        result = client.call_tool(args.tool, json.loads(args.arguments))
        print(json.dumps(result, indent=1, ensure_ascii=False) if args.raw else result_text(result))
        return 1 if result.get("isError") else 0


if __name__ == "__main__":
    sys.exit(main())
