#!/usr/bin/env python3
"""The investment-debate graph as data: stages in order, their output files, their agents and their inputs.

This is the graph in SKILL.md, written once so every script agrees on it: prepare_run.py (resume point and
placeholders), variant_run.py (which stages a follow-up can reuse) and run_debate.py (the headless runner,
including the input isolation of each role). Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

ANALYSTS = ("market", "sentiment", "news", "fundamentals")
ANALYST_NAMES = {"market": "Market Analyst", "sentiment": "Sentiment Analyst", "news": "News Analyst",
                 "fundamentals": "Fundamentals Analyst"}
DEPTH_ROUNDS = {"shallow": 1, "medium": 3, "deep": 5}
RISK_ORDER = ("aggressive", "conservative", "neutral")
NO_FUNDAMENTALS = {"crypto", "forex", "futures"}
JUDGES = {"ta-research-manager", "ta-portfolio-manager"}


@dataclass
class Stage:
    """One step of the graph. `output` and `inputs` are paths relative to the run folder."""
    id: str
    kind: str  # analyst, debate, manager, trader, risk, portfolio, finalize
    agent: str | None
    output: str
    inputs: list[str] = field(default_factory=list)
    turn: str | None = None  # NN for debate and risk turns
    tier: str = "quick"  # quick or deep (TradingAgents runs the two judges on the deep model)


def placeholder(analyst: str) -> str:
    return f"(No {ANALYST_NAMES[analyst]} report in this run: it is not available, not an empty finding.)\n"


def default_analysts(asset_type: str) -> list[str]:
    return [a for a in ANALYSTS if not (a == "fundamentals" and asset_type in NO_FUNDAMENTALS)]


def plan(settings: dict) -> list[Stage]:
    analysts = [a for a in ANALYSTS if a in (settings.get("analysts") or ANALYSTS)]
    debate_rounds = int(settings.get("debate_rounds") or DEPTH_ROUNDS.get(settings.get("depth", "shallow"), 1))
    risk_rounds = int(settings.get("risk_rounds") or DEPTH_ROUNDS.get(settings.get("depth", "shallow"), 1))
    reports = [f"1_analysts/{a}.md" for a in ANALYSTS]
    stages = [Stage(f"1_analysts/{a}", "analyst", f"ta-{a}-analyst", f"1_analysts/{a}.md", ["0_instrument.md"])
              for a in analysts]

    turns: list[str] = []
    for count in range(2 * debate_rounds):
        side = "bull" if count % 2 == 0 else "bear"
        nn = f"{count + 1:02d}"
        output = f"2_research/{nn}_{side}.md"
        stages.append(Stage(f"2_research/{nn}_{side}", "debate", f"ta-{side}-researcher", output,
                            ["0_instrument.md", *reports, *turns], turn=nn))
        turns.append(output)
    stages.append(Stage("2_research/manager", "manager", "ta-research-manager", "2_research/manager.md",
                        ["0_instrument.md", *turns], tier="deep"))
    stages.append(Stage("3_trading/trader", "trader", "ta-trader", "3_trading/trader.md",
                        ["0_instrument.md", "0_portfolio.md", "1_analysts/market.md", "2_research/manager.md"]))

    risk_turns: list[str] = []
    for count in range(3 * risk_rounds):
        side = RISK_ORDER[count % 3]
        nn = f"{count + 1:02d}"
        output = f"4_risk/{nn}_{side}.md"
        stages.append(Stage(f"4_risk/{nn}_{side}", "risk", f"ta-{side}-risk-analyst", output,
                            ["3_trading/trader.md", "0_instrument.md", "0_portfolio.md", *reports, *risk_turns],
                            turn=nn))
        risk_turns.append(output)
    stages.append(Stage("5_portfolio/decision", "portfolio", "ta-portfolio-manager", "5_portfolio/decision.md",
                        ["0_instrument.md", "0_portfolio.md", "0_past_context.md", "2_research/manager.md",
                         "3_trading/trader.md", *risk_turns], tier="deep"))
    stages.append(Stage("finalize", "finalize", None, "complete_report.md"))
    return stages


def load_settings(run: Path) -> dict:
    try:
        return json.loads((run / "0_settings.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def done(run: Path, stage: Stage) -> bool:
    path = run / stage.output
    return path.exists() and path.stat().st_size > 0


def resume_point(run: Path, settings: dict | None = None) -> str:
    """The first stage whose output is missing, 'setup' if the run has no settings, or 'done'."""
    settings = settings if settings is not None else load_settings(run)
    if not settings:
        return "setup"
    for stage in plan(settings):
        if not done(run, stage):
            return stage.id
    return "done"
