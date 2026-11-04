"""Tests for the investment-debate scripts: memory log, report builder, rating parser and indicators."""

from __future__ import annotations

import csv
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # keep the skill folder free of __pycache__
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "skills" / "investment-debate" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finalize_run  # noqa: E402
import indicators  # noqa: E402
import memory_log  # noqa: E402


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True, env=env)


def write_closes(path: Path, rows: list[tuple]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "close", "bench_close"])
        writer.writerows(rows)


class MemoryLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.log = self.dir / "log.md"

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_is_idempotent(self):
        self.assertTrue(memory_log.store(self.log, "NSE:TCS", "2026-09-01", "Overweight", "**Rating**: Overweight"))
        self.assertFalse(memory_log.store(self.log, "NSE:TCS", "2026-09-01", "Overweight", "**Rating**: Overweight"))
        self.assertEqual(len(memory_log.load(self.log)), 1)

    def test_replace_pending_never_touches_settled_entries(self):
        memory_log.store(self.log, "NSE:TCS", "2026-09-01", "Hold", "**Rating**: Hold\n\nfast take")
        self.assertTrue(memory_log.replace_pending(self.log, "NSE:TCS", "2026-09-01", "Buy", "**Rating**: Buy\n\nfull take"))
        self.assertEqual([(e["rating"], e["decision"]) for e in memory_log.load(self.log)],
                         [("Buy", "**Rating**: Buy\n\nfull take")])
        self.assertFalse(memory_log.replace_pending(self.log, "NSE:TCS", "2026-09-02", "Sell", "**Rating**: Sell"))
        closes = self.dir / "c.csv"
        write_closes(closes, [("2026-09-01", 100, 1000), ("2026-09-02", 100, 1000), ("2026-09-03", 105, 1000)])
        window = memory_log.window_from_csv(closes, "2026-09-01", 1, "2026-10-29")
        memory_log.settle(self.log, "NSE:TCS", "2026-09-01", window, 1, "It worked.")
        before = self.log.read_text(encoding="utf-8")
        self.assertFalse(memory_log.replace_pending(self.log, "NSE:TCS", "2026-09-01", "Sell", "**Rating**: Sell"))
        self.assertEqual(self.log.read_text(encoding="utf-8"), before)

    def test_benchmarks(self):
        self.assertTrue(memory_log.benchmark_for("NSE:TCS"))
        self.assertTrue(memory_log.benchmark_for("BSE:500325"))
        self.assertTrue(memory_log.benchmark_for("NASDAQ:MSFT"))
        self.assertEqual(memory_log.benchmark_for("XETR:SAP"), "")

    def test_settle_window_tag_context_and_summary(self):
        memory_log.store(self.log, "NSE:TCS", "2026-09-01", "Overweight", "**Rating**: Overweight\n\nbuy dips")
        memory_log.store(self.log, "NASDAQ:MSFT", "2026-09-02", "Sell", "**Rating**: Sell")
        memory_log.store(self.log, "NSE:TCS", "2026-10-20", "Hold", "**Rating**: Hold")
        closes = self.dir / "c.csv"
        # Entry is the first close after the decision date; a row without a benchmark close is skipped;
        # today's bar is excluded.
        write_closes(closes, [("2026-09-01", 100, 1000), ("2026-09-02", 100, 1000), ("2026-09-03", 101, 1001),
                              ("2026-09-04", 102, ""), ("2026-09-07", 103, 1002), ("2026-09-08", 110, 1010),
                              ("2026-10-29", 999, 999)])
        window = memory_log.window_from_csv(closes, "2026-09-01", 3, "2026-10-29")
        self.assertEqual((window["entry_date"], window["exit_date"]), ("2026-09-02", "2026-09-08"))
        self.assertIsNone(memory_log.window_from_csv(closes, "2026-09-01", 10, "2026-10-29"))
        result = memory_log.settle(self.log, "NSE:TCS", "2026-09-01", window, 3, "Alpha +9.0% supports the call.")
        self.assertAlmostEqual(result["raw"], 0.10, places=6)
        self.assertAlmostEqual(result["alpha"], 0.09, places=6)
        self.assertIn("[2026-09-01 | NSE:TCS | Overweight | +10.0% | +9.0% | 3d | resolved:2026-09-08]",
                      self.log.read_text(encoding="utf-8"))

        write_closes(closes, [("2026-09-03", 400, 5000), ("2026-09-04", 390, 5050), ("2026-09-08", 380, 5100),
                              ("2026-09-09", 370, 5100)])
        window = memory_log.window_from_csv(closes, "2026-09-02", 3, "2026-10-29")
        memory_log.settle(self.log, "NASDAQ:MSFT", "2026-09-02", window, 3, "The Sell call was right.")

        context = memory_log.past_context(self.log, "NSE:TCS", "2026-10-29")
        self.assertTrue(context.startswith("Past analyses of NSE:TCS"))
        self.assertIn("Recent cross-ticker lessons:", context)
        self.assertEqual(memory_log.past_context(self.log, "NSE:TCS", "2026-09-05"), "No resolved past decisions yet.")

        summary = memory_log.summarize(self.log)
        self.assertIn("Overweight: n=1, called the direction 100%", summary)
        self.assertIn("Sell: n=1, called the direction 100%", summary)
        self.assertIn("pending: 1", summary)

        kept = [memory_log.parse_entry(b) for b in memory_log.rotate(memory_log.read_blocks(self.log), 1)]
        self.assertEqual(sum(not e["pending"] for e in kept), 1)
        self.assertEqual(sum(e["pending"] for e in kept), 1)

    def test_cli_not_yet_settleable(self):
        memory_log.store(self.log, "NSE:TCS", "2026-10-28", "Buy", "**Rating**: Buy")
        closes = self.dir / "c.csv"
        write_closes(closes, [("2026-10-29", 10, 100)])
        out = run("memory_log.py", "--log", str(self.log), "settle", "--symbol", "NSE:TCS", "--date", "2026-10-28",
                  "--closes-csv", str(closes), "--holding-days", "5", "--today", "2026-10-30")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["status"], "not yet settleable")


