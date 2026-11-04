"""Tests for deterministic settlement of pending decisions (settle_pending.py), offline."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "skills" / "investment-debate" / "scripts"))

import memory_log  # noqa: E402
import settle_pending  # noqa: E402


def daily(start: str, n: int, first: float, step: float) -> list[dict]:
    out, day, price = [], date.fromisoformat(start), first
    while len(out) < n:
        if day.weekday() < 5:
            out.append({"Date": f"{day.isoformat()}T04:00:00.000Z", "Close": round(price, 4)})
            price += step
        day += timedelta(days=1)
    return out


class FakeServers:
    calls: list = []

    def __init__(self, names):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def call_many(self, calls):
        FakeServers.calls.extend(calls)
        out = []
        for _, _, args in calls:
            series = daily("2026-06-01", 40, 100.0, 1.0) if args["ticker"] == "MSFT" else daily("2026-06-01", 40, 1000.0, 2.0)
            out.append({"content": [{"type": "text", "text": json.dumps(series)}]})
        return out


class SettlePendingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.log = self.dir / "log.md"
        self.reports = self.dir / "reports"
        self.run = self.dir / "run"
        self.run.mkdir()
        FakeServers.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_weekdays_between(self):
        self.assertEqual(settle_pending.weekdays_between("2026-10-29", "2026-11-04"), 3)  # Fri, Mon, Tue
        self.assertEqual(settle_pending.weekdays_between("2026-10-30", "2026-10-31"), 0)

    def test_not_yet_settleable_needs_no_fetch(self):
        memory_log.store(self.log, "NASDAQ:MSFT", "2026-11-02", "Buy", "**Rating**: Buy")
        out = settle_pending.prepare(self.log, self.run, self.reports, "2026-11-04", servers_factory=FakeServers)
        self.assertEqual(out["entries"][0]["status"], "not yet settleable")
        self.assertEqual(FakeServers.calls, [])

    def test_prepare_apply_and_past_context(self):
        memory_log.store(self.log, "NASDAQ:MSFT", "2026-06-01", "Overweight", "**Rating**: Overweight")
        (self.run / "0_settings.json").write_text(json.dumps({"symbol": "NASDAQ:MSFT", "as_of": "2026-11-04"}))
        out = settle_pending.prepare(self.log, self.run, self.reports, "2026-11-04", servers_factory=FakeServers)
        entry = out["entries"][0]
        self.assertEqual((entry["status"], entry["holding_days"], entry["entry_date"]), ("ready", 20, "2026-06-02"))
        self.assertAlmostEqual(entry["raw"], 121.0 / 101.0 - 1, places=6)
        self.assertAlmostEqual(entry["alpha"], entry["raw"] - (1042.0 / 1002.0 - 1), places=6)
        self.assertIn("next_step", out)
        self.assertEqual({c[2]["ticker"] for c in FakeServers.calls}, {"MSFT", "^GSPC"})

        waiting = settle_pending.apply(self.log, self.run, self.reports, "2026-11-04", self.run, None)
        self.assertEqual(len(waiting["missing_reflections"]), 1)
        (self.run / "0_reflection_NASDAQ_MSFT_2026-06-01.md").write_text("The call worked. Lesson: size by ATR.")
        done = settle_pending.apply(self.log, self.run, self.reports, "2026-11-04", self.run, None)
        self.assertEqual(len(done["settled"]), 1)
        self.assertFalse(memory_log.load(self.log)[0]["pending"])
        self.assertIn("Past analyses of NASDAQ:MSFT", (self.run / "0_past_context.md").read_text())

    def test_old_runs_give_ticker_and_holding_days(self):
        old = self.reports / "COMEX_GC1!" / "2026-10-31"
        old.mkdir(parents=True)
        (old / "0_settings.json").write_text(json.dumps({"holding_days": 147, "benchmark": "^GSPC"}))
        (old / "0_instrument.md").write_text("- Symbol: COMEX:GC1!\n- Yahoo ticker: GC=F\n")
        settings = settle_pending.entry_settings(self.reports, "COMEX:GC1!", "2026-10-31")
        self.assertEqual((settings["yahoo_ticker"], settings["holding_days"]), ("GC=F", 147))
        memory_log.store(self.log, "COMEX:GC1!", "2026-10-31", "Underweight", "**Rating**: Underweight")
        out = settle_pending.prepare(self.log, self.run, self.reports, "2026-11-04", servers_factory=FakeServers)
        self.assertEqual(out["entries"][0]["status"], "not yet settleable")


if __name__ == "__main__":
    unittest.main()
