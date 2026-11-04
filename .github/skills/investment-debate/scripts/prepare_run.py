#!/usr/bin/env python3
"""Step 0 of investment-debate in one command: confirm the instrument, write the run settings, the instrument
file, the portfolio context and the past-lessons file, and report where the run resumes.

It replaces the orchestrator's hand-written setup (several model turns) with deterministic code. The content is
what SKILL.md, Step 0, specifies. Market data comes only from the configured local MCP servers, called through
mcp/stdio_client.py (and so through the shared cache): identity and sector from yahoo-finance get_stock_info,
prices from one tradingmcp stock_prices batch, FX from tradingmcp yahoo_price; in historical mode, closes on the
analysis date from yahoo-finance get_historical_stock_prices.

Files that already exist are kept (they are the run's checkpoint) unless --rewrite-setup is given.

Example:
  python3 .github/skills/investment-debate/scripts/prepare_run.py --symbol NSE:TATAPOWER --holding-days 210 \
      --mandate "User asks how the stock may perform over 8-12 months."
Prints one JSON object: the run folder, mode, resume point, files written, pending memory entries and gaps.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "mcp"))
import memory_log  # noqa: E402
import run_plan  # noqa: E402

DATA_SOURCE = "free MCP servers (technicals, Yahoo Finance, TradingMCP, Tapetide)"
US_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "NYSEARCA", "NYSEAMERICAN", "BATS", "CBOE", "OTC"}
ASSET_TYPES = {"EQUITY": "stock", "ETF": "fund", "MUTUALFUND": "fund", "INDEX": "index",
               "CRYPTOCURRENCY": "crypto", "CURRENCY": "forex", "FUTURE": "futures"}
SYMBOL_RE = re.compile(r"^[A-Z0-9_.&-]+:[A-Z0-9_.&!=^-]+$")
PERIODS = [(30, "1mo"), (91, "3mo"), (182, "6mo"), (365, "1y"), (730, "2y"), (1826, "5y"), (3652, "10y")]


def rel(path: Path | str) -> str:
    """A path relative to the repository when it is inside it."""
    path = Path(path).resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def safe_symbol(symbol: str) -> str:
    return symbol.replace(":", "_")


def yahoo_ticker_for(symbol: str) -> str | None:
    exchange, ticker = symbol.split(":", 1)
    if exchange == "NSE":
        return f"{ticker}.NS"
    if exchange == "BSE":
        return f"{ticker}.BO"
    if exchange in US_EXCHANGES:
        return ticker.replace(".", "-")  # BRK.B is BRK-B on Yahoo
    return None


def listing_country(symbol: str) -> str:
    exchange = symbol.split(":", 1)[0]
    return "India" if exchange in ("NSE", "BSE") else "US" if exchange in US_EXCHANGES else exchange


def period_covering(as_of: str, today: str, lookback_days: int = 10) -> str:
    """Smallest Yahoo period whose bars reach back from today past as_of (plus a lookback)."""
    need = (date.fromisoformat(today) - date.fromisoformat(as_of)).days + lookback_days
    return next((name for days, name in PERIODS if days >= need), "max")


def money(value: float | None, currency: str = "") -> str:
    if value is None:
        return "n/a"
    return f"{currency + ' ' if currency else ''}{value:,.2f}"


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


class McpFetcher:
    """The market data prepare_run needs, from the configured local MCP servers."""

    def __init__(self, servers):
        self.servers = servers
        self.log: list[str] = []

    def _text(self, server: str, tool: str, args: dict) -> tuple[str | None, str]:
        from stdio_client import cache_status, result_text  # noqa: PLC0415
        try:
            result = self.servers.call(server, tool, args)
        except Exception as exc:
            return None, f"{server} {tool} failed: {exc}"
        meta = (result.get("_meta") or {}).get("my_fin/cache") or {}
        stamp = meta.get("fetched_at") or datetime.now(timezone.utc).isoformat(timespec="seconds")
        note = f"{server} {tool}({', '.join(f'{k}={v}' for k, v in args.items())}), retrieved {stamp}"
        if cache_status(result) in ("hit", "coalesced"):
            note += " (shared cache)"
        if result.get("isError"):
            return None, f"{note}: error: {result_text(result)[:200]}"
        return result_text(result), note

    def stock_info(self, ticker: str) -> tuple[dict | None, str]:
        text, note = self._text("yahoo-finance", "get_stock_info", {"ticker": ticker})
        data = parse_json(text)
        return (data if isinstance(data, dict) and (data.get("quoteType") or data.get("longName")) else None), note

    def quotes(self, symbols: list[str]) -> tuple[dict[str, dict], str]:
        text, note = self._text("tradingmcp", "stock_prices", {"tickers": ",".join(symbols)})
        data = parse_json(text) or {}
        return {row.get("ticker"): row for row in data.get("rows") or [] if row.get("ticker")}, note

    def fx_to_inr(self, currency: str) -> tuple[float | None, str]:
        pair = "INR=X" if currency == "USD" else f"{currency}INR=X"
        text, note = self._text("tradingmcp", "yahoo_price", {"symbol": pair})
        data = parse_json(text) or {}
        price = data.get("price")
        return (float(price) if isinstance(price, (int, float)) else None), note

    def close_on(self, ticker: str, as_of: str, today: str) -> tuple[tuple[str, float] | None, str]:
        """Last daily close on or before as_of."""
        from cache_core import session_day  # noqa: PLC0415
        args = {"ticker": ticker, "period": period_covering(as_of, today), "interval": "1d"}
        text, note = self._text("yahoo-finance", "get_historical_stock_prices", args)
        bars = parse_json(text)
        if not isinstance(bars, list):
            return None, note
        closes = [(session_day(b["Date"]), b["Close"]) for b in bars if isinstance(b, dict) and b.get("Close") is not None]
        before = [c for c in closes if c[0] <= as_of]
        return (before[-1] if before else None), note


def read_holdings(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = (row.get("symbol") or "").strip()
            if not symbol:
                continue
            rows.append({"symbol": symbol, "quantity": float(row.get("quantity") or 0),
                         "avg_cost": float(row.get("avg_cost") or 0), "currency": (row.get("currency") or "").strip().upper()})
    return rows


def read_cash(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [{"currency": r["currency"].strip().upper(), "amount": float(r["amount"])}
                for r in csv.DictReader(handle) if (r.get("currency") or "").strip() and (r.get("amount") or "").strip()]


def parse_position(text: str) -> tuple[float, float]:
    match = re.fullmatch(r"\s*([\d.]+)\s*@\s*([\d.]+)\s*", text or "")
    if not match:
        raise ValueError("--position must look like QTY@AVG_COST, for example 20@45")
    return float(match.group(1)), float(match.group(2))


def portfolio_md(symbol: str, holdings: list[dict], cash: list[dict], prices: dict[str, tuple[float | None, str]],
                 sectors: dict[str, str], fx: dict[str, tuple[float | None, str]], notes: list[str], mode: str,
                 as_of: str, stated_position: bool) -> str:
    """0_portfolio.md. prices: symbol -> (price, currency); fx: currency -> (INR per unit, source note)."""
    if not holdings:
        return "# Portfolio context\n\nPortfolio context: not provided. Do not assume a flat book.\n"
    lines = ["# Portfolio context", ""]
    position = next((h for h in holdings if h["symbol"] == symbol), None)
    if position:
        source = "stated by the user in chat, not from portfolio/holdings.csv" if stated_position else "portfolio/holdings.csv"
        lines.append(f"Current position in {symbol}: {position['quantity']:g} at an average cost of "
                     f"{money(position['avg_cost'], position['currency'])} ({source}).")
    else:
        lines.append(f"No current position in {symbol}.")
    if mode == "historical":
        lines.append(f"Historical run as of {as_of}: these are today's holdings, used as a standing book; "
                     f"prices are the closes on or before {as_of}.")
    lines += ["", "| Symbol | Quantity | Avg cost | Last price | Market value (own ccy) | Value INR | Value USD | Weight | Unrealised P/L |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    usd_inr = fx.get("USD", (None, ""))[0]
    rows, total_inr = [], 0.0
    for h in holdings:
        price, ccy = prices.get(h["symbol"], (None, h["currency"]))
        ccy = ccy or h["currency"]
        value = h["quantity"] * price if price is not None else None
        rate = 1.0 if ccy == "INR" else fx.get(ccy, (None, ""))[0]
        value_inr = value * rate if value is not None and rate else None
        total_inr += value_inr or 0
        rows.append((h, price, ccy, value, value_inr))
    for h, price, ccy, value, value_inr in rows:
        value_usd = value_inr / usd_inr if value_inr is not None and usd_inr else None
        weight = f"{value_inr / total_inr * 100:.1f}%" if value_inr and total_inr else "n/a"
        pnl = f"{(price / h['avg_cost'] - 1) * 100:+.1f}%" if price and h["avg_cost"] else "n/a"
        label = h["symbol"] + (" (stated in chat)" if stated_position and h["symbol"] == symbol else "")
        lines.append(f"| {label} | {h['quantity']:g} | {money(h['avg_cost'], ccy)} | {money(price, ccy)} | "
                     f"{money(value, ccy)} | {money(value_inr, 'INR')} | {money(value_usd, 'USD')} | {weight} | {pnl} |")
    lines.append(f"| **Total** | | | | | **{money(total_inr, 'INR')}** | "
                 f"**{money(total_inr / usd_inr if usd_inr else None, 'USD')}** | 100% | |")

    def shares(key) -> str:
        buckets: dict[str, float] = {}
        for h, _, ccy, _, value_inr in rows:
            if value_inr:
                bucket = key(h, ccy)
                buckets[bucket] = buckets.get(bucket, 0) + value_inr
        return ", ".join(f"{k} {v / total_inr * 100:.1f}%" for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])) or "n/a"

    lines += ["", "Concentration (by INR value):",
              f"- Names: {shares(lambda h, c: h['symbol'])}",
              f"- Sector (Yahoo sector; 'unknown' when not available): {shares(lambda h, c: sectors.get(h['symbol'], 'unknown'))}",
              f"- Listing country: {shares(lambda h, c: listing_country(h['symbol']))}",
              f"- Currency: {shares(lambda h, c: c)}"]
    if cash:
        parts = []
        for c in cash:
            rate = 1.0 if c["currency"] == "INR" else fx.get(c["currency"], (None, ""))[0]
            parts.append(money(c["amount"], c["currency"]) + (f" (INR {c['amount'] * rate:,.2f})" if rate and c["currency"] != "INR" else ""))
        lines += ["", "Cash: " + "; ".join(parts) + " (portfolio/cash.csv)."]
    else:
        lines += ["", "Cash: not provided."]
    lines += ["", "Sources:"] + [f"- {n}" for n in notes]
    for ccy, (rate, note) in sorted(fx.items()):
        lines.append(f"- FX: {money(rate)} INR per {ccy} ({note})" if rate else f"- FX for {ccy}: not available ({note})")
    lines += ["", "Do not assume this is the user's complete financial portfolio. Amounts are in the currency shown."]
    return "\n".join(lines) + "\n"


def instrument_md(s: dict, info: dict | None, info_note: str, quote: dict | None, quote_note: str,
                  mandate: str | None) -> str:
    info = info or {}
    lines = ["# Instrument", "",
             f"- Symbol: {s['symbol']}",
             f"- Company: {info.get('longName') or info.get('shortName') or 'not confirmed'}",
             f"- Type: {info.get('quoteType') or 'not confirmed'}",
             f"- Asset type: {s['asset_type']}",
             f"- Exchange: {s['symbol'].split(':', 1)[0]}" + (f" (Yahoo: {info.get('exchange')})" if info.get("exchange") else ""),
             f"- Currency: {info.get('currency') or (quote or {}).get('currency') or 'not confirmed'}",
             f"- Sector / Industry: {info.get('sector') or 'n/a'} / {info.get('industry') or 'n/a'}",
             f"- Analysis date: {s['as_of']}",
             "- Mode: live" if s["mode"] == "live" else f"- Mode: historical as of {s['as_of']}",
             f"- Output language: {s['language']}",
             f"- Data source: {DATA_SOURCE}",
             f"- Yahoo ticker: {s.get('yahoo_ticker')}"]
    if s.get("tapetide_symbol"):
        lines.append(f"- Tapetide symbol: {s['tapetide_symbol']}")
    elif s["symbol"].startswith("BSE:"):
        lines.append("- Tapetide symbol: resolve with tapetide search_stocks")
    lines += [f"- Benchmark: {s['benchmark']}",
              f"- Holding period: {s['holding_days']} trading days (use it as horizon_days in technicals price_statistics)",
              f"- Depth: {s['depth']} ({s['debate_rounds']} bull/bear round(s), {s['risk_rounds']} risk round(s))",
              "",
              "Analyze exactly this instrument and refer to it by this exact symbol. Do not substitute a different "
              "company unless a tool result disproves this identity.",
              "", "## Identity check", "",
              f"- {info_note}: {info.get('longName') or info.get('shortName') or 'no match'}, {info.get('quoteType') or '?'}, "
              f"{info.get('fullExchangeName') or info.get('exchange') or '?'}, {info.get('currency') or '?'}."]
    if quote:
        lines.append(f"- {quote_note}: {quote.get('description') or quote.get('symbol')}, last price "
                     f"{money(quote.get('price'), quote.get('currency') or '')}.")
    elif quote_note:
        lines.append(f"- {quote_note}: no quote returned.")
    if mandate:
        lines += ["", "## Research mandate", "", mandate.strip()]
    return "\n".join(lines) + "\n"


def resolve_run_dir(reports: Path, symbol: str, as_of: str, fresh: bool) -> Path:
    base = reports / safe_symbol(symbol)
    run = base / as_of
    if not fresh or not run.exists():
        return run
    n = 2
    while (base / f"{as_of}-{n}").exists():
        n += 1
    return base / f"{as_of}-{n}"


def write(path: Path, text: str, rewrite: bool, written: list[str], kept: list[str], run: Path) -> None:
    rel = path.relative_to(run).as_posix()
    if path.exists() and path.stat().st_size > 0 and not rewrite:
        kept.append(rel)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    written.append(rel)


def prepare(args: argparse.Namespace, fetcher_factory=None) -> dict:
    started = time.perf_counter()
    symbol = args.symbol.strip().upper()
    if not SYMBOL_RE.match(symbol):
        raise ValueError(f"symbol must be EXCHANGE:TICKER, got {args.symbol!r}")
    today = args.today or date.today().isoformat()
    as_of = args.as_of or today
    if as_of > today:
        raise ValueError(f"analysis date {as_of} is in the future")
    mode = "historical" if as_of < today else "live"
    reports = Path(args.reports_dir)
    run = Path(args.run_dir) if args.run_dir else resolve_run_dir(reports, symbol, as_of, args.fresh)
    existing = run_plan.load_settings(run)
    if existing and not args.rewrite_setup:  # a resumed or variant run keeps its own settings
        symbol, as_of, mode = existing["symbol"], existing["as_of"], existing["mode"]
    yahoo = (existing.get("yahoo_ticker") if existing else None) or args.yahoo_ticker or yahoo_ticker_for(symbol)
    if not yahoo:
        raise ValueError(f"no default Yahoo ticker for {symbol}; pass --yahoo-ticker (for example GC=F for COMEX:GC1!)")
    benchmark = (existing.get("benchmark") if existing else None) or args.benchmark or memory_log.benchmark_for(symbol)
    if not benchmark:
        raise ValueError(f"no default benchmark for {symbol}; pass --benchmark with a Yahoo index ticker")

    holdings = read_holdings(Path(args.holdings))
    stated = False
    if args.position:
        qty, cost = parse_position(args.position)
        holdings = [h for h in holdings if h["symbol"] != symbol]
        holdings.append({"symbol": symbol, "quantity": qty, "avg_cost": cost, "currency": ""})
        stated = True
    cash = read_cash(Path(args.cash))

    from stdio_client import LocalServers, marketaux_configured  # noqa: PLC0415
    factory = fetcher_factory or (lambda: LocalServers(["yahoo-finance", "tradingmcp"]))
    gaps: list[str] = []
    with factory() as servers:
        fetch = servers if hasattr(servers, "stock_info") else McpFetcher(servers)
        info, info_note = fetch.stock_info(yahoo)
        if info is None:
            raise LookupError(f"could not confirm {symbol} with yahoo-finance get_stock_info({yahoo}): {info_note}. "
                              "Resolve the symbol first (tapetide search_stocks for Indian names) or ask the user.")
        quote, quote_note = None, ""
        if mode == "live":
            quotes, quote_note = fetch.quotes([symbol])
            quote = quotes.get(symbol)
        asset_type = ASSET_TYPES.get(str(info.get("quoteType", "")).upper(), "stock")

        # Portfolio prices, sectors and FX
        holding_symbols = [h["symbol"] for h in holdings]
        prices: dict[str, tuple[float | None, str]] = {}
        sectors: dict[str, str] = {}
        notes: list[str] = []
        if holdings:
            if mode == "live":
                quotes, note = fetch.quotes(holding_symbols)
                notes.append(note)
                for s in holding_symbols:
                    row = quotes.get(s)
                    prices[s] = (row.get("price"), row.get("currency")) if row else (None, "")
            else:
                for s in holding_symbols:
                    ticker = yahoo_ticker_for(s)
                    close, note = fetch.close_on(ticker, as_of, today) if ticker else (None, f"no Yahoo ticker for {s}")
                    notes.append(note)
                    prices[s] = (close[1], "") if close else (None, "")
            def holding_info(h: dict) -> dict | None:
                ticker = yahoo_ticker_for(h["symbol"])
                return info if h["symbol"] == symbol else (fetch.stock_info(ticker)[0] if ticker else None)

            with ThreadPoolExecutor(max_workers=8) as pool:
                infos = list(pool.map(holding_info, holdings))
            for h, hinfo in zip(holdings, infos):
                if hinfo:
                    sectors[h["symbol"]] = hinfo.get("sector") or hinfo.get("quoteType") or "unknown"
                    if not h["currency"]:
                        h["currency"] = (hinfo.get("currency") or "").upper()
                price, ccy = prices.get(h["symbol"], (None, ""))
                prices[h["symbol"]] = (price, ccy or h["currency"])
                if not h["currency"]:
                    h["currency"] = ccy
        currencies = {c for c in [*(p[1] for p in prices.values()), *(c["currency"] for c in cash), "USD"] if c and c != "INR"}
        def fx_rate(ccy: str) -> tuple[float | None, str]:
            if mode == "live":
                return fetch.fx_to_inr(ccy)
            pair = "INR=X" if ccy == "USD" else f"{ccy}INR=X"
            close, note = fetch.close_on(pair, as_of, today)
            return (close[1] if close else None, note + (f", close on {close[0]}" if close else ""))

        with ThreadPoolExecutor(max_workers=4) as pool:
            fx = dict(zip(sorted(currencies), pool.map(fx_rate, sorted(currencies))))
        missing = [s for s, (p, _) in prices.items() if p is None]
        if missing:
            gaps.append(f"no price for {', '.join(missing)}")

    analysts = args.analysts.split(",") if args.analysts else run_plan.default_analysts(asset_type)
    unknown = set(analysts) - set(run_plan.ANALYSTS)
    if unknown:
        raise ValueError(f"unknown analysts: {sorted(unknown)}")
    rounds = run_plan.DEPTH_ROUNDS[args.depth]
    if not marketaux_configured():
        gaps.append("Marketaux key not configured: tradingmcp financial_news and market_sentiment return no news")
    pending = [e for e in memory_log.load(Path(args.memory_log)) if e["pending"]]
    if existing and not args.rewrite_setup:
        existing.setdefault("yahoo_ticker", yahoo)
    settings = existing if existing and not args.rewrite_setup else {
        "symbol": symbol, "run_date": today, "mode": mode, "as_of": as_of, "asset_type": asset_type,
        "analysts": analysts, "depth": args.depth, "debate_rounds": args.debate_rounds or rounds,
        "risk_rounds": args.risk_rounds or rounds, "language": args.language,
        "quick_model": args.quick_model or "session model", "deep_model": args.deep_model or "session model",
        "quick_effort": args.quick_effort or "session effort", "deep_effort": args.deep_effort or "session effort",
        "holding_days": args.holding_days, "benchmark": benchmark, "html": not args.no_html,
        "memory_max_entries": args.memory_max_entries, "fresh": args.fresh, "memory_log": rel(args.memory_log),
        "memory_note": (f"{len(pending)} pending decision(s) in the memory log; settle them with settle_pending.py."
                        if pending else "No pending decisions in the memory log."),
        "data_source": DATA_SOURCE, "yahoo_ticker": yahoo,
        "tapetide_symbol": args.tapetide_symbol or (symbol.split(":", 1)[1] if symbol.startswith("NSE:") else None),
        "data_gaps": gaps, "prepared_by": "prepare_run.py",
        "prepared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    written: list[str] = []
    kept: list[str] = []
    run.mkdir(parents=True, exist_ok=True)
    run_plan.make_stage_dirs(run)
    write(run / "0_settings.json", json.dumps(settings, indent=1, ensure_ascii=False) + "\n", args.rewrite_setup,
          written, kept, run)
    write(run / "0_instrument.md", instrument_md(settings, info, info_note, quote, quote_note, args.mandate),
          args.rewrite_setup, written, kept, run)
    write(run / "0_portfolio.md", portfolio_md(symbol, holdings, cash, prices, sectors, fx, notes, mode, as_of, stated),
          args.rewrite_setup, written, kept, run)
    context = memory_log.past_context(Path(args.memory_log), symbol, as_of)
    pm_done = (run / "5_portfolio" / "decision.md").exists()
    write(run / "0_past_context.md", context + ("\n" if not context.endswith("\n") else ""), not pm_done,
          written, kept, run)
    for analyst in run_plan.ANALYSTS:
        if analyst not in settings["analysts"]:
            write(run / "1_analysts" / f"{analyst}.md", run_plan.placeholder(analyst), False, written, kept, run)

    return {"run": rel(run), "symbol": symbol, "mode": mode, "as_of": as_of, "asset_type": settings["asset_type"],
            "analysts": settings["analysts"], "resume_from": run_plan.resume_point(run, settings), "written": written,
            "kept": kept, "pending_memory": [{"date": e["date"], "symbol": e["symbol"], "rating": e["rating"]} for e in pending],
            "data_gaps": settings.get("data_gaps", gaps), "seconds": round(time.perf_counter() - started, 1)}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbol", required=True, help="EXCHANGE:TICKER, e.g. NSE:TCS or NASDAQ:MSFT")
    p.add_argument("--as-of", help="analysis date YYYY-MM-DD (default today; earlier means a historical run)")
    p.add_argument("--today", help=argparse.SUPPRESS)
    p.add_argument("--depth", choices=sorted(run_plan.DEPTH_ROUNDS), default="shallow")
    p.add_argument("--debate-rounds", type=int)
    p.add_argument("--risk-rounds", type=int)
    p.add_argument("--analysts", help="comma-separated subset of market,sentiment,news,fundamentals")
    p.add_argument("--language", default="English")
    p.add_argument("--holding-days", type=int, default=20)
    p.add_argument("--benchmark", help="Yahoo ticker; default by exchange (^GSPC, ^NSEI, ^BSESN)")
    p.add_argument("--yahoo-ticker", help="override, e.g. GC=F for COMEX:GC1!")
    p.add_argument("--tapetide-symbol")
    p.add_argument("--quick-model")
    p.add_argument("--deep-model")
    p.add_argument("--quick-effort")
    p.add_argument("--deep-effort")
    mandate = p.add_mutually_exclusive_group()
    mandate.add_argument("--mandate", help="the user's question and horizon, for every role")
    mandate.add_argument("--mandate-file", type=Path)
    p.add_argument("--position", help="QTY@AVG_COST stated by the user in chat, overriding holdings.csv")
    p.add_argument("--fresh", action="store_true", help="start a new run folder instead of resuming")
    p.add_argument("--rewrite-setup", action="store_true", help="rewrite existing setup files")
    p.add_argument("--run-dir", help="use this run folder (variant runs)")
    p.add_argument("--reports-dir", default=str(ROOT / "reports"))
    p.add_argument("--memory-log", default=str(memory_log.DEFAULT_LOG))
    p.add_argument("--memory-max-entries", type=int)
    p.add_argument("--holdings", default=str(ROOT / "portfolio" / "holdings.csv"))
    p.add_argument("--cash", default=str(ROOT / "portfolio" / "cash.csv"))
    p.add_argument("--no-html", action="store_true")
    p.add_argument("--no-prefetch", action="store_true", help="skip prefetch_data.py (the analysts fetch their own data)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.mandate_file:
        args.mandate = args.mandate_file.read_text(encoding="utf-8")
    try:
        summary = prepare(args)
    except (ValueError, LookupError) as exc:
        print(json.dumps({"error": str(exc)}, indent=1), file=sys.stdout)
        return 2
    if not args.no_prefetch:
        import prefetch_data  # noqa: PLC0415
        try:
            summary["prefetch"] = prefetch_data.prefetch(ROOT / summary["run"] if not Path(summary["run"]).is_absolute()
                                                         else Path(summary["run"]), today=args.today)
        except Exception as exc:  # the agents can still fetch their own data
            summary["prefetch"] = {"error": str(exc)}
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
