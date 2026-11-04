#!/usr/bin/env python3
"""Compute TradingAgents' indicator set from OHLCV bars, cut at an as-of date.

Port of the get_indicators tool (stockstats names): close_50_sma, close_200_sma, close_10_ema, macd, macds, macdh,
rsi, boll, boll_ub, boll_lb, atr, vwma. Input is OHLCV bars saved as JSON: a list of {t, o, h, l, c, v} or an object
with a "bars" list, Yahoo Finance MCP get_historical_stock_prices output (Date, Open, High, Low, Close, Volume), or a
CSV with date,open,high,low,close,volume.
Local computation only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ALL = ("close_50_sma", "close_200_sma", "close_10_ema", "macd", "macds", "macdh", "rsi", "boll", "boll_ub",
       "boll_lb", "atr", "vwma")


def iso_day(text: str) -> str:
    """Session date of a daily bar. Yahoo stamps daily bars at local midnight written in UTC
    (an NSE session on 23 Oct is '2026-10-22T18:30:00Z'), so shift by 12 hours before taking the date."""
    if "T" not in text:
        return text[:10]
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if stamp.tzinfo is None:
        return text[:10]
    return (stamp.astimezone(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%d")


def load_bars(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
        bars = [{"date": (r.get("date") or r.get("time") or "")[:10], "o": float(r["open"]), "h": float(r["high"]),
                 "l": float(r["low"]), "c": float(r["close"]), "v": float(r.get("volume") or 0)} for r in rows]
    else:
        data = json.loads(text)
        raw = data.get("bars", data.get("data", [])) if isinstance(data, dict) else data
        bars = []
        for b in raw:
            b = {str(k).lower(): v for k, v in b.items()}  # Yahoo bars use Date/Open/High/Low/Close/Volume
            stamp = b.get("t", b.get("time", b.get("date")))
            day = (datetime.fromtimestamp(float(stamp), tz=timezone.utc).strftime("%Y-%m-%d")
                   if isinstance(stamp, (int, float)) or str(stamp).replace(".", "", 1).isdigit() else iso_day(str(stamp)))
            bars.append({"date": day, "o": float(b.get("o", b.get("open"))), "h": float(b.get("h", b.get("high"))),
                         "l": float(b.get("l", b.get("low"))), "c": float(b.get("c", b.get("close"))),
                         "v": float(b.get("v", b.get("volume")) or 0)})
    return sorted(bars, key=lambda b: b["date"])


def check(bars: list[dict]) -> list[str]:
    issues = []
    dates = [b["date"] for b in bars]
    if len(set(dates)) != len(dates):
        issues.append("duplicate bar dates")
    for b in bars:
        if min(b["o"], b["h"], b["l"], b["c"]) <= 0:
            issues.append(f"{b['date']}: non-positive price")
        elif b["h"] < max(b["o"], b["c"]) - 1e-9 or b["l"] > min(b["o"], b["c"]) + 1e-9:
            issues.append(f"{b['date']}: high/low do not contain open/close")
    return issues[:20]


def sma(values: list[float], n: int) -> list[float | None]:
    out, total = [], 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def ema(values: list[float | None], n: int) -> list[float | None]:
    """EMA seeded with the SMA of the first n available values."""
    out, alpha, seed, prev = [], 2 / (n + 1), [], None
    for v in values:
        if v is None:
            out.append(None)
            continue
        if prev is None:
            seed.append(v)
            prev = sum(seed) / n if len(seed) == n else None
            out.append(prev)
            continue
        prev = alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def wilder(values: list[float], n: int) -> list[float | None]:
    out, prev = [], None
    for i, v in enumerate(values):
        if i < n - 1:
            out.append(None)
        elif i == n - 1:
            prev = sum(values[:n]) / n
            out.append(prev)
        else:
            prev = (prev * (n - 1) + v) / n
            out.append(prev)
    return out


def compute(bars: list[dict]) -> dict[str, list[float | None]]:
    close = [b["c"] for b in bars]
    high, low, vol = [b["h"] for b in bars], [b["l"] for b in bars], [b["v"] for b in bars]
    out = {"close_50_sma": sma(close, 50), "close_200_sma": sma(close, 200), "close_10_ema": ema(close, 10)}
    fast, slow = ema(close, 12), ema(close, 26)
    macd = [f - s if f is not None and s is not None else None for f, s in zip(fast, slow)]
    signal = ema(macd, 9)
    out.update(macd=macd, macds=signal,
               macdh=[m - s if m is not None and s is not None else None for m, s in zip(macd, signal)])
    gains = [0.0] + [max(close[i] - close[i - 1], 0.0) for i in range(1, len(close))]
    losses = [0.0] + [max(close[i - 1] - close[i], 0.0) for i in range(1, len(close))]
    avg_gain, avg_loss = wilder(gains[1:], 14), wilder(losses[1:], 14)
    rsi = [None]
    for g, l in zip(avg_gain, avg_loss):
        rsi.append(None if g is None else (100.0 if l == 0 else 100 - 100 / (1 + g / l)))
    out["rsi"] = rsi
    mid = sma(close, 20)
    sd = [math.sqrt(sum((x - mid[i]) ** 2 for x in close[i - 19:i + 1]) / 20) if mid[i] is not None else None
          for i in range(len(close))]
    out.update(boll=mid, boll_ub=[m + 2 * s if m is not None else None for m, s in zip(mid, sd)],
               boll_lb=[m - 2 * s if m is not None else None for m, s in zip(mid, sd)])
    true_range = [high[0] - low[0]] + [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
                                       for i in range(1, len(close))]
    out["atr"] = wilder(true_range, 14)
    pv, vwma = [c * v for c, v in zip(close, vol)], []
    for i in range(len(close)):
        window = sum(vol[i - 19:i + 1]) if i >= 19 else 0
        vwma.append(sum(pv[i - 19:i + 1]) / window if window else None)
    out["vwma"] = vwma
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bars", required=True, type=Path, help="JSON bars (Yahoo get_historical_stock_prices or {t,o,h,l,c,v}), or CSV")
    parser.add_argument("--as-of", help="drop bars after this date (YYYY-MM-DD) for point-in-time analysis")
    parser.add_argument("--indicators", default=",".join(ALL), help="comma-separated names (default: all)")
    parser.add_argument("--tail", type=int, default=1, help="values to show per indicator (default 1)")
    args = parser.parse_args(argv)

    names = [n.strip() for n in args.indicators.split(",") if n.strip()]
    unknown = [n for n in names if n not in ALL]
    if unknown:
        parser.error(f"unknown indicators {unknown}; choose from {', '.join(ALL)}")
    bars = load_bars(args.bars)
    if args.as_of:
        bars = [b for b in bars if b["date"] <= args.as_of]
    if not bars:
        parser.error("no bars on or before the as-of date")
    series = compute(bars)
    tail = bars[-max(1, args.tail):]
    result = {
        "as_of": args.as_of or bars[-1]["date"],
        "bars_used": len(bars),
        "first_bar": bars[0]["date"],
        "last_bar": bars[-1]["date"],
        "last_close": bars[-1]["c"],
        "data_issues": check(bars),
        "values": {n: [{"date": b["date"], "value": None if v is None else round(v, 4)}
                       for b, v in zip(tail, series[n][-len(tail):])] for n in names},
        "notes": "None means not enough bars for that indicator. Bollinger uses population standard deviation "
                 "(TradingView convention); RSI and ATR use Wilder smoothing; EMAs are seeded with an SMA.",
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
