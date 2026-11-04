"""Tests for the scripts' MCP client (mcp/stdio_client.py), against tests/fixtures/fake_mcp_server.py.

Standard library only; no network and no MCP SDK needed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "tests" / "fixtures" / "fake_mcp_server.py"
sys.path.insert(0, str(ROOT / "mcp"))

import stdio_client  # noqa: E402
from stdio_client import LocalServers, MCPError, StdioMCP, result_text  # noqa: E402


def fake_client(name: str = "fake", env: dict | None = None) -> StdioMCP:
    return StdioMCP([sys.executable, str(FAKE)], name=name, env={**os.environ, **(env or {})}, call_timeout=20)


class StdioClientTest(unittest.TestCase):
    def test_initialize_list_and_call(self):
        with fake_client() as client:
            self.assertEqual(client.server_info["name"], "fake")
            self.assertEqual({t["name"] for t in client.list_tools()},
                             {"get_stock_info", "get_historical_stock_prices", "failing_tool", "slow_tool"})
            result = client.call_tool("get_stock_info", {"ticker": "MSFT"})
            self.assertEqual(json.loads(result_text(result))["symbol"], "MSFT")

    def test_concurrent_calls_overlap(self):
        with fake_client() as client:
            started = time.perf_counter()
            threads = [threading.Thread(target=client.call_tool, args=("slow_tool", {"seconds": 0.6})) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertLess(time.perf_counter() - started, 1.8)

    def test_errors(self):
        with fake_client() as client:
            with self.assertRaises(MCPError):
                client.request("no/such/method")
            client.proc.kill()
            client.proc.wait()
            time.sleep(0.2)
            with self.assertRaises(MCPError):
                client.call_tool("get_stock_info", {"ticker": "MSFT"})

    def test_local_servers_call_many_and_missing_server(self):
        with LocalServers(["a", "b"], factory=lambda name: fake_client(name)) as servers:
            results = servers.call_many([("a", "get_stock_info", {"ticker": "X"}), ("b", "slow_tool", {"seconds": 0.1}),
                                         ("c", "get_stock_info", {})])
            self.assertEqual(json.loads(result_text(results[0]))["symbol"], "X")
            self.assertEqual(result_text(results[1]), "done")
            self.assertIsInstance(results[2], MCPError)

    def test_tapetide_is_not_a_local_server(self):
        with self.assertRaises(ValueError):
            stdio_client.launch_command("tapetide")

    def test_marketaux_detection_never_needs_the_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            secrets = Path(tmp) / "secrets.env"
            env = {k: v for k, v in os.environ.items() if k != "MARKETAUX_API_TOKEN"}
            with mock.patch.dict(os.environ, {**env, "MY_FIN_SECRETS": str(secrets)}, clear=True):
                self.assertFalse(stdio_client.marketaux_configured())
                secrets.write_text("MARKETAUX_API_TOKEN=\n")
                self.assertFalse(stdio_client.marketaux_configured())
                secrets.write_text("# key\nMARKETAUX_API_TOKEN=abc123\n")
                self.assertTrue(stdio_client.marketaux_configured())


if __name__ == "__main__":
    unittest.main()
