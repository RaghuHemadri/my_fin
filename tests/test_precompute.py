"""Tests for nightly precompute (tools/precompute.py), offline."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / ".github" / "skills" / "investment-debate" / "scripts"))

import precompute  # noqa: E402


class PrecomputeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.holdings = self.dir / "holdings.csv"
        self.holdings.write_text("symbol,quantity,avg_cost,currency\nNASDAQ:MSFT,10,350,USD\nNSE:TCS,12,3500,INR\n")
        self.watchlist = self.dir / "watchlist.csv"
        self.watchlist.write_text("symbol,holding_days,note\nnse:tcs,60,dup\nNSE:INFY,60,x\nNASDAQ:AAPL,,y\nNASDAQ:NVDA,,z\n")
        self.reports = self.dir / "reports"
        done = self.reports / "NASDAQ_MSFT" / "2026-11-04-2"
        done.mkdir(parents=True)
        (done / "complete_report.md").write_text("report")

    def tearDown(self):
        self.tmp.cleanup()

    def test_selection(self):
        to_run, skipped = precompute.select(self.holdings, self.watchlist, self.reports, "2026-11-04", 20, max_runs=2)
        self.assertEqual([(i["symbol"], i["holding_days"]) for i in to_run], [("NSE:TCS", 20), ("NSE:INFY", 60)])
        reasons = {i["symbol"]: i["reason"] for i in skipped}
        self.assertIn("already exists", reasons["NASDAQ:MSFT"])
        self.assertIn("--max-runs", reasons["NASDAQ:AAPL"])
        self.assertNotIn("nse:tcs", reasons)  # duplicates keep the first entry (the holding)
        only_watch, _ = precompute.select(self.holdings, self.watchlist, self.reports, "2026-11-04", 20, 10, False)
        self.assertEqual([i["symbol"] for i in only_watch], ["NSE:TCS", "NSE:INFY", "NASDAQ:AAPL", "NASDAQ:NVDA"])

    def test_groups_by_holding_period_and_passes_options(self):
        calls = []
        fake = mock.Mock(main=lambda argv: calls.append(argv) or 0)
        with mock.patch.dict(sys.modules, {"run_debate": fake}), redirect_stdout(io.StringIO()) as out:
            code = precompute.main(["--holdings", str(self.holdings), "--watchlist", str(self.watchlist),
                                    "--reports-dir", str(self.reports), "--today", "2026-11-04", "--max-runs", "3",
                                    "--", "--quick-effort", "high"])
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][:3], ["NSE:TCS", "NASDAQ:AAPL", "--holding-days"])
        self.assertEqual(calls[0][3], "20")
        self.assertEqual(calls[1][:3], ["NSE:INFY", "--holding-days", "60"])
        self.assertEqual(calls[0][-2:], ["--quick-effort", "high"])
        summary = json.loads((self.reports / "precompute" / "2026-11-04.json").read_text())
        self.assertEqual(len(summary["runs"]), 2)
        self.assertIn('"to_run"', out.getvalue())

    def test_dry_run_runs_nothing(self):
        with mock.patch.dict(sys.modules, {"run_debate": None}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(precompute.main(["--holdings", str(self.holdings), "--watchlist", str(self.watchlist),
                                              "--reports-dir", str(self.reports), "--today", "2026-11-04", "--dry-run"]), 0)
        self.assertEqual(len(json.loads(out.getvalue())["to_run"]), 4)
        self.assertFalse((self.reports / "precompute").exists())


if __name__ == "__main__":
    unittest.main()
