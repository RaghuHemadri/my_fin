#!/usr/bin/env python3
"""Headless investment-debate runner: the TradingAgents graph driven by code, one Copilot CLI call per role.

In chat, the orchestrating model spends a turn (7-33 s in the profiled runs) before every role and keeps a
growing context. This runner walks the same graph (run_plan.py, from SKILL.md) deterministically:

  setup      prepare_run.py and prefetch_data.py (seconds), then settle_pending.py once for all instruments
  analysts   the selected analysts in parallel, each reading its prefetched data
  debate     bull and bear turns, research manager, trader, risk turns, portfolio manager, in order; each role
             gets one input bundle (RUN/_inputs/<stage>.md) built from exactly its input list, so the isolation
             rules hold by construction
  finalize   finalize_run.py (report, state log, memory-log entry)

Each role runs as `copilot -p <prompt> --agent <role> --allow-all-tools --no-ask-user`, with --model and --effort
when a quick or deep tier is set (unset by default: the CLI's model and effort for every role). Roles that use
no data skip the MCP servers. A run resumes from its first missing stage; a failed analyst is retried once and
then gets the "not available" placeholder (SKILL.md, Step 1); any other failed role stops that run. Stage
timings go to RUN/run_log.json and to the runs and stages tables of the shared store.

Needs the Copilot CLI, logged in once (`copilot`, then `/login`, or COPILOT_GITHUB_TOKEN). Tapetide allows 50
calls a day; each run uses at most about 10.

Examples:
  python3 .github/skills/investment-debate/scripts/run_debate.py NSE:TATAPOWER --holding-days 210 \
      --mandate "How may Tata Power perform over the next 8-12 months?"
  python3 .github/skills/investment-debate/scripts/run_debate.py NASDAQ:MSFT NSE:TCS --parallel 2
  python3 .github/skills/investment-debate/scripts/run_debate.py --run reports/NSE_TCS/2026-11-04   # resume
  python3 .github/skills/investment-debate/scripts/run_debate.py NSE:TCS --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "mcp"))
import run_plan  # noqa: E402
from prepare_run import rel  # noqa: E402

MCP_SERVERS = ("technicals", "yahoo-finance", "tradingmcp", "tapetide")
SESSION = ("session model", "session effort", "", None)
AUTH_MARKERS = ("No authentication information found", "To authenticate, you can use")
REFLECTION_PROMPT = (
    "You are a research analyst reviewing your own past decision now that the outcome is known. The outcome covers N "
    "trading days from the first close after the analysis date, which may be shorter than the horizon the decision was "
    "written for. Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown). Cover, in order: "
    "(1) what the N-day alpha shows about the directional call (cite the figure and the benchmark), and say so plainly "
    "if the window is too short to judge the thesis; (2) which part of the investment thesis this window supports or "
    "undercuts; (3) one concrete lesson to apply to the next similar analysis. Be specific and terse. Your text is "
    "stored verbatim and re-read by future analysts, so every word must earn its place.")


class AuthError(RuntimeError):
    """The Copilot CLI is not logged in."""


class RunFailed(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tier_values(settings: dict, stage: run_plan.Stage, overrides: dict) -> tuple[str | None, str | None]:
    """(model, effort) for a stage: the run's quick/deep tier, else the runner-wide default, else the CLI's own."""
    tier = stage.tier
    model = settings.get(f"{tier}_model")
    effort = settings.get(f"{tier}_effort")
    model = None if model in SESSION else model
    effort = None if effort in SESSION else effort
    return model or overrides.get("model"), effort or overrides.get("effort")


def build_inputs(run: Path, stage: run_plan.Stage) -> Path:
    """RUN/_inputs/<stage>.md: the stage's input files, in order and unchanged, each under a header."""
    parts = []
    for name in stage.inputs:
        path = run / name
        body = path.read_text(encoding="utf-8") if path.exists() else "(missing: this file does not exist in the run)\n"
        parts.append(f"=== FILE: {name} ===\n{body.rstrip()}\n")
    bundle = run / "_inputs" / f"{stage.id.replace('/', '__')}.md"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text("\n".join(parts), encoding="utf-8")
    return bundle


