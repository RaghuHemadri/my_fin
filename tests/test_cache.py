"""Tests for the MCP cache helpers and the SQLite store (mcp/cache_core.py, mcp/datastore.py)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))

import cache_core  # noqa: E402
from datastore import Store  # noqa: E402


def text_result(text: str, **extra) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False, **extra}


class PolicyTest(unittest.TestCase):
    def test_listed_tools_are_cached_and_others_are_not(self):
        with mock.patch.dict(os.environ, {"MY_FIN_CACHE": "1"}, clear=False):
            os.environ.pop("MY_FIN_CACHE_TTL_SCALE", None)
            self.assertEqual(cache_core.ttl_for("yahoo-finance", "get_financial_statement", {}), 12 * 3600)
            self.assertEqual(cache_core.ttl_for("tradingmcp", "stock_prices", {"tickers": "NSE:TCS"}), 120)
            self.assertEqual(cache_core.ttl_for("tradingmcp", "futures_top_movers", {}), 300)
            self.assertEqual(cache_core.ttl_for("tradingmcp", "unknown_tool", {}), 0)
            self.assertEqual(cache_core.ttl_for("tapetide", "search_stocks", {}), 0)

    def test_intraday_bars_expire_sooner(self):
        daily = cache_core.ttl_for("yahoo-finance", "get_historical_stock_prices", {"interval": "1d"})
        intraday = cache_core.ttl_for("yahoo-finance", "get_historical_stock_prices", {"interval": "5m"})
        self.assertGreater(daily, intraday)
        self.assertGreater(cache_core.ttl_for("technicals", "technical_snapshot", {"interval": "1d"}),
                           cache_core.ttl_for("technicals", "technical_snapshot", {"interval": "15m"}))

    def test_environment_switches(self):
        with mock.patch.dict(os.environ, {"MY_FIN_CACHE": "0"}):
            self.assertEqual(cache_core.ttl_for("yahoo-finance", "get_stock_info", {}), 0)
        with mock.patch.dict(os.environ, {"MY_FIN_CACHE": "1", "MY_FIN_CACHE_TTL_SCALE": "0.5"}):
            self.assertEqual(cache_core.ttl_for("yahoo-finance", "get_stock_info", {}), 15 * 60)

    def test_cache_key_ignores_argument_order(self):
        a = cache_core.cache_key("yahoo-finance", "get_financial_statement", {"ticker": "TCS.NS", "financial_type": "cashflow"})
        b = cache_core.cache_key("yahoo-finance", "get_financial_statement", {"financial_type": "cashflow", "ticker": "TCS.NS"})
        c = cache_core.cache_key("yahoo-finance", "get_financial_statement", {"financial_type": "balance_sheet", "ticker": "TCS.NS"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, cache_core.cache_key("technicals", "get_financial_statement", {"ticker": "TCS.NS", "financial_type": "cashflow"}))


class CompactionTest(unittest.TestCase):
    def test_fastmcp_duplicate_is_dropped(self):
        text = json.dumps([{"date": "2026-09-30", "Total Revenue": 1.0}])
        result = text_result(text, structuredContent={"result": text})
        out = cache_core.strip_duplicate_structured(result)
        self.assertNotIn("structuredContent", out)
        self.assertEqual(out["content"], result["content"])

    def test_parsed_duplicate_is_dropped(self):
        data = {"rows": [1, 2], "symbol": "MSFT"}
        out = cache_core.strip_duplicate_structured(text_result(json.dumps(data, indent=2), structuredContent=data))
        self.assertEqual(len(out["content"]), 1)

    def test_extra_structured_data_is_kept_as_text(self):
        result = text_result("summary only", structuredContent={"rows": [1, 2, 3]})
        out = cache_core.strip_duplicate_structured(result)
        self.assertNotIn("structuredContent", out)
        self.assertEqual(json.loads(out["content"][1]["text"]), {"rows": [1, 2, 3]})

    def test_result_without_structured_content_is_unchanged(self):
        result = text_result("plain")
        self.assertIs(cache_core.strip_duplicate_structured(result), result)


class ErrorDetectionTest(unittest.TestCase):
    def test_errors_are_detected(self):
        self.assertTrue(cache_core.is_error_result({"content": [], "isError": True}))
        self.assertTrue(cache_core.is_error_result(text_result(
            json.dumps({"count": 0, "items": [], "error": "MARKETAUX_API_TOKEN not configured"}))))
        self.assertTrue(cache_core.is_error_result(text_result("Error: getting stock information for XYZ")))
        self.assertTrue(cache_core.is_error_result(text_result("429 Too Many Requests. Rate limited")))

    def test_ordinary_results_are_not_errors(self):
        self.assertFalse(cache_core.is_error_result(text_result("No financial statement data found for ticker X.NS.")))
        self.assertFalse(cache_core.is_error_result(text_result(json.dumps({"rows": [], "error": None}))))
        self.assertFalse(cache_core.is_error_result(text_result(json.dumps([{"Date": "2026-10-01", "Close": 1}]))))


class BarsTest(unittest.TestCase):
    def test_session_day_shifts_yahoo_midnight_stamps(self):
        self.assertEqual(cache_core.session_day("2026-10-22T18:30:00.000Z"), "2026-10-23")  # NSE session
        self.assertEqual(cache_core.session_day("2026-10-28T04:00:00.000Z"), "2026-10-28")  # US session
        self.assertEqual(cache_core.session_day("2026-10-28"), "2026-10-28")
        self.assertEqual(cache_core.session_day(1793404800), "2026-10-31")

    def test_bars_from_history(self):
        text = json.dumps([{"Date": "2026-10-28T04:00:00.000Z", "Open": 1, "High": 2, "Low": 0.5, "Close": 1.5,
                            "Volume": 10, "Dividends": 0.0, "Stock Splits": 0.0},
                           {"Date": "2026-10-29T04:00:00.000Z", "Close": None}])
        self.assertEqual(cache_core.bars_from_history(text),
                         [{"date": "2026-10-28", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}])
        self.assertEqual(cache_core.bars_from_history("No data found"), [])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "cache.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_put_get_and_expiry(self):
        self.store.put("k", "yahoo-finance", "get_stock_info", "{}", text_result("x"), ttl_s=60, now=1000)
        self.assertEqual(self.store.get("k", now=1030)["result"], text_result("x"))
        self.assertEqual(self.store.get("k", now=1030)["fetched_at"], 1000)
        self.assertIsNone(self.store.get("k", now=1061))
        self.assertEqual(self.store.prune(now=1061)["expired_responses"], 1)

    def test_lease_blocks_other_owner_until_released_or_stale(self):
        self.assertTrue(self.store.acquire("k", "a", lease_s=60, now=1000))
        self.assertFalse(self.store.acquire("k", "b", lease_s=60, now=1010))
        self.assertTrue(self.store.acquire("k", "a", lease_s=60, now=1010))
        self.assertTrue(self.store.acquire("k", "b", lease_s=60, now=1100))  # stale lease is taken over
        self.store.release("k", "b")
        self.assertTrue(self.store.acquire("k", "c", lease_s=60, now=1101))

    def test_call_log_stats_and_clear(self):
        for outcome in ("miss", "hit", "hit"):
            self.store.log_call("technicals", "price_statistics", "k", outcome, 5.0, 10, now=2000)
        stats = {r["outcome"]: r["calls"] for r in self.store.stats(since=1999)}
        self.assertEqual(stats, {"hit": 2, "miss": 1})
        self.store.put("a", "technicals", "price_statistics", "{}", text_result("1"), 60)
        self.store.put("b", "yahoo-finance", "get_stock_info", "{}", text_result("2"), 60)
        self.assertEqual(self.store.clear(server="technicals"), 1)
        self.assertIsNotNone(self.store.get("b"))

    def test_bars_runs_and_stages(self):
        rows = [{"date": "2026-10-28", "close": 1.5}, {"date": "2026-10-29", "close": 1.6}]
        self.assertEqual(self.store.upsert_bars("MSFT", "1d", rows, "yahoo-finance"), 2)
        self.store.upsert_bars("MSFT", "1d", [{"date": "2026-10-29", "close": 1.7}], "yahoo-finance")
        bars = self.store.get_bars("MSFT", "1d", start="2026-10-29")
        self.assertEqual([(b["date"], b["close"]) for b in bars], [("2026-10-29", 1.7)])

        self.store.record_run("r1", symbol="NSE:TCS", as_of="2026-11-04", run_dir="reports/NSE_TCS/2026-11-04",
                              settings={"depth": "shallow"}, status="running", started_at=1.0)
        self.store.record_run("r1", status="done", rating="Hold", finished_at=2.0)
        run = self.store.runs("NSE:TCS")[0]
        self.assertEqual((run["status"], run["rating"], json.loads(run["settings"])), ("done", "Hold", {"depth": "shallow"}))
        self.store.record_stage("r1", "1_analysts/market", role="ta-market-analyst", status="done", started_at=1.0)
        self.store.record_stage("r1", "1_analysts/market", attempts=2)
        self.assertEqual(self.store.stages("r1")[0]["attempts"], 2)
        with self.assertRaises(ValueError):
            self.store.record_run("r1", bogus=1)


if __name__ == "__main__":
    unittest.main()
