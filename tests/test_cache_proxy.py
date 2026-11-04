"""Opt-in integration test for the caching proxy (mcp/cache_proxy.py) against tests/fixtures/fake_mcp_server.py.

It needs uv, which resolves the MCP SDK the proxy runs on. Run it with:
    MY_FIN_INTEGRATION=1 python3 -B -m unittest tests.test_cache_proxy -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fixtures" / "fake_mcp_server.py"
sys.path.insert(0, str(ROOT / "mcp"))

import stdio_client  # noqa: E402
from datastore import Store  # noqa: E402
from stdio_client import StdioMCP  # noqa: E402


@unittest.skipUnless(os.environ.get("MY_FIN_INTEGRATION") == "1" and shutil.which("uv"),
                     "set MY_FIN_INTEGRATION=1 (needs uv) to run the caching proxy against the fake server")
class CachingProxyIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.counts = Path(self.tmp.name) / "counts.json"
        self.db = Path(self.tmp.name) / "cache.sqlite3"
        env = {**os.environ, "MY_FIN_DB": str(self.db), "FAKE_MCP_COUNTS": str(self.counts), "MY_FIN_CACHE": "1"}
        command = ["uv", "run", "--quiet", "--script", str(ROOT / "mcp" / "cache_proxy.py"),
                   "--name", "yahoo-finance", "--", sys.executable, str(FAKE)]
        self.client = StdioMCP(command, name="proxy", env=env, call_timeout=60)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def upstream_calls(self, tool: str) -> int:
        return json.loads(self.counts.read_text()).get(tool, 0) if self.counts.exists() else 0

    def test_proxy(self):
        tools = {t["name"]: t for t in self.client.list_tools()}
        self.assertNotIn("outputSchema", tools["get_stock_info"])
        self.assertEqual(tools["get_stock_info"]["inputSchema"]["required"], ["ticker"])

        first = self.client.call_tool("get_stock_info", {"ticker": "MSFT"})
        second = self.client.call_tool("get_stock_info", {"ticker": "MSFT"})
        self.assertNotIn("structuredContent", first)
        self.assertEqual(len(first["content"]), 1)
        self.assertEqual(first["content"], second["content"])
        self.assertEqual([stdio_client.cache_status(r) for r in (first, second)], ["miss", "hit"])
        self.assertEqual(self.upstream_calls("get_stock_info"), 1)

        for _ in range(2):  # an error answer from a cacheable tool is forwarded every time
            self.client.call_tool("get_stock_info", {"ticker": "BAD"})
        self.assertEqual(self.upstream_calls("get_stock_info"), 3)
        for _ in range(2):  # a tool outside the policy is never cached
            self.assertEqual(stdio_client.cache_status(self.client.call_tool("failing_tool")), "bypass")
        self.assertEqual(self.upstream_calls("failing_tool"), 2)

        results = []
        args = {"ticker": "AAPL", "period": "1mo", "interval": "1d"}
        threads = [threading.Thread(target=lambda: results.append(self.client.call_tool("get_historical_stock_prices", args)))
                   for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.upstream_calls("get_historical_stock_prices"), 1)
        self.assertEqual(sorted(stdio_client.cache_status(r) for r in results), ["coalesced", "coalesced", "miss"])
        with Store(self.db) as store:
            self.assertEqual(len(store.get_bars("AAPL", "1d")), 5)


if __name__ == "__main__":
    unittest.main()
