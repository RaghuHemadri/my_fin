#!/usr/bin/env python3
"""Profile a Copilot session from its event log: where the time of an investment-debate run goes.

Reads ~/.copilot/session-state/<session>/events.jsonl, written by Copilot CLI and the VS Code agent host, and
reports:
- each question: wall time from the user's message to the answer;
- subagents: start, duration, tool calls and tokens;
- model calls per agent: number, total and time-to-first-token seconds, input, cached, output and reasoning
  tokens, model;
- phases per agent: time before the turn that writes the report (reading, fetching, searching), the writing
  turn itself, and after it;
- tool time by tool, and model time against data-tool time.

Usage:
  python3 tools/profile_session.py --latest            the newest session started in this repository
  python3 tools/profile_session.py <session-id>        or a path to an events.jsonl file
  python3 tools/profile_session.py --list              recent sessions with their first question
  add --json for machine-readable output
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get("COPILOT_HOME") or Path.home() / ".copilot") / "session-state"
DATA_SERVERS = ("technicals-", "yahoo-finance-", "tradingmcp-", "mcp_route_tapetide", "tapetide-")


def ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def load(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def sessions() -> list[tuple[Path, str, str]]:
    """(events path, cwd, first question) for every local session, newest first."""
    out = []
    for folder in STATE.glob("*"):
        events, meta = folder / "events.jsonl", folder / "workspace.yaml"
        if not events.exists():
            continue
        cwd = ""
        if meta.exists():
            for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("cwd:"):
                    cwd = line.split(":", 1)[1].strip()
        question = ""
        with events.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"user.message"' in line:
                    try:
                        content = json.loads(line)["data"].get("content") or ""
                    except ValueError:
                        continue
                    question = str(content).replace("\n", " ")[:100]
                    break
        out.append((events, cwd, question))
    return sorted(out, key=lambda item: item[0].stat().st_mtime, reverse=True)


def profile(events: list[dict]) -> dict:
    names = {e.get("agentId"): e["data"].get("agentDisplayName") or e["data"].get("agentName")
             for e in events if e.get("type") == "subagent.started"}

    def who(event: dict) -> str:
        return names.get(event.get("agentId"), "orchestrator") if event.get("agentId") else "orchestrator"

    questions, open_q = [], None
    for e in events:
        if e.get("type") == "user.message" and not e.get("agentId"):
            content = str(e["data"].get("content") or "")
            if content.lstrip().startswith("<system_notification"):
                continue
            if open_q:
                open_q["end"] = e["timestamp"]
                questions.append(open_q)
            open_q = {"question": content.replace("\n", " ")[:120], "start": e["timestamp"], "end": None}
        elif open_q and not e.get("agentId") and (e.get("type") == "session.task_complete" or (
                e.get("type") == "tool.execution_start" and e["data"].get("toolName") == "task_complete")):
            open_q["end"] = e["timestamp"]
            questions.append(open_q)
            open_q = None
    if open_q:
        open_q["end"] = events[-1]["timestamp"] if events else open_q["start"]
        questions.append(open_q)
    for q in questions:
        q["minutes"] = round((ts(q["end"]) - ts(q["start"])).total_seconds() / 60, 1)

    starts = {e.get("agentId"): e["timestamp"] for e in events if e.get("type") == "subagent.started"}
    subagents = [{"agent": names.get(e.get("agentId")), "role": e["data"].get("agentName"),
                  "start": starts.get(e.get("agentId")), "seconds": round((e["data"].get("durationMs") or 0) / 1000, 1),
                  "tool_calls": e["data"].get("totalToolCalls"), "tokens": e["data"].get("totalTokens")}
                 for e in events if e.get("type") == "subagent.completed"]

    usage: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("type") == "session.usage_record":
            u = e["data"].get("usage") or {}
            usage[who(e)].append({"s": (u.get("duration") or 0) / 1000, "ttft": (u.get("timeToFirstTokenMs") or 0) / 1000,
                                  "in": u.get("inputTokens") or 0, "cached": u.get("cacheReadTokens") or 0,
                                  "out": u.get("outputTokens") or 0, "reasoning": u.get("reasoningTokens") or 0,
                                  "model": u.get("model")})
    agents = {}
    for agent, calls in usage.items():
        write = max(range(len(calls)), key=lambda i: calls[i]["out"])
        agents[agent] = {"calls": len(calls), "model_seconds": round(sum(c["s"] for c in calls), 1),
                         "ttft_seconds": round(sum(c["ttft"] for c in calls), 1),
                         "input_tokens": sum(c["in"] for c in calls), "cached_tokens": sum(c["cached"] for c in calls),
                         "output_tokens": sum(c["out"] for c in calls), "reasoning_tokens": sum(c["reasoning"] for c in calls),
                         "before_write_s": round(sum(c["s"] for c in calls[:write]), 1),
                         "write_s": round(calls[write]["s"], 1), "after_write_s": round(sum(c["s"] for c in calls[write + 1:]), 1),
                         "models": sorted({c["model"] for c in calls if c["model"]})}

    started, tools = {}, defaultdict(lambda: [0, 0.0])
    for e in events:
        if e.get("type") == "tool.execution_start":
            started[e["data"].get("toolCallId")] = (e["timestamp"], e["data"].get("toolName"))
        elif e.get("type") == "tool.execution_complete":
            begin = started.get(e["data"].get("toolCallId"))
            if begin and begin[1] not in ("task", "read_agent"):
                seconds = (ts(e["timestamp"]) - ts(begin[0])).total_seconds()
                tools[begin[1]][0] += 1
                tools[begin[1]][1] += max(0.0, seconds)
    tool_rows = sorted(({"tool": k, "calls": v[0], "seconds": round(v[1], 1)} for k, v in tools.items()),
                       key=lambda r: -r["seconds"])
    data_seconds = round(sum(r["seconds"] for r in tool_rows if r["tool"].startswith(DATA_SERVERS)), 1)
    model_seconds = round(sum(a["model_seconds"] for a in agents.values()), 1)
    return {"questions": questions, "subagents": subagents, "agents": agents, "tools": tool_rows,
            "totals": {"model_seconds": model_seconds, "data_tool_seconds": data_seconds,
                       "model_calls": sum(a["calls"] for a in agents.values()),
                       "output_tokens": sum(a["output_tokens"] for a in agents.values()),
                       "reasoning_tokens": sum(a["reasoning_tokens"] for a in agents.values())}}


def text_report(p: dict) -> str:
    lines = ["Questions:"]
    lines += [f"  {q['minutes']:6.1f} min  {q['question']}" for q in p["questions"]] or ["  (none)"]
    lines += ["", "Subagents (start, seconds, tool calls, tokens):"]
    lines += [f"  {(s['start'] or '')[11:19]}  {s['seconds']:7.1f}s  {s['tool_calls'] or 0:4}  {s['tokens'] or 0:9}  {s['agent']}"
              for s in p["subagents"]] or ["  (none)"]
    lines += ["", f"{'Model calls by agent':34} {'calls':>5} {'model s':>8} {'ttft s':>7} {'before':>7} {'write':>7} "
                  f"{'after':>6} {'out tok':>8} {'reason':>7}"]
    for agent, a in sorted(p["agents"].items(), key=lambda kv: -kv[1]["model_seconds"]):
        lines.append(f"  {agent[:32]:32} {a['calls']:5} {a['model_seconds']:8.1f} {a['ttft_seconds']:7.1f} "
                     f"{a['before_write_s']:7.1f} {a['write_s']:7.1f} {a['after_write_s']:6.1f} {a['output_tokens']:8} "
                     f"{a['reasoning_tokens']:7}")
    lines += ["", "Tools by time (excluding subagent and wait calls):"]
    lines += [f"  {r['tool'][:50]:50} {r['calls']:4} calls {r['seconds']:7.1f}s" for r in p["tools"][:15]]
    t = p["totals"]
    lines += ["", f"Totals: {t['model_calls']} model calls, {t['model_seconds']:.0f} s of model time, "
                  f"{t['data_tool_seconds']:.0f} s in data tools, {t['output_tokens']} output tokens "
                  f"({t['reasoning_tokens']} reasoning)."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", nargs="?", help="session id or path to events.jsonl")
    parser.add_argument("--latest", action="store_true", help="newest session started in this repository")
    parser.add_argument("--list", action="store_true", help="list recent sessions")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        for path, cwd, question in sessions()[:20]:
            print(f"{path.parent.name}  {datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d %H:%M}  {question}"
                  + ("" if str(ROOT) in cwd else f"  [{cwd}]"))
        return 0
    if args.latest:
        here = [s for s in sessions() if s[1] == str(ROOT)]
        if not here:
            print("no session found for this repository", file=sys.stderr)
            return 1
        path = here[0][0]
    elif args.session:
        path = Path(args.session)
        if not path.exists():
            path = STATE / args.session / "events.jsonl"
    else:
        parser.error("give a session id or path, --latest or --list")
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1
    result = profile(load(path))
    print(json.dumps(result, indent=1) if args.json else text_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
