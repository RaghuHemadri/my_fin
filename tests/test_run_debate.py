"""Tests for the headless debate runner (run_debate.py) with a fake model backend, offline."""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "skills" / "investment-debate" / "scripts"))
sys.path.insert(0, str(ROOT / "mcp"))

import memory_log  # noqa: E402
import run_debate  # noqa: E402
import run_plan  # noqa: E402
from datastore import Store  # noqa: E402

SETTINGS = {"symbol": "NASDAQ:FAKE", "as_of": "2026-11-04", "mode": "live", "asset_type": "stock",
            "analysts": ["market", "sentiment", "news", "fundamentals"], "depth": "shallow", "debate_rounds": 1,
            "risk_rounds": 1, "language": "English", "holding_days": 20, "benchmark": "^GSPC", "html": False,
            "quick_model": "session model", "deep_model": "session model", "quick_effort": "session effort",
            "deep_effort": "session effort"}


def prepared_run(base: Path, **overrides) -> Path:
    run = base / "reports" / "NASDAQ_FAKE" / "2026-11-04"
    (run / "0_data").mkdir(parents=True)
    (run / "0_settings.json").write_text(json.dumps({**SETTINGS, **overrides}))
    for name, text in {"0_instrument.md": "INSTRUMENT", "0_portfolio.md": "HOLDINGS", "0_past_context.md": "LESSONS"}.items():
        (run / name).write_text(text + "\n")
    return run


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.log = self.base / "log.md"
        self.store = Store(self.base / "db.sqlite3")
        self.messages = []

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def runner(self, backend, **kwargs):
        return run_debate.Runner(backend, memory_log=self.log, store=self.store, echo=self.messages.append, **kwargs)

    def test_full_graph_order_isolation_and_records(self):
        run = prepared_run(self.base)
        backend = run_debate.FakeBackend(delay_s=0.3)
        started = time.monotonic()
        out = self.runner(backend).run_debate(run)
        self.assertEqual((out["status"], out["rating"], out["stages_run"]), ("done", "Hold", 12))
        agents = [c["agent"] for c in backend.calls]
        self.assertEqual(set(agents[:4]), {"ta-market-analyst", "ta-sentiment-analyst", "ta-news-analyst", "ta-fundamentals-analyst"})
        self.assertEqual(agents[4:], ["ta-bull-researcher", "ta-bear-researcher", "ta-research-manager", "ta-trader",
                                      "ta-aggressive-risk-analyst", "ta-conservative-risk-analyst", "ta-neutral-risk-analyst",
                                      "ta-portfolio-manager"])
        self.assertLess(time.monotonic() - started, 0.3 * 8 + 0.3 + 2.0, "analysts run in parallel")
        self.assertEqual([c["uses_data"] for c in backend.calls], [True] * 4 + [False] * 8)

        bull = (run / "_inputs" / "2_research__01_bull.md").read_text()
        self.assertIn("=== FILE: 1_analysts/fundamentals.md ===", bull)
        self.assertNotIn("HOLDINGS", bull)
        self.assertNotIn("LESSONS", bull)
        pm = (run / "_inputs" / "5_portfolio__decision.md").read_text()
        self.assertIn("LESSONS", pm)
        self.assertIn("=== FILE: 4_risk/03_neutral.md ===", pm)

        self.assertTrue((run / "complete_report.md").exists())
        self.assertIn("| NASDAQ:FAKE | Hold | pending]", self.log.read_text())
        log = json.loads((run / "run_log.json").read_text())
        self.assertEqual(len(log["stages"]), 12)
        self.assertEqual(self.store.runs("NASDAQ:FAKE")[0]["status"], "done")
        self.assertEqual(len(self.store.stages("NASDAQ_FAKE/2026-11-04")), 12)

        again = run_debate.FakeBackend()
        second = self.runner(again).run_debate(run)
        self.assertEqual((second["status"], second["stages_run"], again.calls), ("done", 0, []))

    def test_failures(self):
        run = prepared_run(self.base)
        out = self.runner(run_debate.FakeBackend(fail={"ta-news-analyst"})).run_debate(run)
        self.assertEqual(out["status"], "done")
        self.assertIn("not available, not an empty finding", (run / "1_analysts" / "news.md").read_text())

        run2 = prepared_run(self.base / "b")
        backend = run_debate.FakeBackend(fail={"ta-research-manager"})
        failed = self.runner(backend, retries=1).run_debate(run2)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(sum(c["agent"] == "ta-research-manager" for c in backend.calls), 2)
        self.assertNotIn("ta-trader", [c["agent"] for c in backend.calls])
        self.assertEqual(run_plan.resume_point(run2), "2_research/manager")

    def test_tiers(self):
        run = prepared_run(self.base, quick_effort="high", deep_model="deep-model-x")
        backend = run_debate.FakeBackend()
        self.runner(backend, overrides={"model": "quick-default", "effort": None}).run_debate(run)
        by_agent = {c["agent"]: c for c in backend.calls}
        self.assertEqual((by_agent["ta-bull-researcher"]["model"], by_agent["ta-bull-researcher"]["effort"]), ("quick-default", "high"))
        self.assertEqual((by_agent["ta-portfolio-manager"]["model"], by_agent["ta-portfolio-manager"]["effort"]), ("deep-model-x", None))

    def test_copilot_command_and_auth_detection(self):
        backend = run_debate.CopilotBackend("copilot")
        analyst = backend.command("p", "ta-news-analyst", None, None, True, Path("/r"))
        debate = backend.command("p", "ta-bear-researcher", "m1", "high", False, Path("/r"))
        self.assertIn("--agent", analyst)
        self.assertNotIn("--disable-mcp-server", analyst)
        self.assertEqual(debate[debate.index("--model") + 1], "m1")
        self.assertEqual(debate[debate.index("--effort") + 1], "high")
        self.assertEqual(debate.count("--disable-mcp-server"), 4)

        fake = self.base / "copilot"
        fake.write_text("#!/bin/sh\necho 'Error: No authentication information found.'\nexit 1\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        with self.assertRaises(run_debate.AuthError):
            run_debate.CopilotBackend(str(fake)).run("p", "ta-bull-researcher", None, None, False, self.base,
                                                     self.base / "log.txt", dict(os.environ))

    def test_settle_and_dry_run(self):
        reflection = self.base / "0_reflection_NASDAQ_FAKE_2026-06-01.md"
        plan = {"ready": 1, "entries": [{"status": "ready", "symbol": "NASDAQ:FAKE", "rating": "Buy", "date": "2026-06-01",
                                         "holding_days": 20, "entry_date": "2026-06-02", "exit_date": "2026-07-01",
                                         "raw": 0.1, "benchmark_return": 0.02, "alpha": 0.08, "benchmark": "^GSPC",
                                         "reflection_file": str(reflection)}]}
        applied = []
        out = self.runner(run_debate.FakeBackend()).settle(lambda: plan, lambda: applied.append(1) or {"settled": [1]})
        self.assertEqual(out, {"ready": 1, "reflections": 1, "settled": 1})
        self.assertTrue(reflection.read_text().startswith("Alpha was positive"))

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(run_debate.main(["NSE:TCS", "--dry-run", "--depth", "medium", "--deep-effort", "xhigh"]), 0)
        text = buffer.getvalue()
        self.assertEqual(text.count("ta-bull-researcher"), 3)
        self.assertIn("ta-portfolio-manager           deep  model=cli default effort=xhigh", text)

    def test_fast_profile_dry_run(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(run_debate.main(["NSE:TCS", "--dry-run", "--profile", "fast"]), 0)
        text = buffer.getvalue()
        self.assertIn("profile fast", text.splitlines()[0])
        self.assertIn("ta-bull-researcher             quick model=cli default effort=low", text)
        self.assertIn("ta-portfolio-manager           deep  model=cli default effort=medium", text)

    def test_fast_profile_reaches_the_backend(self):
        run = prepared_run(self.base, profile="fast", quick_effort="low", deep_effort="medium")
        backend = run_debate.FakeBackend()
        self.runner(backend, overrides={"model": None, "effort": "xhigh"}).run_debate(run)
        efforts = {c["agent"]: c["effort"] for c in backend.calls}
        self.assertEqual((efforts["ta-market-analyst"], efforts["ta-trader"]), ("low", "low"))
        self.assertEqual((efforts["ta-research-manager"], efforts["ta-portfolio-manager"]), ("medium", "medium"))


if __name__ == "__main__":
    unittest.main()
