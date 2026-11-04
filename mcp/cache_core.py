"""Pure helpers for the MCP response cache: TTL policy, cache keys, lossless result compaction, error
detection and Yahoo bar parsing.

Standard library only, so the tests and the run scripts can import it without the MCP SDK. The proxy
(`cache_proxy.py`) and the store (`datastore.py`) build on it.

Freshness policy: a result is reused only while it is younger than its tool's TTL. Quotes live for minutes,
technical readings and daily bars for 15-30 minutes, statements and holder data for hours. A typical debate
run takes 20-40 minutes, so every agent in a run sees one snapshot instead of several slightly different ones.
Tools not listed here are never cached.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

MINUTE = 60
HOUR = 3600

_INTRADAY = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}


def _history_ttl(args: dict) -> int:
    return 2 * MINUTE if str(args.get("interval", "1d")) in _INTRADAY else 30 * MINUTE


def _snapshot_ttl(args: dict) -> int:
    return 5 * MINUTE if str(args.get("interval", "1d")) in {"15m", "1h", "4h"} else 15 * MINUTE


Rule = int | Callable[[dict], int]

# Seconds a successful result stays fresh, per server and tool.
POLICY: dict[str, dict[str, Rule]] = {
    "technicals": {
        "technical_snapshot": _snapshot_ttl,
        "price_statistics": 15 * MINUTE,
    },
    "yahoo-finance": {
        "get_historical_stock_prices": _history_ttl,
        "get_stock_info": 30 * MINUTE,
        "get_yahoo_finance_news": 15 * MINUTE,
        "get_stock_actions": 12 * HOUR,
        "get_financial_statement": 12 * HOUR,
        "get_holder_info": 12 * HOUR,
        "get_recommendations": 6 * HOUR,
        "get_option_expiration_dates": 1 * HOUR,
        "get_option_chain": 5 * MINUTE,
    },
    "tradingmcp": {
        "stock_prices": 2 * MINUTE,
        "yahoo_price": 2 * MINUTE,
        "stock_extended_hours": 2 * MINUTE,
        "market_snapshot": 2 * MINUTE,
        "bitcoin_market_pulse": 2 * MINUTE,
        "financial_news": 15 * MINUTE,
        "market_sentiment": 15 * MINUTE,
        "stock_screener": 15 * MINUTE,
        "combined_analysis": 15 * MINUTE,
        "multi_timeframe_analysis": 15 * MINUTE,
        "multi_agent_analysis": 15 * MINUTE,
        "coin_analysis": 5 * MINUTE,
        "volume_confirmation_analysis": 5 * MINUTE,
        "stock_options_chain": 5 * MINUTE,
        "stock_options_unusual_activity": 15 * MINUTE,
        "backtest_strategy": 6 * HOUR,
        "compare_strategies": 6 * HOUR,
        "walk_forward_backtest_strategy": 6 * HOUR,
        "top_gainers": 5 * MINUTE,
        "top_losers": 5 * MINUTE,
        "bollinger_scan": 5 * MINUTE,
        "rating_filter": 5 * MINUTE,
        "consecutive_candles_scan": 5 * MINUTE,
        "advanced_candle_pattern": 5 * MINUTE,
        "volume_breakout_scanner": 5 * MINUTE,
        "smart_volume_scanner": 5 * MINUTE,
    },
}
PREFIX_POLICY: dict[str, list[tuple[str, int]]] = {
    "tradingmcp": [("futures_", 5 * MINUTE), ("egx_", 15 * MINUTE)],
}

_OFF = {"0", "off", "false", "no"}


def cache_enabled() -> bool:
    return os.environ.get("MY_FIN_CACHE", "1").strip().lower() not in _OFF


def ttl_for(server: str, tool: str, args: dict | None) -> int:
    """Seconds to keep a successful result; 0 means do not cache. MY_FIN_CACHE=0 disables caching and
    MY_FIN_CACHE_TTL_SCALE (for example 0.5 or 2) scales every TTL."""
    if not cache_enabled():
        return 0
    rule: Rule | None = POLICY.get(server, {}).get(tool)
    if rule is None:
        rule = next((ttl for prefix, ttl in PREFIX_POLICY.get(server, []) if tool.startswith(prefix)), None)
    if rule is None:
        return 0
    ttl = rule(args or {}) if callable(rule) else rule
    try:
        scale = float(os.environ.get("MY_FIN_CACHE_TTL_SCALE") or 1)
    except ValueError:
        scale = 1.0
    return max(0, int(ttl * scale))


def canonical_args(args: dict | None) -> str:
    return json.dumps(args or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def cache_key(server: str, tool: str, args: dict | None) -> str:
    return hashlib.sha256(f"{server}\x1f{tool}\x1f{canonical_args(args)}".encode()).hexdigest()


def _same_value(text: str, value: Any) -> bool:
    if isinstance(value, str) and value.strip() == text.strip():
        return True
    try:
        return json.loads(text) == value
    except ValueError:
        return False


def is_duplicate_structured(structured: Any, texts: list[str]) -> bool:
    """True when structuredContent only repeats a text block. FastMCP wraps a plain return value as
    {"result": value} and also sends the same value as text."""
    candidates = [structured]
    if isinstance(structured, dict) and set(structured) == {"result"}:
        candidates.append(structured["result"])
    return any(_same_value(text, value) for text in texts for value in candidates)


def strip_duplicate_structured(result: dict) -> dict:
    """Return a tool result (MCP CallToolResult as a dict) without structuredContent, losing nothing.

    Copilot shows both content and structuredContent to the model, so a Yahoo result that repeats itself
    doubles in size and crosses the 8 KB limit above which the output is written to a temporary file. A
    duplicate is dropped; anything else is appended as one more text block holding its JSON.
    """
    structured = result.get("structuredContent")
    if structured is None:
        return result
    out = {key: value for key, value in result.items() if key != "structuredContent"}
    content = list(result.get("content") or [])
    texts = [block.get("text", "") for block in content if block.get("type") == "text"]
    if not is_duplicate_structured(structured, texts):
        content.append({"type": "text", "text": json.dumps(structured, ensure_ascii=False)})
    out["content"] = content
    return out


_ERROR_TEXT = re.compile(r"^\s*(error|exception|traceback)\b", re.I)
_TRANSIENT_TEXT = re.compile(r"\b(rate.?limit|too many requests|timed out|timeout|temporarily unavailable|"
                             r"not configured|unauthori[sz]ed|forbidden)\b", re.I)


def is_error_result(result: dict) -> bool:
    """True for results that must not be cached: isError, a JSON object with an "error" field, text that
    starts with Error/Exception/Traceback, or a short message about rate limits, timeouts or missing keys."""
    if result.get("isError"):
        return True
    for block in result.get("content") or []:
        if block.get("type") != "text":
            continue
        text = (block.get("text") or "").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            if _ERROR_TEXT.match(text) or (len(text) < 600 and _TRANSIENT_TEXT.search(text)):
                return True
            continue
        if isinstance(data, dict) and data.get("error"):
            return True
    return False


def session_day(stamp: Any) -> str:
    """Trading day of a Yahoo daily bar. Yahoo stamps a bar at local midnight written in UTC (an NSE
    session on 23 Oct is '2026-10-22T18:30:00.000Z'), so shift by 12 hours before taking the date.
    Same rule as indicators.iso_day."""
    if isinstance(stamp, (int, float)):
        moment = datetime.fromtimestamp(float(stamp) / (1000 if stamp > 1e11 else 1), tz=timezone.utc)
        return (moment + timedelta(hours=12)).strftime("%Y-%m-%d")
    text = str(stamp)
    if "T" not in text:
        return text[:10]
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if moment.tzinfo is None:
        return text[:10]
    return (moment.astimezone(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%d")


def bars_from_history(text: str) -> list[dict]:
    """Parse yahoo-finance get_historical_stock_prices text (a JSON list of Date/Open/High/Low/Close/Volume
    records) into rows for the bars table. Returns [] for anything else."""
    try:
        records = json.loads(text)
    except ValueError:
        return []
    if not isinstance(records, list):
        return []
    rows = []
    for record in records:
        if not isinstance(record, dict) or "Date" not in record or record.get("Close") is None:
            continue
        rows.append({"date": session_day(record["Date"]), "open": record.get("Open"), "high": record.get("High"),
                     "low": record.get("Low"), "close": record.get("Close"), "volume": record.get("Volume")})
    return rows