def stage_prompt(run: Path, settings: dict, stage: run_plan.Stage, bundle: Path | None) -> str:
    head = (f"You are the {stage.agent} role of the investment-debate skill (TradingAgents workflow), in a headless "
            f"run with no user to ask. Symbol: {settings['symbol']}. Analysis date: {settings['as_of']}. "
            f"Mode: {settings['mode']}. Output language: {settings.get('language', 'English')}. "
            f"Run folder (RUN): `{run}`. Your output path: `{run / stage.output}`.")
    if stage.kind == "analyst":
        name = stage.id.split("/")[1]
        return (f"{head} Holding period: {settings.get('holding_days')} trading days. Follow your instructions: read "
                f"RUN/0_instrument.md and your prefetched data file RUN/0_data/{name}.md first. Write the report to your "
                "output path, then reply with at most 8 lines. If something is missing, record it as a gap.")
    turn = f" Your turn number NN is {stage.turn}." if stage.turn else ""
    return (f"{head}{turn} Your input files are bundled, in order and unchanged, in `{bundle}`, each under a "
            "'=== FILE: <path> ===' header; read that one file with view and forceReadLargeFiles: true. Open other "
            "files only where your instructions allow it. Write your output file, then reply with at most 6 lines.")


class CopilotBackend:
    """Runs one role with the Copilot CLI in non-interactive mode."""

    def __init__(self, executable: str = "copilot", timeout_s: float = 1800, max_ai_credits: int | None = None):
        self.executable = executable
        self.timeout_s = timeout_s
        self.max_ai_credits = max_ai_credits

    def command(self, prompt: str, agent: str | None, model: str | None, effort: str | None, uses_data: bool,
                run: Path) -> list[str]:
        cmd = [self.executable, "-p", prompt, "--allow-all-tools", "--no-ask-user", "-s", "--no-auto-update",
               "--add-dir", str(run)]
        if agent:
            cmd += ["--agent", agent]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        if self.max_ai_credits:
            cmd += ["--max-ai-credits", str(self.max_ai_credits)]
        if not uses_data:  # no data needed: skip starting the MCP servers
            cmd += ["--disable-builtin-mcps", *(arg for name in MCP_SERVERS for arg in ("--disable-mcp-server", name))]
        return cmd

    def run(self, prompt: str, agent: str | None, model: str | None, effort: str | None, uses_data: bool, run: Path,
            log: Path, env: dict) -> str:
        cmd = self.command(prompt, agent, model, effort, uses_data, run)
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            log.write_text(f"$ {' '.join(cmd[:1] + cmd[3:])}\nTIMEOUT after {self.timeout_s:.0f}s\n{exc.stdout or ''}")
            raise RunFailed(f"{agent or 'copilot'} timed out after {self.timeout_s:.0f}s") from exc
        output = (proc.stdout or "") + (proc.stderr or "")
        log.write_text(f"$ {' '.join(cmd[:1] + cmd[3:])}\nexit {proc.returncode}\n{output}", encoding="utf-8")
        if any(marker in output for marker in AUTH_MARKERS):
            raise AuthError("the Copilot CLI is not logged in: run `copilot` and then `/login` (or set "
                            "COPILOT_GITHUB_TOKEN), then run this command again")
        if proc.returncode != 0:
            raise RunFailed(f"{agent or 'copilot'} exited with {proc.returncode}: {output.strip()[-300:]}")
        return output


class FakeBackend:
    """Writes deterministic outputs instead of calling a model. For tests and for checking the plan offline."""

    def __init__(self, fail: set[str] | None = None, delay_s: float = 0.0):
        self.calls: list[dict] = []
        self.fail = fail or set()
        self.delay_s = delay_s
        self.lock = threading.Lock()

    def run(self, prompt, agent, model, effort, uses_data, run, log, env):
        with self.lock:
            self.calls.append({"agent": agent, "model": model, "effort": effort, "uses_data": uses_data, "prompt": prompt,
                               "started": time.monotonic()})
        time.sleep(self.delay_s)
        if agent in self.fail:
            raise RunFailed(f"{agent} failed (fake)")
        target = Path(prompt.split("Your output path: `")[1].split("`")[0]) if "Your output path: `" in prompt else None
        if agent is None:  # reflection
            target = Path(prompt.split("Write it to `")[1].split("`")[0])
            target.write_text("Alpha was positive versus the benchmark. The thesis held. Lesson: size by ATR.\n")
            return "ok"
        body = {"ta-trader": "**Action**: Hold\n\nFINAL TRANSACTION PROPOSAL (research only, not an order): **HOLD**\n",
                "ta-portfolio-manager": "**Rating**: Hold\n**Confidence**: medium\n"}.get(agent, f"# {agent}\nfake output\n")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return "ok"


