"""Tests for the run graph (run_plan.py) and deterministic run setup (prepare_run.py), offline."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "skills" / "investment-debate" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import prepare_run  # noqa: E402
import run_plan  # noqa: E402

INFO = {
    "TCS.NS": {"longName": "Tata Consultancy Services Limited", "quoteType": "EQUITY", "exchange": "NSI",
               "fullExchangeName": "NSE", "currency": "INR", "sector": "Technology", "industry": "IT Services"},
    "MSFT": {"longName": "Microsoft Corporation", "quoteType": "EQUITY", "exchange": "NMS", "currency": "USD",
             "sector": "Technology", "industry": "Software"},
    "GC=F": {"shortName": "Gold Dec 26", "quoteType": "FUTURE", "exchange": "CMX", "currency": "USD"},
}
QUOTES = {"NSE:TCS": {"ticker": "NSE:TCS", "description": "Tata Consultancy Services", "price": 2400.0, "currency": "INR"},
          "NASDAQ:MSFT": {"ticker": "NASDAQ:MSFT", "description": "Microsoft", "price": 500.0, "currency": "USD"}}


class FakeFetcher:
    """Stands in for the MCP servers: the four lookups prepare_run makes."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def stock_info(self, ticker):
        return INFO.get(ticker), f"fake get_stock_info({ticker})"

    def quotes(self, symbols):
        return {s: QUOTES[s] for s in symbols if s in QUOTES}, "fake stock_prices"

    def fx_to_inr(self, currency):
        return {"USD": 95.0}.get(currency), f"fake fx {currency}"

    def close_on(self, ticker, as_of, today):
        return {"MSFT": ("2026-10-01", 480.0), "TCS.NS": ("2026-10-01", 2300.0), "INR=X": ("2026-10-01", 94.0)}.get(ticker), \
            f"fake close {ticker}"


class RunPlanTest(unittest.TestCase):
    def test_shallow_order(self):
        ids = [s.id for s in run_plan.plan({"analysts": ["market", "news"], "depth": "shallow"})]
        self.assertEqual(ids, ["1_analysts/market", "1_analysts/news", "2_research/01_bull", "2_research/02_bear",
                               "2_research/manager", "3_trading/trader", "4_risk/01_aggressive",
                               "4_risk/02_conservative", "4_risk/03_neutral", "5_portfolio/decision", "finalize"])

    def test_depth_and_isolation(self):
        stages = run_plan.plan({"analysts": list(run_plan.ANALYSTS), "depth": "medium",
                                "debate_rounds": 3, "risk_rounds": 3})
        self.assertEqual(sum(s.kind == "debate" for s in stages), 6)
        self.assertEqual(sum(s.kind == "risk" for s in stages), 9)
        by_id = {s.id: s for s in stages}
        for stage in stages:
            if stage.kind in ("analyst", "debate", "manager"):
                self.assertNotIn("0_portfolio.md", stage.inputs, stage.id)  # researchers never see holdings
            if stage.kind != "portfolio":
                self.assertNotIn("0_past_context.md", stage.inputs, stage.id)  # only the PM sees past lessons
        self.assertEqual(by_id["2_research/03_bull"].inputs[-2:], ["2_research/01_bull.md", "2_research/02_bear.md"])
        self.assertEqual(by_id["4_risk/04_aggressive"].inputs[-3:],
                         ["4_risk/01_aggressive.md", "4_risk/02_conservative.md", "4_risk/03_neutral.md"])
        self.assertEqual({s.agent for s in stages if s.tier == "deep"}, run_plan.JUDGES)

    def test_resume_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.assertEqual(run_plan.resume_point(run), "setup")
            settings = {"analysts": ["market"], "depth": "shallow"}
            (run / "0_settings.json").write_text(json.dumps(settings))
            self.assertEqual(run_plan.resume_point(run), "1_analysts/market")
            (run / "1_analysts").mkdir()
            (run / "1_analysts" / "market.md").write_text("report")
            self.assertEqual(run_plan.resume_point(run), "2_research/01_bull")

    def test_effort_tiers(self):
        self.assertEqual(run_plan.effort_tiers("full", None, None), (None, None))
        self.assertEqual(run_plan.effort_tiers("full", "high", None), ("high", None))
        self.assertEqual(run_plan.effort_tiers("fast", None, None), ("low", "medium"))
        self.assertEqual(run_plan.effort_tiers("fast", None, "high"), ("low", "high"))


class PrepareRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.holdings = self.dir / "holdings.csv"
        self.holdings.write_text("symbol,quantity,avg_cost,currency\nNASDAQ:MSFT,10,350,USD\nNSE:TCS,12,3500,INR\n")
        self.cash = self.dir / "cash.csv"
        self.cash.write_text("currency,amount\nINR,50000\nUSD,1000\n")
        self.env = mock.patch.dict(os.environ, {"MY_FIN_SECRETS": str(self.dir / "none.env")})
        self.env.start()
        os.environ.pop("MARKETAUX_API_TOKEN", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def prepare(self, *extra: str) -> dict:
        args = prepare_run.parser().parse_args(
            ["--reports-dir", str(self.dir / "reports"), "--memory-log", str(self.dir / "log.md"),
             "--holdings", str(self.holdings), "--cash", str(self.cash), "--today", "2026-11-04", *extra])
        return prepare_run.prepare(args, fetcher_factory=FakeFetcher)

    def test_live_run_files(self):
        out = self.prepare("--symbol", "NSE:TCS", "--holding-days", "210", "--mandate", "8-12 month view.")
        run = Path(out["run"])
        for folder in run_plan.STAGE_DIRS:  # the role agents cannot create folders, so setup does
            self.assertTrue((run / folder).is_dir(), folder)
        self.assertEqual((out["mode"], out["asset_type"], out["resume_from"]), ("live", "stock", "1_analysts/market"))
        self.assertEqual(out["written"], ["0_settings.json", "0_instrument.md", "0_portfolio.md", "0_past_context.md"])
        settings = json.loads((run / "0_settings.json").read_text())
        self.assertEqual((settings["benchmark"], settings["yahoo_ticker"], settings["tapetide_symbol"]),
                         ("^NSEI", "TCS.NS", "TCS"))
        self.assertIn("Marketaux key not configured", " ".join(settings["data_gaps"]))
        instrument = (run / "0_instrument.md").read_text()
        for line in ("- Symbol: NSE:TCS", "- Company: Tata Consultancy Services Limited", "- Mode: live",
                     "- Holding period: 210 trading days", "## Research mandate", "8-12 month view."):
            self.assertIn(line, instrument)
        portfolio = (run / "0_portfolio.md").read_text()
        self.assertIn("Current position in NSE:TCS: 12 at an average cost of INR 3,500.00", portfolio)
        self.assertIn("| NASDAQ:MSFT | 10 | USD 350.00 | USD 500.00 | USD 5,000.00 | INR 475,000.00 | USD 5,000.00 | 94.3% | +42.9% |",
                      portfolio)
        self.assertIn("Cash: INR 50,000.00; USD 1,000.00 (INR 95,000.00)", portfolio)
        self.assertEqual((run / "0_past_context.md").read_text().strip(), "No resolved past decisions yet.")

        again = self.prepare("--symbol", "NSE:TCS")
        self.assertEqual(again["written"], ["0_past_context.md"])
        self.assertIn("0_portfolio.md", again["kept"])

    def test_fast_profile(self):
        run = Path(self.prepare("--symbol", "NSE:TCS", "--profile", "fast")["run"])
        settings = json.loads((run / "0_settings.json").read_text())
        self.assertEqual((settings["profile"], settings["quick_effort"], settings["deep_effort"]), ("fast", "low", "medium"))
        self.assertIn("- Profile: fast.", (run / "0_instrument.md").read_text())

        run = Path(self.prepare("--symbol", "NASDAQ:MSFT", "--profile", "fast", "--quick-effort", "high")["run"])
        settings = json.loads((run / "0_settings.json").read_text())
        self.assertEqual((settings["quick_effort"], settings["deep_effort"]), ("high", "medium"))

        run = Path(self.prepare("--symbol", "NSE:TCS", "--fresh")["run"])
        settings = json.loads((run / "0_settings.json").read_text())
        self.assertEqual((settings["profile"], settings["quick_effort"], settings["deep_effort"]),
                         ("full", "session effort", "session effort"))
        self.assertNotIn("Profile:", (run / "0_instrument.md").read_text())

    def test_main_starts_the_servers_once(self):
        import prefetch_data
        import stdio_client
        entered, seen = [], {}

        class FakeServers:
            def __init__(self, names):
                self.names = names

            def __enter__(self):
                entered.append(sorted(self.names))
                return self

            def __exit__(self, *exc):
                return None

        def fake_prepare(args, fetcher_factory=None):
            with fetcher_factory() as servers:
                seen["prepare"] = servers
            return {"run": str(self.dir / "run")}

        def fake_prefetch(run, today=None, servers_factory=None):
            with servers_factory(["technicals", "yahoo-finance"]) as servers:
                seen["prefetch"] = servers
            return {"roles": []}

        with mock.patch.object(stdio_client, "LocalServers", FakeServers), \
                mock.patch.object(prepare_run, "prepare", fake_prepare), \
                mock.patch.object(prefetch_data, "prefetch", fake_prefetch), redirect_stdout(io.StringIO()):
            self.assertEqual(prepare_run.main(["--symbol", "NSE:TCS"]), 0)
        self.assertEqual(entered, [["technicals", "tradingmcp", "yahoo-finance"]])
        self.assertIs(seen["prepare"], seen["prefetch"])

    def test_futures_drop_fundamentals_and_position_override(self):
        out = self.prepare("--symbol", "COMEX:GC1!", "--yahoo-ticker", "GC=F", "--benchmark", "^GSPC",
                           "--position", "2@2000")
        run = Path(out["run"])
        self.assertEqual(out["analysts"], ["market", "sentiment", "news"])
        self.assertIn("not available, not an empty finding", (run / "1_analysts" / "fundamentals.md").read_text())
        self.assertIn("(stated by the user in chat", (run / "0_portfolio.md").read_text())

    def test_historical_uses_closes(self):
        out = self.prepare("--symbol", "NASDAQ:MSFT", "--as-of", "2026-10-01")
        portfolio = (Path(out["run"]) / "0_portfolio.md").read_text()
        self.assertEqual(out["mode"], "historical")
        self.assertIn("used as a standing book", portfolio)
        self.assertIn("USD 480.00", portfolio)
        self.assertIn("Mode: historical as of 2026-10-01", (Path(out["run"]) / "0_instrument.md").read_text())

    def test_no_holdings_and_fresh_folders(self):
        self.holdings.write_text("symbol,quantity,avg_cost,currency\n")
        first = self.prepare("--symbol", "NASDAQ:MSFT")
        self.assertIn("Portfolio context: not provided", (Path(first["run"]) / "0_portfolio.md").read_text())
        second = self.prepare("--symbol", "NASDAQ:MSFT", "--fresh")
        self.assertTrue(second["run"].endswith("2026-11-04-2"))

    def test_rejections(self):
        for argv, error in [(["--symbol", "TCS"], ValueError), (["--symbol", "NSE:TCS", "--as-of", "2026-12-01"], ValueError),
                            (["--symbol", "XETR:SAP"], ValueError), (["--symbol", "NASDAQ:NOPE"], LookupError)]:
            with self.subTest(argv=argv), self.assertRaises(error):
                self.prepare(*argv)

    def test_period_covering(self):
        self.assertEqual(prepare_run.period_covering("2026-10-30", "2026-11-04"), "1mo")
        self.assertEqual(prepare_run.period_covering("2026-01-01", "2026-11-04"), "1y")
        self.assertEqual(prepare_run.period_covering("2010-01-01", "2026-11-04"), "max")


if __name__ == "__main__":
    unittest.main()