class RatingParserTest(unittest.TestCase):
    CASES = {
        "**Rating**: Overweight\nbody": "Overweight",
        "# Decision\n\n**Rating**: **Underweight**": "Underweight",
        "- **Rating**: Sell": "Sell",
        "**Final Rating** — Hold": "Hold",
        "Rating：Buy": "Buy",
        "**Rating Scale**: Buy, Overweight\n**Rating**: Hold": "Hold",
        "Consensus rating: Buy\n**Rating**: Sell": "Sell",
        "Intro text\nConsensus rating: Buy": "REVIEW",
        "Intro\n**Rating**: Buy\n**Rating**: Sell": "REVIEW",
        "Intro\n**Rating**: Buy\n**Rating**: Buy": "Buy",
        "**Rating**: Strong Buy": "REVIEW",
        "**Ratings distribution**: 30 buy": "REVIEW",
        "no rating here": "REVIEW",
        "- Past rating: Sell was wrong\n**Rating**: Overweight": "Overweight",
    }

    def test_cases(self):
        for text, expected in self.CASES.items():
            with self.subTest(text=text):
                self.assertEqual(finalize_run.extract_rating(text), expected)


class FinalizeTest(unittest.TestCase):
    def test_report_html_state_and_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "reports" / "NSE_TCS" / "2026-10-29"
            for sub in ("1_analysts", "2_research", "3_trading", "4_risk", "5_portfolio"):
                (run_dir / sub).mkdir(parents=True)
            (run_dir / "0_settings.json").write_text(json.dumps({"mode": "live", "analysts": ["market"],
                                                                 "debate_rounds": 1, "risk_rounds": 1}))
            (run_dir / "1_analysts" / "market.md").write_text(
                "# Market\n| A | B |\n| --- | --- |\n| x \\| y | <script>z</script> |\n")
            (run_dir / "2_research" / "01_bull.md").write_text("# Bull\nup")
            (run_dir / "2_research" / "02_bear.md").write_text("# Bear\ndown")
            (run_dir / "4_risk" / "01_aggressive.md").write_text("go")
            (run_dir / "5_portfolio" / "decision.md").write_text("**Rating**: Underweight\n")
            log = Path(tmp) / "log.md"
            out = run("finalize_run.py", "--run", str(run_dir), "--symbol", "NSE:TCS", "--date", "2026-10-29",
                      "--memory-log", str(log))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(json.loads(out.stdout)["rating"], "Underweight")
            report = (run_dir / "complete_report.md").read_text(encoding="utf-8")
            self.assertIn("### Bull Researcher (turn 1)", report)
            self.assertIn("not available, not an empty finding", report)
            page = (run_dir / "complete_report.html").read_text(encoding="utf-8")
            self.assertIn("<td>x | y</td>", page)
            self.assertNotIn("<script>", page)
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual((state["final_rating"], state["investment_debate_state"]["count"]), ("Underweight", 2))
            self.assertIn("| NSE:TCS | Underweight | pending]", log.read_text(encoding="utf-8"))

    def test_full_run_replaces_a_same_day_fast_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.md"

            def finalize(folder: str, profile: str, rating: str) -> tuple[str, str]:
                run_dir = Path(tmp) / "reports" / "NSE_TCS" / folder
                (run_dir / "5_portfolio").mkdir(parents=True)
                (run_dir / "0_settings.json").write_text(json.dumps({"profile": profile, "analysts": [],
                                                                     "debate_rounds": 1, "risk_rounds": 1}))
                (run_dir / "5_portfolio" / "decision.md").write_text(f"**Rating**: {rating}\n")
                out = run("finalize_run.py", "--run", str(run_dir), "--symbol", "NSE:TCS", "--date", "2026-10-29",
                          "--memory-log", str(log), "--no-html")
                self.assertEqual(out.returncode, 0, out.stderr)
                return json.loads(out.stdout)["memory_entry"], (run_dir / "complete_report.md").read_text(encoding="utf-8")

            entry, report = finalize("2026-10-29", "fast", "Hold")
            self.assertEqual(entry, "added")
            self.assertIn("- Profile: fast", report)
            entry, report = finalize("2026-10-29-2", "full", "Buy")
            self.assertEqual(entry, "replaced the pending entry from a same-day fast run")
            self.assertNotIn("- Profile:", report)
            self.assertEqual(finalize("2026-10-29-3", "fast", "Sell")[0], "already present")  # fast never replaces
            self.assertEqual(finalize("2026-10-29-4", "full", "Sell")[0], "already present")  # the first full one stays
            self.assertEqual([e["rating"] for e in memory_log.load(log)], ["Buy"])

    def test_missing_decision_is_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run("finalize_run.py", "--run", tmp, "--symbol", "NSE:TCS", "--date", "2026-10-29",
                      "--memory-log", str(Path(tmp) / "log.md"), "--no-html")
            self.assertEqual(out.returncode, 1)
            self.assertEqual(json.loads(out.stdout)["rating"], "REVIEW")

    def test_rejects_bad_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotEqual(run("finalize_run.py", "--run", tmp, "--symbol", "TCS", "--date", "2026-10-29").returncode, 0)
            self.assertNotEqual(run("finalize_run.py", "--run", tmp, "--symbol", "NSE:TCS", "--date", "29-10-2026").returncode, 0)


