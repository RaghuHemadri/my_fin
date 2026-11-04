"""Tests for follow-up variants that reuse a finished run's stages (variant_run.py), offline."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "skills" / "investment-debate" / "scripts"))

import run_plan  # noqa: E402
import variant_run  # noqa: E402

SETTINGS = {"symbol": "NASDAQ:IREN", "as_of": "2026-11-04", "mode": "live", "asset_type": "stock",
            "analysts": ["market", "sentiment", "news", "fundamentals"], "depth": "shallow", "debate_rounds": 1,
            "risk_rounds": 1, "language": "English", "holding_days": 210, "benchmark": "^GSPC", "yahoo_ticker": "IREN"}


def finished_run(base: Path) -> Path:
    run = base / "reports" / "NASDAQ_IREN" / "2026-11-04"
    for stage in run_plan.plan(SETTINGS):
        (run / stage.output).parent.mkdir(parents=True, exist_ok=True)
        (run / stage.output).write_text(f"# {stage.id}\n")
    (run / "0_settings.json").write_text(json.dumps(SETTINGS))
    (run / "0_instrument.md").write_text("# Instrument\n\n- Symbol: NASDAQ:IREN\n- Output language: English\n"
                                         "- Holding period: 210 trading days (use it as horizon_days in technicals price_statistics)\n\n"
                                         "## Research mandate\n\nHow will IREN do in 8-12 months?\n")
    for name in ("0_portfolio.md", "0_past_context.md", "0_data/market.md", "0_data/news.md", "1_analysts/bars_1D.json"):
        (run / name).parent.mkdir(parents=True, exist_ok=True)
        (run / name).write_text("x\n")
    return run


class VariantRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = finished_run(Path(self.tmp.name))
        self.setup_calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def setup(self, run, settings, position):
        self.setup_calls.append(position)
        (run / "0_portfolio.md").write_text(f"position {position}\n")
        (run / "0_past_context.md").write_text("No resolved past decisions yet.\n")
        return {"ok": True}

    def variant(self, change, **kwargs):
        return variant_run.make_variant(self.src, change, today="2026-11-04", setup=self.setup, **kwargs)

    def test_position_reuses_research_only(self):
        out = self.variant("position", position="20@45", mandate="I hold 20 shares at USD 45; buy the dip?")
        run = Path(out["run"])
        self.assertTrue(run.name.endswith("2026-11-04-v2"))
        self.assertEqual(out["resume_from"], "3_trading/trader")
        for name in ("1_analysts/market.md", "2_research/01_bull.md", "2_research/02_bear.md", "2_research/manager.md"):
            self.assertIn(name, out["reused"])
        for stage in run_plan.plan(SETTINGS):
            if "0_portfolio.md" in stage.inputs or "0_past_context.md" in stage.inputs:
                self.assertNotIn(stage.output, out["reused"], "anything that reads holdings must run again")
        self.assertEqual(self.setup_calls, ["20@45"])
        settings = json.loads((run / "0_settings.json").read_text())
        self.assertEqual((settings["variant"]["change"], settings["derived_from"].endswith("2026-11-04")), ("position", True))
        instrument = (run / "0_instrument.md").read_text()
        self.assertIn("I hold 20 shares at USD 45", instrument)
        self.assertNotIn("8-12 months", instrument)
        self.assertIn("## Variant", instrument)

    def test_horizon_reruns_market_and_everything_after(self):
        out = self.variant("horizon", holding_days=60)
        self.assertEqual(out["resume_from"], "1_analysts/market")
        self.assertNotIn("1_analysts/market.md", out["reused"])
        self.assertNotIn("0_data/market.md", out["reused"])
        self.assertIn("1_analysts/news.md", out["reused"])
        self.assertFalse(any(name.startswith(("2_research", "3_", "4_", "5_")) for name in out["reused"]))
        self.assertIn("- Holding period: 60 trading days", (Path(out["run"]) / "0_instrument.md").read_text())

    def test_question_and_language_reuse_data_only(self):
        out = self.variant("language", language="Hindi")
        self.assertTrue(all(n.startswith("0_data/") or n.endswith(".json") for n in out["reused"]))
        self.assertIn("- Output language: Hindi", (Path(out["run"]) / "0_instrument.md").read_text())
        second = self.variant("question", mandate="Focus on dilution risk.")
        self.assertTrue(second["run"].endswith("-v3"))

    def test_guards(self):
        with self.assertRaises(ValueError):
            variant_run.make_variant(self.src, "position", position="1@2", today="2026-11-05", setup=self.setup)
        out = variant_run.make_variant(self.src, "position", position="1@2", today="2026-11-05", allow_older=True,
                                       setup=self.setup)
        self.assertEqual(out["resume_from"], "3_trading/trader")
        with self.assertRaises(ValueError):
            self.variant("horizon")
        (self.src / "2_research" / "manager.md").unlink()
        with self.assertRaises(ValueError):
            self.variant("position", position="1@2")


if __name__ == "__main__":
    unittest.main()
