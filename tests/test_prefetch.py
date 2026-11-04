"""Tests for the lossless data views (data_formats.py) and the analyst data prefetch (prefetch_data.py), offline."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "skills" / "investment-debate" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import data_formats  # noqa: E402
import prefetch_data  # noqa: E402


def bars(n: int, start: str = "2025-01-01", step_days: int = 1) -> list[dict]:
    day, out, price = date.fromisoformat(start), [], 100.0
    for i in range(n):
        price *= 1.001 + (0.004 if i % 3 else -0.006)
        out.append({"Date": f"{day.isoformat()}T04:00:00.000Z", "Open": price * 0.99, "High": price * 1.01,
                    "Low": price * 0.98, "Close": round(price, 10), "Volume": 1000 + i, "Dividends": 0.0, "Stock Splits": 0.0})
        day += timedelta(days=step_days)
    return out


def text(value) -> dict:
    return {"content": [{"type": "text", "text": value if isinstance(value, str) else json.dumps(value)}], "isError": False,
            "_meta": {"my_fin/cache": {"status": "miss", "fetched_at": "2026-11-04T09:00:00+00:00"}}}


class FakeServers:
    """Answers prefetch calls with canned, realistically shaped tool output."""
    calls: list = []

    def __init__(self, names):
        self.names = names

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def call_many(self, calls):
        FakeServers.calls.extend(calls)
        return [self.answer(*c) for c in calls]

    @staticmethod
    def answer(server, tool, args):
        if tool == "get_historical_stock_prices":
            n = {"1d": 400, "1wk": 260}.get(args["interval"], 30) if args.get("period") != "1mo" else 22
            return text(bars(n, step_days=7 if args["interval"] == "1wk" else 1))
        if tool in ("financial_news", "market_sentiment"):
            return text({"count": 0, "items": [], "error": "MARKETAUX_API_TOKEN not configured"})
        if tool == "get_financial_statement":
            return text([{"date": "2026-06-30", "Total Revenue": 1.5e10, "Net Income": None},
                         {"date": "2026-03-31", "Total Revenue": 1.4e10, "Net Income": 2.0e9}])
        if tool == "get_stock_info":
            return text({"longName": "Fake Corp", "quoteType": "EQUITY", "currency": "USD", "sector": "Tech",
                         "targetMeanPrice": 120.5, "numberOfAnalystOpinions": 30, "regularMarketPrice": 100.0})
        if tool == "get_recommendations":
            return text([{"period": "0m", "strongBuy": 5, "buy": 10, "hold": 3, "sell": 1, "strongSell": 0}])
        if tool == "get_stock_actions":
            return text([{"Date": "2026-08-20T04:00:00.000Z", "Dividends": 0.83, "Stock Splits": 0.0}])
        if tool == "get_holder_info":
            return text([{"Shares": 100, "Insider": "A B", "Text": "Sale at price 1|2", "Start Date": "2026-10-01"}])
        if tool == "get_yahoo_finance_news":
            return text("Title: Something happened\nURL: https://example.com")
        if tool == "stock_prices":
            return text({"rows": [{"ticker": args["tickers"], "price": 100.0, "currency": "USD"}]})
        return text({"tool": tool, "args": args, "value": 1})


class DataFormatsTest(unittest.TestCase):
    RECORDS = [{"date": "2026-09-30", "Total Revenue": 77673000000.0, "EPS": 3.72, "Note": "", "Flag": None,
                "Odd": "123", "Bool": "true", "Pad": " x", "Pipe": "a|b", "NL": "x\ny", "Mark": "∅", "List": [1, {"a": 2}]},
               {"date": "2026-06-30", "Total Revenue": 76441000000.0, "EPS": 3.65, "Note": "ok", "Flag": True, "Odd": "abc"}]

    def test_statement_and_record_tables_round_trip(self):
        self.assertEqual(data_formats.statement_from_table(data_formats.statement_table(self.RECORDS)), self.RECORDS)
        self.assertEqual(data_formats.records_from_table(data_formats.records_table(self.RECORDS)), self.RECORDS)

    def test_statement_layout(self):
        table = data_formats.statement_table(self.RECORDS)
        self.assertTrue(table.startswith("| Line item | 2026-09-30 | 2026-06-30 |"))
        self.assertIn("| Total Revenue | 77673000000.0 | 76441000000.0 |", table)

    def test_bars_csv(self):
        records = bars(10, start="2026-10-20")
        csv_text, summary = data_formats.bars_csv(records)
        lines = csv_text.splitlines()
        self.assertEqual(lines[0], "date,open,high,low,close,volume,dividends,stock_splits")
        self.assertEqual(len(lines), 11)
        self.assertEqual(lines[1].split(",")[0], "2026-10-20")
        self.assertEqual(float(lines[-1].split(",")[4]), records[-1]["Close"])
        _, cut = data_formats.bars_csv(records, as_of="2026-10-25", start="2026-10-22")
        self.assertEqual((cut["first"], cut["last"], cut["bars"], cut["dropped_after_as_of"]), ("2026-10-22", "2026-10-25", 4, 6))
        self.assertEqual(summary["last_close"], records[-1]["Close"])

    def test_render_kinds(self):
        self.assertEqual(data_formats.render(json.dumps(self.RECORDS), "get_financial_statement")[0], "statement")
        self.assertEqual(data_formats.render(json.dumps(self.RECORDS), "get_recommendations")[0], "table")
        self.assertEqual(data_formats.render(json.dumps({"a": 1}))[0], "json")
        self.assertEqual(data_formats.render("Title: x"), ("text", "Title: x"))


class PrefetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        FakeServers.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_dir(self, **settings) -> Path:
        run = Path(self.tmp.name) / "run"
        run.mkdir(exist_ok=True)
        base = {"symbol": "NASDAQ:FAKE", "yahoo_ticker": "FAKE", "mode": "live", "as_of": "2026-11-04",
                "asset_type": "stock", "analysts": ["market", "sentiment", "news", "fundamentals"],
                "holding_days": 20, "benchmark": "^GSPC"}
        (run / "0_settings.json").write_text(json.dumps({**base, **settings}))
        return run

    def test_live_bundles(self):
        run = self.run_dir()
        summary = prefetch_data.prefetch(run, today="2026-11-04", servers_factory=FakeServers)
        self.assertEqual(summary["files"], ["0_data/market.md", "0_data/sentiment.md", "0_data/news.md", "0_data/fundamentals.md"])
        keys = [(c[1], json.dumps(c[2], sort_keys=True)) for c in FakeServers.calls]
        self.assertEqual(len(keys), len(set(keys)), "each call is made once for the whole team")
        raw = json.loads((run / "1_analysts" / "bars_1D.json").read_text())
        self.assertEqual(len(raw), 400)  # saved exactly as returned
        self.assertEqual(len((run / "0_data" / "bars_1D.csv").read_text().splitlines()), 401)
        market = (run / "0_data" / "market.md").read_text()
        headings = [line for line in market.splitlines() if line.startswith("## ")]
        self.assertTrue(headings[0].startswith("## Step 1") and headings[-1].startswith("## Step 4"))
        self.assertIn('"close_50_sma"', market)  # indicators.py ran on the saved bars
        sentiment = (run / "0_data" / "sentiment.md").read_text()
        self.assertIn('"targetMeanPrice": 120.5', sentiment)
        self.assertIn("configuration gap", sentiment)
        fundamentals = (run / "0_data" / "fundamentals.md").read_text()
        self.assertIn("| Total Revenue | 15000000000.0 | 14000000000.0 |", fundamentals)
        self.assertIn("Sale at price 1\\|2", fundamentals)
        self.assertTrue((run / "0_data" / "stock_info.json").exists())
        manifest = json.loads((run / "0_data" / "manifest.json").read_text())
        self.assertEqual({c["status"] for c in manifest["calls"]}, {"ok", "error"})

        again = prefetch_data.prefetch(run, today="2026-11-04", servers_factory=FakeServers)
        self.assertEqual(again["roles"], [])  # bundles exist: nothing to do

    def test_historical_follows_point_in_time_rules(self):
        run = self.run_dir(mode="historical", as_of="2025-06-30")
        prefetch_data.prefetch(run, today="2026-11-04", servers_factory=FakeServers)
        tools = {c[1] for c in FakeServers.calls}
        self.assertFalse(tools & {"technical_snapshot", "price_statistics", "stock_prices", "get_recommendations",
                                   "get_holder_info"})
        self.assertFalse((run / "0_data" / "stock_info.json").exists())
        fundamentals = (run / "0_data" / "fundamentals.md").read_text()
        self.assertNotIn("targetMeanPrice", fundamentals)
        self.assertIn("published on or before 2025-06-30", fundamentals)
        csv_rows = (run / "0_data" / "bars_1D.csv").read_text().splitlines()[1:]
        self.assertTrue(all(row[:10] <= "2025-06-30" for row in csv_rows))
        self.assertIn("Do not use them", (run / "0_data" / "market.md").read_text())

    def test_futures_and_existing_reports(self):
        run = self.run_dir(symbol="COMEX:GC1!", yahoo_ticker="GC=F", asset_type="futures",
                           analysts=["market", "sentiment", "news"])
        (run / "1_analysts").mkdir()
        (run / "1_analysts" / "news.md").write_text("done")
        summary = prefetch_data.prefetch(run, today="2026-11-04", servers_factory=FakeServers)
        self.assertEqual(summary["roles"], ["market", "sentiment"])
        self.assertEqual(summary["calls"]["skipped"], 2)
        self.assertIn("**SKIPPED**: technicals technical_snapshot covers NSE, BSE and US listings only",
                      (run / "0_data" / "market.md").read_text())
        self.assertNotIn("get_financial_statement", {c[1] for c in FakeServers.calls})


if __name__ == "__main__":
    unittest.main()