class IndicatorsTest(unittest.TestCase):
    @staticmethod
    def bars(n: int = 320) -> list[dict]:
        rng, price, out = random.Random(7), 100.0, []
        for i in range(n):
            close = max(1.0, price * (1 + rng.gauss(0.0005, 0.015)))
            high = max(price, close) * (1 + abs(rng.gauss(0, 0.005)))
            low = min(price, close) * (1 - abs(rng.gauss(0, 0.005)))
            out.append({"t": 1735689600 + 86400 * i, "o": price, "h": high, "l": low, "c": close,
                        "v": rng.randint(100_000, 1_000_000)})
            price = close
        return out

    def test_matches_reference_formulas(self):
        bars = self.bars()
        values = {k: v[-1] for k, v in indicators.compute(indicators.load_bars(self._file(bars))).items()}
        close = [b["c"] for b in bars]
        high, low, vol = [b["h"] for b in bars], [b["l"] for b in bars], [b["v"] for b in bars]

        def ewm(xs, alpha):
            out = [xs[0]]
            for x in xs[1:]:
                out.append(alpha * x + (1 - alpha) * out[-1])
            return out

        macd = [a - b for a, b in zip(ewm(close, 2 / 13), ewm(close, 2 / 27))]
        signal = ewm(macd, 2 / 10)
        diff = [close[i] - close[i - 1] for i in range(1, len(close))]
        gain, loss = ewm([max(d, 0) for d in diff], 1 / 14)[-1], ewm([max(-d, 0) for d in diff], 1 / 14)[-1]
        mid = sum(close[-20:]) / 20
        sd = math.sqrt(sum((c - mid) ** 2 for c in close[-20:]) / 20)
        true_range = [high[0] - low[0]] + [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
                                           for i in range(1, len(close))]
        reference = {
            "close_50_sma": sum(close[-50:]) / 50, "close_200_sma": sum(close[-200:]) / 200,
            "close_10_ema": ewm(close, 2 / 11)[-1], "macd": macd[-1], "macds": signal[-1], "macdh": macd[-1] - signal[-1],
            "rsi": 100 - 100 / (1 + gain / loss), "boll": mid, "boll_ub": mid + 2 * sd, "boll_lb": mid - 2 * sd,
            "atr": ewm(true_range, 1 / 14)[-1],
            "vwma": sum(c * v for c, v in zip(close[-20:], vol[-20:])) / sum(vol[-20:]),
        }
        for name, expected in reference.items():
            with self.subTest(indicator=name):
                self.assertAlmostEqual(values[name], expected, delta=2e-4 * max(1.0, abs(expected)))

    def test_as_of_cut_and_cli(self):
        path = self._file(self.bars())
        out = run("indicators.py", "--bars", str(path), "--as-of", "2025-06-30", "--indicators", "rsi,atr", "--tail", "2")
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertEqual(result["last_bar"], "2025-06-30")
        self.assertEqual(len(result["values"]["rsi"]), 2)
        self.assertEqual(result["data_issues"], [])

    def _file(self, bars: list[dict]) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        with handle:
            json.dump({"bars": bars}, handle)
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)


if __name__ == "__main__":
    unittest.main()
