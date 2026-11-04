# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "mcp>=1.9,<2",
#   "yfinance>=0.2.50",
#   "pandas>=2.0",
#   "numpy>=1.26",
#   "tradingview-ta>=3.3",
# ]
# ///
"""Local, read-only MCP server: technical indicators for NSE, BSE and US stocks.

Fills the gap left by the free market-data servers: tradingview-mcp has no
NSE/BSE technical analysis, and the Yahoo Finance MCP returns raw bars only.

Tools
- technical_snapshot: TradingView's public technical rating and indicator values
  (no TradingView account needed).
- price_statistics: indicators computed here from Yahoo Finance daily bars
  (returns, volatility, ATR, RSI, MACD, moving averages, drawdown, beta).

Run: uv run --script mcp/market_technicals.py
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from mcp.server.fastmcp import FastMCP
from tradingview_ta import Interval, TA_Handler

mcp = FastMCP("market-technicals")

_SCREENER = {
    "NSE": "india", "BSE": "india",
    "NASDAQ": "america", "NYSE": "america", "AMEX": "america", "NYSEARCA": "america",
}
_INTERVALS = {
    "15m": Interval.INTERVAL_15_MINUTES, "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS, "1d": Interval.INTERVAL_1_DAY,
    "1w": Interval.INTERVAL_1_WEEK, "1M": Interval.INTERVAL_1_MONTH,
}
_KEY_INDICATORS = [
    "close", "open", "high", "low", "volume", "change",
    "RSI", "RSI[1]", "Stoch.K", "Stoch.D", "CCI20", "ADX", "ADX+DI", "ADX-DI", "AO", "Mom",
    "MACD.macd", "MACD.signal", "W.R", "BBPower", "UO",
    "EMA10", "EMA20", "EMA50", "EMA100", "EMA200",
    "SMA10", "SMA20", "SMA50", "SMA100", "SMA200",
    "BB.upper", "BB.lower", "VWMA", "HullMA9", "Ichimoku.BLine",
    "Pivot.M.Classic.S2", "Pivot.M.Classic.S1", "Pivot.M.Classic.Middle",
    "Pivot.M.Classic.R1", "Pivot.M.Classic.R2",
]


def _split(symbol: str, exchange: str | None) -> tuple[str, str]:
    """Return (EXCHANGE, TICKER) from 'NSE:LODHA', 'LODHA.NS' or ('LODHA', 'NSE')."""
    s = symbol.strip().upper()
    if ":" in s:
        ex, tk = s.split(":", 1)
        return ex, tk
    if s.endswith(".NS"):
        return "NSE", s[:-3]
    if s.endswith(".BO"):
        return "BSE", s[:-3]
    return (exchange or "NASDAQ").upper(), s


def to_yahoo(symbol: str) -> str:
    """'NSE:LODHA' -> 'LODHA.NS', 'BSE:RELIANCE' -> 'RELIANCE.BO', 'NASDAQ:MSFT' -> 'MSFT'.
    Index and FX tickers already in Yahoo form (^NSEI, INR=X) pass through."""
    s = symbol.strip()
    if s.startswith("^") or s.endswith("=X") or "." in s:
        return s.upper()
    ex, tk = _split(s, None)
    return {"NSE": f"{tk}.NS", "BSE": f"{tk}.BO"}.get(ex, tk)


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else round(v, 4)


@mcp.tool()
def technical_snapshot(symbol: str, exchange: str = "NSE", interval: str = "1d") -> dict:
    """TradingView public technical rating for one stock (no TradingView account needed).

    symbol: 'NSE:LODHA', 'BSE:RELIANCE', 'NASDAQ:MSFT', or a bare ticker with `exchange`.
    BSE takes the trading symbol, not the numeric scrip code.
    interval: 15m, 1h, 4h, 1d, 1w or 1M.
    Returns the overall summary (BUY/SELL/NEUTRAL counts), oscillator and moving-average
    ratings, and key indicator values (RSI, MACD, ADX, EMAs/SMAs, Bollinger, monthly pivots).
    """
    ex, tk = _split(symbol, exchange)
    screener = _SCREENER.get(ex)
    if not screener:
        return {"error": f"Unsupported exchange '{ex}'. Use one of {sorted(_SCREENER)}."}
    if interval not in _INTERVALS:
        return {"error": f"Unsupported interval '{interval}'. Use one of {list(_INTERVALS)}."}
    try:
        a = TA_Handler(symbol=tk, screener=screener, exchange=ex, interval=_INTERVALS[interval]).get_analysis()
    except Exception as e:  # upstream returns plain exceptions for unknown symbols
        return {"error": f"No TradingView technicals for {ex}:{tk}: {e}"}
    ind = a.indicators or {}
    return {
        "symbol": f"{ex}:{tk}",
        "interval": interval,
        "summary": a.summary,
        "oscillators": {"rating": a.oscillators.get("RECOMMENDATION"), "signals": a.oscillators.get("COMPUTE")},
        "moving_averages": {"rating": a.moving_averages.get("RECOMMENDATION"), "signals": a.moving_averages.get("COMPUTE")},
        "indicators": {k: _num(ind.get(k)) for k in _KEY_INDICATORS if k in ind},
        "source": "TradingView public scanner via tradingview-ta (delayed; not the paid TradingView MCP)",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _ret(close: pd.Series, n: int) -> float | None:
    return _num((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100) if len(close) > n else None


def _history(ticker: str, period: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True).dropna(subset=["Close"])
    # Align by calendar date so a stock and a benchmark in different time zones still join.
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    return df[~df.index.duplicated(keep="last")]


@mcp.tool()
def price_statistics(symbol: str, period: str = "2y", benchmark: str | None = None, horizon_days: int = 20) -> dict:
    """Indicators computed from Yahoo Finance daily bars for one stock.

    symbol: 'NSE:LODHA', 'LODHA.NS', 'BSE:RELIANCE', 'NASDAQ:MSFT' or 'MSFT'.
    period: Yahoo period for the bars (6mo, 1y, 2y, 5y). The default 2y gives a full 250-day return and SMA200.
    benchmark: Yahoo ticker; defaults to ^NSEI (NIFTY 50) for NSE/BSE and ^GSPC otherwise.
    horizon_days: trading days for the volatility-implied move range.
    Returns returns over several windows, realised volatility, ATR, RSI(14), MACD(12,26,9),
    SMA/EMA levels, Bollinger position, 52-week range, max drawdown, volume trend, and
    beta/correlation/relative return versus the benchmark.
    """
    yt = to_yahoo(symbol)
    df = _history(yt, period)
    if len(df) < 30:
        return {"error": f"Not enough Yahoo Finance history for {yt} ({len(df)} bars)."}
    c = df["Close"]
    last = float(c.iloc[-1])
    r = c.pct_change().dropna()
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    sma20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
    bb_up, bb_lo = sma20 + 2 * sd20, sma20 - 2 * sd20
    atr = _atr(df)
    win = c.iloc[-252:]
    hi52, lo52 = float(win.max()), float(win.min())
    dd = (c / c.cummax() - 1).min() * 100
    vol20 = r.iloc[-20:].std() * math.sqrt(252) * 100 if len(r) >= 20 else None
    vol60 = r.iloc[-60:].std() * math.sqrt(252) * 100 if len(r) >= 60 else None
    daily_sd = r.iloc[-60:].std() if len(r) >= 60 else r.std()
    move = last * daily_sd * math.sqrt(max(1, horizon_days))

    def ma(n: int) -> float | None:
        return _num(c.rolling(n).mean().iloc[-1]) if len(c) >= n else None

    out = {
        "symbol": yt,
        "currency": None,
        "as_of": str(c.index[-1].date()),
        "bars": len(c),
        "last_close": _num(last),
        "returns_pct": {"5d": _ret(c, 5), "20d": _ret(c, 20), "60d": _ret(c, 60), "120d": _ret(c, 120), "250d": _ret(c, 250)},
        "volatility_annualised_pct": {"20d": _num(vol20), "60d": _num(vol60)},
        "atr14": _num(atr.iloc[-1]),
        "atr14_pct_of_price": _num(atr.iloc[-1] / last * 100),
        "rsi14": _num(_rsi(c).iloc[-1]),
        "macd": {"macd": _num(macd.iloc[-1]), "signal": _num(sig.iloc[-1]), "histogram": _num((macd - sig).iloc[-1]),
                 "histogram_prev": _num((macd - sig).iloc[-2])},
        "moving_averages": {"sma20": ma(20), "sma50": ma(50), "sma200": ma(200),
                            "ema20": _num(c.ewm(span=20, adjust=False).mean().iloc[-1])},
        "bollinger_20_2": {"upper": _num(bb_up.iloc[-1]), "lower": _num(bb_lo.iloc[-1]),
                           "percent_b": _num((last - bb_lo.iloc[-1]) / (bb_up.iloc[-1] - bb_lo.iloc[-1]))},
        "range_52w": {"high": _num(hi52), "low": _num(lo52),
                      "pct_below_high": _num((1 - last / hi52) * 100), "pct_above_low": _num((last / lo52 - 1) * 100)},
        "max_drawdown_pct_in_period": _num(dd),
        "volume": {"avg20": _num(df["Volume"].iloc[-20:].mean()), "avg60": _num(df["Volume"].iloc[-60:].mean())},
        "volatility_implied_range": {
            "horizon_trading_days": horizon_days,
            "one_sd_move": _num(move),
            "low_1sd": _num(last - move),
            "high_1sd": _num(last + move),
            "note": "Historical 60-day volatility scaled by sqrt(horizon). Not a forecast; about 1 in 3 outcomes fall outside.",
        },
    }
    try:
        out["currency"] = yf.Ticker(yt).fast_info.get("currency")
    except Exception:
        pass

    bm = benchmark or ("^NSEI" if yt.endswith((".NS", ".BO")) else "^GSPC")
    try:
        b = _history(bm, period)["Close"]
        j = pd.concat([c, b], axis=1, keys=["s", "b"]).dropna()
        jr = j.pct_change().dropna()
        cov = np.cov(jr["s"], jr["b"])
        out["vs_benchmark"] = {
            "benchmark": bm,
            "beta": _num(cov[0, 1] / cov[1, 1]),
            "correlation": _num(jr["s"].corr(jr["b"])),
            "relative_return_pct": {
                w: _num(((j["s"].iloc[-1] / j["s"].iloc[-1 - n]) / (j["b"].iloc[-1] / j["b"].iloc[-1 - n]) - 1) * 100)
                for w, n in (("20d", 20), ("60d", 60), ("250d", 250)) if len(j) > n
            },
        }
    except Exception as e:
        out["vs_benchmark"] = {"benchmark": bm, "error": str(e)}

    out["source"] = "Yahoo Finance daily bars via yfinance (adjusted); indicators computed locally"
    out["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return out


if __name__ == "__main__":
    mcp.run()