class Runner:
    def __init__(self, backend, overrides: dict | None = None, retries: int = 1, memory_log: Path | None = None,
                 store=None, echo=print):
        self.backend = backend
        self.overrides = overrides or {}
        self.retries = retries
        self.memory_log = memory_log
        self.store = store
        self.echo = echo

    def _record(self, method: str, *args, **kwargs) -> None:
        if self.store is not None:
            try:
                getattr(self.store, method)(*args, **kwargs)
            except Exception as exc:  # observability must not stop a run
                self.echo(f"  (store: {exc})")

    def run_stage(self, run: Path, settings: dict, stage: run_plan.Stage, run_id: str, log_rows: list) -> None:
        model, effort = tier_values(settings, stage, self.overrides)
        bundle = build_inputs(run, stage) if stage.kind != "analyst" else None
        prompt = stage_prompt(run, settings, stage, bundle)
        env = {**os.environ, "MY_FIN_RUN": run_id, "COPILOT_AUTO_UPDATE": "false"}
        started, attempts, status, error = time.time(), 0, "failed", None
        self._record("record_stage", run_id, stage.id, role=stage.agent, output=stage.output, status="running",
                     model=model, effort=effort, started_at=started)
        while attempts <= self.retries:
            attempts += 1
            try:
                self.backend.run(prompt, stage.agent, model, effort, stage.kind == "analyst", run,
                                 run / "_logs" / f"{stage.id.replace('/', '__')}.log", env)
                if run_plan.done(run, stage):
                    status, error = "done", None
                    break
                error = f"{stage.agent} did not write {stage.output}"
            except AuthError:
                self._record("record_stage", run_id, stage.id, status="not logged in", attempts=attempts,
                             finished_at=time.time())
                raise
            except RunFailed as exc:
                error = str(exc)
        if status != "done" and stage.kind == "analyst":
            analyst = stage.id.split("/")[1]
            (run / stage.output).write_text(run_plan.placeholder(analyst), encoding="utf-8")
            status = "placeholder"
        finished = time.time()
        row = {"stage": stage.id, "agent": stage.agent, "status": status, "attempts": attempts, "model": model or "cli default",
               "effort": effort or "cli default", "seconds": round(finished - started, 1), "error": error}
        log_rows.append(row)
        self._record("record_stage", run_id, stage.id, status=status, attempts=attempts, finished_at=finished)
        self.echo(f"  {run_id} {stage.id}: {status} in {row['seconds']}s" + (f" ({error})" if error else ""))
        if status == "failed":
            raise RunFailed(f"{stage.id} failed: {error}")

    def finalize(self, run: Path, settings: dict) -> dict:
        cmd = [sys.executable, str(HERE / "finalize_run.py"), "--run", str(run), "--symbol", settings["symbol"],
               "--date", settings["as_of"]]
        if settings.get("html") is False:
            cmd.append("--no-html")
        if self.memory_log:
            cmd += ["--memory-log", str(self.memory_log)]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
        try:
            return json.loads(proc.stdout)
        except ValueError:
            raise RunFailed(f"finalize_run.py failed: {proc.stderr.strip()[-300:]}")

    def run_debate(self, run: Path) -> dict:
        settings = run_plan.load_settings(run)
        if not settings:
            raise RunFailed(f"{run}/0_settings.json is missing")
        run_id = f"{run.parent.name}/{run.name}"  # e.g. NSE_TCS/2026-11-04, the same wherever reports live
        started = time.time()
        log_rows: list[dict] = []
        self._record("record_run", run_id, symbol=settings["symbol"], as_of=settings["as_of"], run_dir=rel(run),
                     mode=settings["mode"], depth=settings.get("depth"), settings=settings, status="running",
                     runner="run_debate.py", started_at=started)
        stages = run_plan.plan(settings)
        pending = [s for s in stages if s.kind != "finalize" and not run_plan.done(run, s)]
        analysts = [s for s in pending if s.kind == "analyst"]
        rating, status, error = None, "failed", None
        try:
            if analysts:
                with ThreadPoolExecutor(max_workers=len(analysts)) as pool:
                    for future in [pool.submit(self.run_stage, run, settings, s, run_id, log_rows) for s in analysts]:
                        future.result()
            for stage in [s for s in pending if s.kind != "analyst"]:
                self.run_stage(run, settings, stage, run_id, log_rows)
            final = self.finalize(run, settings)
            rating, status = final.get("rating"), "done"
        except RunFailed as exc:
            error = str(exc)
        except AuthError:
            self._record("record_run", run_id, status="not logged in", finished_at=time.time())
            raise
        finished = time.time()
        summary = {"run": rel(run), "symbol": settings["symbol"], "status": status, "rating": rating, "error": error,
                   "minutes": round((finished - started) / 60, 1), "stages_run": len(log_rows),
                   "stages_reused": len(stages) - 1 - len(pending)}
        (run / "run_log.json").write_text(json.dumps({**summary, "stages": log_rows, "finished_at": now_iso()},
                                                     indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        self._record("record_run", run_id, status=status, rating=rating, finished_at=finished)
        if (run / "complete_report.md").exists():
            summary["report"] = rel(run / "complete_report.md")
        return summary

    def settle(self, settle_prepare, settle_apply) -> dict:
        """Settle ready memory-log entries once, before any run, with one reflection call per entry."""
        plan = settle_prepare()
        written = 0
        for entry in [e for e in plan.get("entries", []) if e.get("status") == "ready"]:
            reflection = ROOT / entry["reflection_file"] if not Path(entry["reflection_file"]).is_absolute() \
                else Path(entry["reflection_file"])
            decision = entry.get("decision_file")
            prompt = (f"{REFLECTION_PROMPT}\n\nDecision under review: {entry['symbol']}, rated {entry['rating']} on "
                      f"{entry['date']}. Window: {entry['holding_days']} trading days, {entry['entry_date']} to "
                      f"{entry['exit_date']}. Raw return {entry['raw']:+.2%}, benchmark return "
                      f"{entry['benchmark_return']:+.2%}, alpha {entry['alpha']:+.2%} versus {entry.get('benchmark')}. "
                      + (f"The decision text is in `{decision}`. " if decision else "")
                      + f"Write it to `{reflection}` and reply with one line.")
            model, effort = self.overrides.get("model"), self.overrides.get("effort")
            self.backend.run(prompt, None, model, effort, False, reflection.parent,
                             reflection.parent / f"reflection_{entry['symbol'].replace(':', '_')}_{entry['date']}.log",
                             {**os.environ, "COPILOT_AUTO_UPDATE": "false"})
            written += 1
        applied = settle_apply() if written else {"settled": []}
        return {"ready": plan.get("ready", 0), "reflections": written, "settled": len(applied.get("settled", []))}


def setup_run(symbol: str, args: argparse.Namespace) -> Path:
    import prefetch_data  # noqa: PLC0415
    import prepare_run  # noqa: PLC0415
    argv = ["--symbol", symbol, "--depth", args.depth, "--language", args.language, "--holding-days", str(args.holding_days)]
    for flag in ("as_of", "analysts", "benchmark", "yahoo_ticker", "mandate", "position", "quick_model", "deep_model",
                 "quick_effort", "deep_effort", "reports_dir", "memory_log"):
        value = getattr(args, flag, None)
        if value:
            argv += [f"--{flag.replace('_', '-')}", str(value)]
    if args.fresh:
        argv.append("--fresh")
    if args.no_html:
        argv.append("--no-html")
    out = prepare_run.prepare(prepare_run.parser().parse_args(argv))
    run = Path(out["run"]) if Path(out["run"]).is_absolute() else ROOT / out["run"]
    prefetch_data.prefetch(run)
    return run


def plan_text(settings: dict, run: Path | None, overrides: dict) -> list[str]:
    lines = []
    for stage in run_plan.plan(settings):
        if stage.kind == "finalize":
            lines.append("finalize   finalize_run.py")
            continue
        state = "done" if run and run_plan.done(run, stage) else "to run"
        model, effort = tier_values(settings, stage, overrides)
        lines.append(f"{stage.kind:9}  {stage.id:28} {stage.agent:30} {stage.tier:5} model={model or 'cli default'} "
                     f"effort={effort or 'cli default'}  [{state}]")
    return lines


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("symbols", nargs="*", help="EXCHANGE:TICKER, one or more")
    p.add_argument("--run", type=Path, action="append", help="resume an existing run folder (repeatable)")
    p.add_argument("--holding-days", type=int, default=20)
    p.add_argument("--depth", choices=sorted(run_plan.DEPTH_ROUNDS), default="shallow")
    p.add_argument("--analysts")
    p.add_argument("--language", default="English")
    p.add_argument("--as-of")
    p.add_argument("--mandate")
    p.add_argument("--position")
    p.add_argument("--benchmark")
    p.add_argument("--yahoo-ticker")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--no-html", action="store_true")
    p.add_argument("--quick-model")
    p.add_argument("--deep-model")
    p.add_argument("--quick-effort")
    p.add_argument("--deep-effort")
    p.add_argument("--model", help="model for every role without a tier model (default: the CLI's own)")
    p.add_argument("--effort", help="reasoning effort for every role without a tier effort (default: the CLI's own)")
    p.add_argument("--parallel", type=int, default=1, help="instruments to run at the same time")
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--timeout-min", type=float, default=30, help="per role")
    p.add_argument("--max-ai-credits", type=int)
    p.add_argument("--no-settle", action="store_true", help="do not settle pending memory-log entries first")
    p.add_argument("--reports-dir")
    p.add_argument("--memory-log")
    p.add_argument("--backend", choices=("copilot", "fake"), default="copilot")
    p.add_argument("--copilot", default=os.environ.get("MY_FIN_COPILOT", "copilot"),
                   help="path of the Copilot CLI (env MY_FIN_COPILOT)")
    p.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.symbols and not args.run:
        parser().error("give one or more symbols, or --run")
    overrides = {"model": args.model, "effort": args.effort}
    if args.dry_run:
        for target in (args.run or []) + args.symbols:
            run = target if isinstance(target, Path) else None
            settings = run_plan.load_settings(run) if run else {
                "symbol": target, "analysts": args.analysts.split(",") if args.analysts else list(run_plan.ANALYSTS),
                "depth": args.depth, "quick_model": args.quick_model, "deep_model": args.deep_model,
                "quick_effort": args.quick_effort, "deep_effort": args.deep_effort}
            print(f"# {settings.get('symbol', target)}" + (f" ({rel(run)})" if run else " (new run)"))
            print("\n".join(plan_text(settings, run, overrides)))
        return 0
    if args.backend == "copilot" and not shutil.which(args.copilot):
        print(json.dumps({"error": f"Copilot CLI not found ({args.copilot}). Install it: npm install -g @github/copilot"}))
        return 2
    if len(args.symbols) + len(args.run or []) > 5:
        print("warning: Tapetide allows 50 calls a day and each run may use about 10", file=sys.stderr)

    from datastore import Store  # noqa: PLC0415
    import settle_pending  # noqa: PLC0415
    store = Store()
    backend = CopilotBackend(args.copilot, args.timeout_min * 60, args.max_ai_credits) if args.backend == "copilot" else FakeBackend()
    memory = Path(args.memory_log) if args.memory_log else None
    runner = Runner(backend, overrides, args.retries, memory, store, echo=lambda m: print(m, file=sys.stderr, flush=True))
    results: dict = {"started_at": now_iso()}
    try:
        if not args.no_settle:
            import memory_log  # noqa: PLC0415
            log = memory or memory_log.DEFAULT_LOG
            reports = Path(args.reports_dir) if args.reports_dir else ROOT / "reports"
            today = date.today().isoformat()
            folder = ROOT / "memory" / "settlements"
            results["settlement"] = runner.settle(
                lambda: settle_pending.prepare(log, folder, reports, today),
                lambda: settle_pending.apply(log, folder, reports, today, None, None))

        def one(target) -> dict:
            try:
                run = target if isinstance(target, Path) else setup_run(target, args)
                return runner.run_debate(run if run.is_absolute() else Path.cwd() / run)
            except AuthError:
                raise
            except (RunFailed, ValueError, LookupError, FileNotFoundError) as exc:
                return {"target": str(target), "status": "failed", "error": str(exc)}

        targets = list(args.run or []) + list(args.symbols)
        with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
            results["runs"] = list(pool.map(one, targets))
    except AuthError as exc:
        results["error"] = str(exc)
        print(json.dumps(results, indent=1))
        return 3
    finally:
        store.close()
    results["finished_at"] = now_iso()
    print(json.dumps(results, indent=1, ensure_ascii=False))
    return 0 if all(r.get("status") == "done" for r in results["runs"]) else 1


if __name__ == "__main__":
    sys.exit(main())
