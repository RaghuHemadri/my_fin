# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.25,<2",
# ]
# ///
"""Transparent caching proxy for one local MCP server (stdio in, stdio out).

`mcp/launch.sh` starts each local server behind this proxy, so Copilot sees the same server name, tool
names, descriptions and input schemas as before. On each tool call the proxy:

1. Returns a fresh cached result when the tool is in the TTL policy (`cache_core.POLICY`) and the same call,
   with the same arguments, was answered recently by any session or script on this machine.
2. Otherwise forwards the call to the real server. Identical concurrent calls share one upstream request,
   in this process and, through a lease in the SQLite store, across processes.
3. Drops structuredContent when it only repeats the text content (FastMCP servers send every result twice,
   which doubles what the model reads). The text is unchanged; anything else is kept as an extra text block.
   Because of that, the tools are advertised without an outputSchema.
4. Never caches errors, and records every call (hit, miss, coalesced, bypass, error) in the store.

The result carries `_meta["my_fin/cache"]` with the status, the fetch time and the age in seconds.
If the real server stops, the next call returns an error result and the proxy exits, so the client sees the
server stop just as it would without the proxy.

Usage: uv run --script mcp/cache_proxy.py --name <server> -- <command that starts the real server>
Environment: MY_FIN_CACHE=0 forwards every call uncached; MY_FIN_DB sets the database path;
MY_FIN_CACHE_TTL_SCALE scales all TTLs; MY_FIN_CACHE_TIMEOUT is the upstream call timeout in seconds
(default 180); MY_FIN_CACHE_LOG sets the stderr log level (default WARNING).
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache_core  # noqa: E402
from datastore import Store  # noqa: E402

log = logging.getLogger("my_fin.cache_proxy")
_UPSTREAM_GONE = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], isError=True)


def _with_meta(result: dict, status: str, fetched_at: float) -> types.CallToolResult:
    meta = dict(result.get("_meta") or {})
    meta["my_fin/cache"] = {"status": status, "fetched_at": _iso(fetched_at), "age_s": round(time.time() - fetched_at)}
    return types.CallToolResult.model_validate({**result, "_meta": meta})


class CachingForwarder:
    def __init__(self, name: str, upstream: ClientSession, store: Store | None, timeout_s: float,
                 on_upstream_gone):
        self.name = name
        self.upstream = upstream
        self.store = store
        self.timeout_s = timeout_s
        self.on_upstream_gone = on_upstream_gone
        self.owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.session = os.environ.get("MY_FIN_RUN") or self.owner
        self.inflight: dict[str, anyio.Event] = {}

    def _log(self, tool: str, key: str | None, outcome: str, started: float, result: dict | None) -> None:
        if self.store is None:
            return
        size = len(str(result.get("content"))) if result else None
        try:
            self.store.log_call(self.name, tool, key, outcome, (time.perf_counter() - started) * 1000, size, self.session)
        except Exception as exc:  # logging must never break a tool call
            log.warning("could not log call: %s", exc)

    def _lookup(self, key: str) -> dict | None:
        try:
            return self.store.get(key) if self.store else None
        except Exception as exc:
            log.warning("cache read failed: %s", exc)
            return None

    async def _forward(self, tool: str, arguments: dict) -> dict:
        gone = _error_result(f"The {self.name} data server stopped. Start a new chat or session to restart it.")
        try:
            with anyio.fail_after(self.timeout_s):
                result = await self.upstream.call_tool(tool, arguments)
        except TimeoutError:
            return _error_result(f"{self.name}.{tool} did not answer within {self.timeout_s:.0f}s "
                                 "(cache proxy timeout).").model_dump(mode="json", by_alias=True, exclude_none=True)
        except McpError as exc:
            if exc.error.code == types.CONNECTION_CLOSED:
                self._upstream_gone()
                return gone.model_dump(mode="json", by_alias=True, exclude_none=True)
            return _error_result(exc.error.message).model_dump(mode="json", by_alias=True, exclude_none=True)
        except _UPSTREAM_GONE:
            self._upstream_gone()
            return gone.model_dump(mode="json", by_alias=True, exclude_none=True)
        return cache_core.strip_duplicate_structured(result.model_dump(mode="json", by_alias=True, exclude_none=True))

    def _upstream_gone(self) -> None:
        log.error("upstream %s is gone; stopping the proxy", self.name)
        self.on_upstream_gone()

    async def _wait_for_other_process(self, key: str, timeout_s: float = 30) -> dict | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            await anyio.sleep(0.25)
            cached = self._lookup(key)
            if cached:
                return cached
            if self.store.acquire(key, self.owner, lease_s=60):
                return None
        return None

    def _remember(self, key: str, tool: str, arguments: dict, result: dict, ttl: int) -> None:
        try:
            self.store.put(key, self.name, tool, cache_core.canonical_args(arguments), result, ttl)
            if self.name == "yahoo-finance" and tool == "get_historical_stock_prices" and ttl >= 600:
                text = next((b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"), "")
                rows = cache_core.bars_from_history(text)
                if rows:
                    self.store.upsert_bars(str(arguments.get("ticker")), str(arguments.get("interval", "1d")),
                                           rows, "yahoo-finance get_historical_stock_prices")
        except Exception as exc:
            log.warning("cache write failed: %s", exc)

    async def call(self, tool: str, arguments: dict) -> types.CallToolResult:
        started = time.perf_counter()
        ttl = cache_core.ttl_for(self.name, tool, arguments) if self.store else 0
        if ttl <= 0:
            result = await self._forward(tool, arguments)
            self._log(tool, None, "error" if cache_core.is_error_result(result) else "bypass", started, result)
            return _with_meta(result, "bypass", time.time())

        key = cache_core.cache_key(self.name, tool, arguments)
        cached = self._lookup(key)
        if cached:
            self._log(tool, key, "hit", started, cached["result"])
            return _with_meta(cached["result"], "hit", cached["fetched_at"])

        leader = self.inflight.get(key)
        if leader is not None:
            await leader.wait()
            cached = self._lookup(key)
            if cached:
                self._log(tool, key, "coalesced", started, cached["result"])
                return _with_meta(cached["result"], "coalesced", cached["fetched_at"])

        event = anyio.Event()
        self.inflight[key] = event
        try:
            if not self.store.acquire(key, self.owner, lease_s=60):
                cached = await self._wait_for_other_process(key)
                if cached:
                    self._log(tool, key, "coalesced", started, cached["result"])
                    return _with_meta(cached["result"], "coalesced", cached["fetched_at"])
            fetched_at = time.time()
            result = await self._forward(tool, arguments)
            failed = cache_core.is_error_result(result)
            if not failed:
                self._remember(key, tool, arguments, result, ttl)
            self._log(tool, key, "error" if failed else "miss", started, result)
            return _with_meta(result, "miss", fetched_at)
        finally:
            try:
                self.store.release(key, self.owner)
            except Exception as exc:
                log.warning("lease release failed: %s", exc)
            self.inflight.pop(key, None)
            event.set()


async def _all_tools(upstream: ClientSession) -> list[types.Tool]:
    tools, cursor = [], None
    while True:
        page = await upstream.list_tools(cursor=cursor) if cursor else await upstream.list_tools()
        tools.extend(page.tools)
        cursor = page.nextCursor
        if not cursor:
            return tools


def _open_store() -> Store | None:
    if not cache_core.cache_enabled():
        return None
    try:
        return Store()
    except Exception as exc:  # a broken cache must not take the data server down
        log.warning("cache disabled, store unavailable: %s", exc)
        return None


def _exit_soon(delay_s: float = 0.5) -> None:
    """Exit the process shortly, after the current error result has been written. Cancelling the server's
    tasks is not enough: the SDK reads stdin in a worker thread that blocks until the client writes again.
    Exiting makes the client see the server stop, exactly as when a directly started server crashes."""
    def stop() -> None:
        time.sleep(delay_s)
        os._exit(1)

    threading.Thread(target=stop, daemon=True).start()


async def serve(name: str, command: list[str], timeout_s: float) -> None:
    store = _open_store()
    params = StdioServerParameters(command=command[0], args=command[1:], env=dict(os.environ))
    async with stdio_client(params, errlog=sys.stderr) as (read, write), ClientSession(read, write) as upstream:
        init = await upstream.initialize()
        tools = await _all_tools(upstream)
        forwarder = CachingForwarder(name, upstream, store, timeout_s, _exit_soon)
        server = Server(name, version=init.serverInfo.version or None, instructions=init.instructions)

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [tool.model_copy(update={"outputSchema": None}) for tool in tools]

        @server.call_tool(validate_input=False)
        async def call_tool(tool: str, arguments: dict[str, Any]) -> types.CallToolResult:
            return await forwarder.call(tool, arguments or {})

        capabilities = init.capabilities
        if capabilities.prompts and (await upstream.list_prompts()).prompts:
            @server.list_prompts()
            async def list_prompts() -> list[types.Prompt]:
                return (await upstream.list_prompts()).prompts

            @server.get_prompt()
            async def get_prompt(prompt: str, arguments: dict[str, str] | None) -> types.GetPromptResult:
                return await upstream.get_prompt(prompt, arguments)

        if capabilities.resources and ((await upstream.list_resources()).resources
                                       or (await upstream.list_resource_templates()).resourceTemplates):
            @server.list_resources()
            async def list_resources() -> list[types.Resource]:
                return (await upstream.list_resources()).resources

            @server.list_resource_templates()
            async def list_resource_templates() -> list[types.ResourceTemplate]:
                return (await upstream.list_resource_templates()).resourceTemplates

            @server.read_resource()
            async def read_resource(uri) -> list[ReadResourceContents]:
                contents = (await upstream.read_resource(uri)).contents
                return [ReadResourceContents(content=c.text, mime_type=c.mimeType) if hasattr(c, "text")
                        else ReadResourceContents(content=base64.b64decode(c.blob), mime_type=c.mimeType)
                        for c in contents]

        async with stdio_server() as (client_read, client_write):
            await server.run(client_read, client_write, server.create_initialization_options())
    if store:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Caching proxy for one local MCP server")
    parser.add_argument("--name", required=True, help="server name used in the cache policy, e.g. yahoo-finance")
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("MY_FIN_CACHE_TIMEOUT") or 180))
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- <command that starts the real server>")
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("missing the upstream command after --")
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("MY_FIN_CACHE_LOG", "WARNING").upper(),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    anyio.run(serve, args.name, command, args.timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
