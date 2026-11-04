"""Tests for the session profiler (tools/profile_session.py) on a synthetic event log."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import profile_session  # noqa: E402


def usage(at: str, seconds: float, out: int, agent: str | None = None, ttft: float = 1.0) -> dict:
    event = {"type": "session.usage_record", "timestamp": at,
             "data": {"usage": {"model": "m", "duration": seconds * 1000, "timeToFirstTokenMs": ttft * 1000,
                                "inputTokens": 100, "cacheReadTokens": 50, "outputTokens": out, "reasoningTokens": out // 2}}}
    if agent:
        event["agentId"] = agent
    return event


EVENTS = [
    {"type": "user.message", "timestamp": "2026-11-04T10:00:00Z", "data": {"content": "How will X do?"}},
    usage("2026-11-04T10:00:05Z", 5, 50),
    {"type": "subagent.started", "timestamp": "2026-11-04T10:00:10Z", "agentId": "a1",
     "data": {"agentName": "ta-bull-researcher", "agentDisplayName": "x-bull"}},
    usage("2026-11-04T10:00:20Z", 10, 100, "a1"),
    {"type": "tool.execution_start", "timestamp": "2026-11-04T10:00:20Z", "agentId": "a1",
     "data": {"toolCallId": "t1", "toolName": "yahoo-finance-get_stock_info"}},
    {"type": "tool.execution_complete", "timestamp": "2026-11-04T10:00:22Z", "agentId": "a1", "data": {"toolCallId": "t1"}},
    usage("2026-11-04T10:01:20Z", 60, 4000, "a1"),
    usage("2026-11-04T10:01:25Z", 5, 80, "a1"),
    {"type": "subagent.completed", "timestamp": "2026-11-04T10:01:30Z", "agentId": "a1",
     "data": {"agentName": "ta-bull-researcher", "durationMs": 80000, "totalToolCalls": 3, "totalTokens": 9000}},
    {"type": "tool.execution_start", "timestamp": "2026-11-04T10:02:00Z", "data": {"toolCallId": "t2", "toolName": "task_complete"}},
]


class ProfileSessionTest(unittest.TestCase):
    def test_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in EVENTS) + "\nnot json\n")
            result = profile_session.profile(profile_session.load(path))
        self.assertEqual(result["questions"][0]["minutes"], 2.0)
        bull = result["agents"]["x-bull"]
        self.assertEqual((bull["calls"], bull["before_write_s"], bull["write_s"], bull["after_write_s"]), (3, 10.0, 60.0, 5.0))
        self.assertEqual(result["subagents"][0]["seconds"], 80.0)
        self.assertEqual(result["totals"]["data_tool_seconds"], 2.0)
        self.assertEqual(result["totals"]["model_calls"], 4)
        self.assertIn("x-bull", profile_session.text_report(result))


if __name__ == "__main__":
    unittest.main()
