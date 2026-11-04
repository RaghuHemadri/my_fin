#!/usr/bin/env python3
"""Prefetch the analysts' data for an investment-debate run: once, in parallel, into one file per analyst.

Each analyst agent lists numbered tool calls in its "Data" section. Made by four agents one model turn at a
time, they cost dozens of turns and repeat each other (the market and sentiment analysts both call
technical_snapshot and price_statistics; news and sentiment both call financial_news). This script makes the
unconditional calls of the selected analysts once, concurrently, through the configured local MCP servers
(and so the shared cache), with the arguments the agent instructions give, and writes:

- RUN/0_data/<analyst>.md   the outputs, labelled with the step they answer; values exact, record lists as
                            lossless tables (data_formats.py)
- RUN/0_data/bars_1D.csv, bars_1W.csv   the daily (2y) and weekly (5y) bars as compact CSV
- RUN/1_analysts/bars_1D.json, bars_1W.json   the same bars exactly as returned, for indicators.py
- RUN/0_data/stock_info.json   the full get_stock_info answer (live runs)
- RUN/0_data/manifest.json   every call: arguments, status, retrieval time, cache status, size

Calls that need judgment (Tapetide, document summaries, earnings-call digests, peers, backtests) stay with
the agents. Historical runs follow references/point-in-time.md: current-snapshot tools are not called, bars
are fetched far enough back and their compact views end on the analysis date, and statements carry the
publication-lag rule.

Usage: python3 .github/skills/investment-debate/scripts/prefetch_data.py --run RUN [--roles market,news] [--force]
Prints a JSON summary. Exit code 0 even when some calls fail: failures are listed in the files as gaps.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "mcp"))
import data_formats  # noqa: E402
import run_plan  # noqa: E402
from prepare_run import US_EXCHANGES, period_covering, rel  # noqa: E402

MACRO = ("^TNX", "INR=X", "BZ=F", "^INDIAVIX")
TECHNICALS_EXCHANGES = {"NSE", "BSE"} | US_EXCHANGES
TARGET_FIELDS = ("currentPrice", "regularMarketPrice", "currency", "targetMeanPrice", "targetMedianPrice",
                 "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions", "recommendationKey",
                 "recommendationMean", "averageAnalystRating")
IDENTITY_FIELDS = ("longName", "shortName", "quoteType", "exchange", "fullExchangeName", "currency",
                   "financialCurrency", "sector", "industry", "country")
ROLE_TITLES = run_plan.ANALYST_NAMES


@dataclass
class Dataset:
    key: str
    server: str
    tool: str
    args: dict
    labels: dict[str, str]  # analyst -> the step of its instructions this answers
    view: str = "auto"  # auto, bars, inline_bars
    views: dict[str, str] = field(default_factory=dict)  # per-analyst override: targets, identity
    raw_file: str | None = None
    note: str = ""
    skip: str | None = None
    # filled in after the call
    status: str = "pending"
    text: str | None = None
    error: str | None = None
    fetched_at: str | None = None
    cache: str = ""


def datasets(settings: dict, roles: list[str], today: str) -> list[Dataset]:
    symbol, yt = settings["symbol"], settings.get("yahoo_ticker")
    exchange = symbol.split(":", 1)[0]
    as_of, historical = settings["as_of"], settings["mode"] == "historical"
    asset_type, hold, bench = settings.get("asset_type", "stock"), settings.get("holding_days", 20), settings.get("benchmark")
    technicals_ok = exchange in TECHNICALS_EXCHANGES
    us = exchange in US_EXCHANGES
    want = set(roles)
    items: list[Dataset] = []

    def add(*args, **kwargs) -> None:
        item = Dataset(*args, **kwargs)
        item.labels = {r: label for r, label in item.labels.items() if r in want}
        if item.labels:
            items.append(item)

    if historical:
        pit = f"Historical run: use only items dated on or before {as_of}."
        add("bars_1D", "yahoo-finance", "get_historical_stock_prices",
            {"ticker": yt, "period": period_covering(as_of, today, 740), "interval": "1d"},
            {"market": "Step 2 (daily bars) and the historical-mode snapshot"}, view="bars", raw_file="1_analysts/bars_1D.json")
        add("bars_1W", "yahoo-finance", "get_historical_stock_prices",
            {"ticker": yt, "period": period_covering(as_of, today, 1836), "interval": "1wk"},
            {"market": "Step 2 (weekly bars)"}, view="bars", raw_file="1_analysts/bars_1W.json")
        add("news_symbol", "tradingmcp", "financial_news", {"symbol": yt},
            {"sentiment": "Source 1 (news flow)", "news": "Step 1 (company news)"}, note=pit)
        add("market_sentiment", "tradingmcp", "market_sentiment", {"symbol": yt},
            {"sentiment": "Source 1 (Marketaux entity sentiment)"}, note=pit)
        if us:
            add("yahoo_news", "yahoo-finance", "get_yahoo_finance_news", {"ticker": yt},
                {"news": "Step 1 (Yahoo news, US listings)"}, note=pit)
        for ticker in MACRO:
            add(f"macro_{ticker}", "yahoo-finance", "get_historical_stock_prices",
                {"ticker": ticker, "period": period_covering(as_of, today, 40), "interval": "1d"},
                {"news": "Step 4 (macro proxies)"}, view="inline_bars")
        add("news_market", "tradingmcp", "financial_news", {}, {"news": "Step 5 (market-wide news)"}, note=pit)
        if asset_type == "stock":
            lag = (f"Historical run: use only periods published on or before {as_of} "
                   "(quarter end + 45 days, fiscal year end + 90 days); figures may have been restated since.")
            for kind in ("quarterly_income_stmt", "income_stmt", "balance_sheet", "cashflow"):
                add(f"stmt_{kind}", "yahoo-finance", "get_financial_statement", {"ticker": yt, "financial_type": kind},
                    {"fundamentals": f"Step 2 ({kind})"}, note=lag)
            add("stock_actions", "yahoo-finance", "get_stock_actions", {"ticker": yt},
                {"fundamentals": "Step 6 (dividends and splits)"}, note=f"Historical run: use only ex-dates on or before {as_of}.")
        add("stock_info", "yahoo-finance", "get_stock_info", {"ticker": yt}, {"fundamentals": "Step 1 (identity fields only)"},
            views={"fundamentals": "identity"},
            note="Historical run: only the identity fields are shown; the other fields are current values.")
        return items

    add("price_statistics", "technicals", "price_statistics",
        {"symbol": symbol if technicals_ok else yt, "period": "2y", "horizon_days": hold, **({"benchmark": bench} if bench else {})},
        {"market": "Step 1 (verified snapshot)", "sentiment": "Source 4 (attention and positioning proxies)"})
    for interval in ("1d", "1w"):
        add(f"technical_snapshot_{interval}", "technicals", "technical_snapshot", {"symbol": symbol, "interval": interval},
            {"market": f"Step 3 (TradingView rating, {interval})", "sentiment": f"Source 3 (technical consensus, {interval})"},
            skip=None if technicals_ok else "technicals technical_snapshot covers NSE, BSE and US listings only")
    if technicals_ok:
        add("stock_prices", "tradingmcp", "stock_prices", {"tickers": symbol}, {"market": "Step 1 (last-price cross-check)"})
    else:
        add("yahoo_price", "tradingmcp", "yahoo_price", {"symbol": yt}, {"market": "Step 1 (last-price cross-check)"})
    add("bars_1D", "yahoo-finance", "get_historical_stock_prices", {"ticker": yt, "period": "2y", "interval": "1d"},
        {"market": "Step 2 (daily bars, 2y)"}, view="bars", raw_file="1_analysts/bars_1D.json")
    add("bars_1W", "yahoo-finance", "get_historical_stock_prices", {"ticker": yt, "period": "5y", "interval": "1wk"},
        {"market": "Step 2 (weekly bars, 5y)"}, view="bars", raw_file="1_analysts/bars_1W.json")
    add("news_symbol", "tradingmcp", "financial_news", {"symbol": yt},
        {"sentiment": "Source 1 (news flow)", "news": "Step 1 (company news, Marketaux)"})
    add("market_sentiment", "tradingmcp", "market_sentiment", {"symbol": yt},
        {"sentiment": "Source 1 (Marketaux entity sentiment)"})
    add("recommendations", "yahoo-finance", "get_recommendations", {"ticker": yt, "recommendation_type": "recommendations"},
        {"sentiment": "Source 2 (buy/hold/sell split by month)", "fundamentals": "Step 3 (buy/hold/sell split)"})
    add("upgrades_downgrades", "yahoo-finance", "get_recommendations",
        {"ticker": yt, "recommendation_type": "upgrades_downgrades"}, {"sentiment": "Source 2 (recent rating changes)"})
    add("stock_info", "yahoo-finance", "get_stock_info", {"ticker": yt},
        {"sentiment": "Source 2 (price targets and analyst count)", "fundamentals": "Step 1 (snapshot, profile, ratios, targets)"},
        views={"sentiment": "targets"}, raw_file="0_data/stock_info.json")
    if us:
        add("yahoo_news", "yahoo-finance", "get_yahoo_finance_news", {"ticker": yt}, {"news": "Step 1 (Yahoo news, US listings)"})
    for ticker in MACRO:
        add(f"macro_{ticker}", "yahoo-finance", "get_historical_stock_prices", {"ticker": ticker, "period": "1mo", "interval": "1d"},
            {"news": "Step 4 (macro proxies, 1 month)"}, view="inline_bars")
    add("news_market", "tradingmcp", "financial_news", {}, {"news": "Step 5 (market-wide news, no symbol)"})
    if asset_type == "stock":
        for kind in ("quarterly_income_stmt", "income_stmt", "balance_sheet", "cashflow"):
            add(f"stmt_{kind}", "yahoo-finance", "get_financial_statement", {"ticker": yt, "financial_type": kind},
                {"fundamentals": f"Step 2 ({kind})"})
        add("stock_actions", "yahoo-finance", "get_stock_actions", {"ticker": yt}, {"fundamentals": "Step 6 (dividends and splits)"})
        if us:
            add("insider", "yahoo-finance", "get_holder_info", {"ticker": yt, "holder_type": "insider_transactions"},
                {"fundamentals": "Insider transactions (US listings)"})
    return items


def _step_number(label: str) -> int:
    """Sort key: the step or source number in a label ("Step 2 (...)" -> 2); unnumbered labels go last."""
    match = re.search(r"\b(?:Step|Source)\s+(\d+)", label)
    return int(match.group(1)) if match else 99


def _args_text(args: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items()) or "no arguments"


def _provenance(item: Dataset) -> str:
    if item.cache in ("hit", "coalesced"):
        return f"retrieved {item.fetched_at} (shared cache)"
    return f"retrieved {item.fetched_at} (live)"


def _section(item: Dataset, role: str, as_of: str, historical: bool, bars: dict) -> list[str]:
    head = [f"## {item.labels[role]} · {item.server} {item.tool}", "",
            f"Arguments: {_args_text(item.args)}" + (f" · {_provenance(item)}" if item.status == "ok" else "")]
    if item.note:
        head.append(f"Note: {item.note}")
    head.append("")
    if item.status == "skipped":
        return head + [f"**SKIPPED**: {item.skip}", ""]
    if item.status == "error":
        hint = (" This is a configuration gap, not a transient failure: record it as a gap; calling the tool again "
                "returns the same answer.") if "not configured" in (item.error or "").lower() else ""
        return head + [f"**ERROR**: {item.error}{hint}", ""]
    view = item.views.get(role, item.view)
    data = data_formats.parse_json(item.text)
    if view == "bars":
        summary = bars.get(item.key, {})
        lines = [f"{summary.get('bars', 0)} bars, {summary.get('first')} to {summary.get('last')}, last close "
                 f"{summary.get('last_close')} (high {summary.get('high')}, low {summary.get('low')} over the file).",
                 f"Compact CSV with every bar and field: `0_data/{item.key}.csv` (read it in the same batch as this file).",
                 f"Exactly as returned: `{item.raw_file}`."]
        if historical and summary.get("dropped_after_as_of"):
            lines.append(f"The CSV ends on {as_of}; {summary['dropped_after_as_of']} later bars are only in the raw file. "
                         "Do not use them.")
        return head + lines + [""]
    if view == "inline_bars" and data_formats.is_records(data):
        start = (date.fromisoformat(as_of) - timedelta(days=31)).isoformat() if historical else None
        csv_text, summary = data_formats.bars_csv(data, as_of=as_of if historical else None, start=start)
        return head + [f"{summary['bars']} daily bars, {summary['first']} to {summary['last']}:", "", "```csv",
                       csv_text.rstrip(), "```", ""]
    if view in ("targets", "identity") and isinstance(data, dict):
        keep = TARGET_FIELDS if view == "targets" else IDENTITY_FIELDS
        extract = {k: data[k] for k in keep if k in data}
        tail = ["The full answer is in `0_data/stock_info.json`."] if view == "targets" else []
        return head + ["Fields used for this step (values exact):", "", "```json",
                       json.dumps(extract, indent=1, ensure_ascii=False), "```", *tail, ""]
    kind, body = data_formats.render(item.text, item.tool)
    if kind in ("statement", "table"):
        caption = ("One row per line item, one column per period; amounts in raw currency units as Yahoo returns them."
                   if kind == "statement" else "Every row and field of the answer.")
        return head + [caption, "", body, ""]
    fence = "json" if kind == "json" else "text"
    return head + [f"```{fence}", body.rstrip(), "```", ""]


def _role_header(role: str, settings: dict, run: Path, stamp: str, extra: list[str]) -> list[str]:
    lines = [f"# Prefetched data for the {ROLE_TITLES[role]}", "",
             f"Run `{rel(run)}` · {settings['symbol']} · mode {settings['mode']} · analysis date {settings['as_of']} · "
             f"prefetched {stamp} by prefetch_data.py", "",
             "These are the outputs of the tool calls in your **Data** steps, made once for the whole team through the "
             "configured MCP servers (and the shared cache), with the arguments shown. Treat each one as your own tool "
             "result, and cite its tool and retrieval time. Values are copied exactly; record lists are shown as tables "
             "with every row and field (an empty cell is null; ∅ marks a field missing from that record).", "",
             "- A section marked **ERROR** or **SKIPPED** has no data: call the tool yourself if you still need it, or record the gap.",
             "- `tapetide` is never prefetched. Make your own `tapetide` calls, within your cap.",
             "- Steps that need judgment (documents to summarize, peers, optional backtests) are yours to call.",
             "- Your MCP budget counts only the calls you make yourself.", *extra, ""]
    return lines


def prefetch(run: Path, roles: list[str] | None = None, today: str | None = None, force: bool = False,
             servers_factory=None) -> dict:
    started = time.perf_counter()
    settings = run_plan.load_settings(run)
    if not settings:
        raise FileNotFoundError(f"{run}/0_settings.json is missing: run prepare_run.py first")
    if not settings.get("yahoo_ticker"):
        raise ValueError("0_settings.json has no yahoo_ticker: re-run prepare_run.py with --rewrite-setup")
    today = today or date.today().isoformat()
    data_dir = run / "0_data"
    selected = [a for a in run_plan.ANALYSTS if a in settings.get("analysts", [])]
    roles = [r for r in (roles or selected) if r in selected]
    if not force:
        roles = [r for r in roles if not (data_dir / f"{r}.md").exists()
                 and not ((run / "1_analysts" / f"{r}.md").exists() and (run / "1_analysts" / f"{r}.md").stat().st_size)]
    if not roles:
        return {"run": rel(run), "roles": [], "note": "nothing to prefetch (bundles or reports already exist)",
                "seconds": round(time.perf_counter() - started, 1)}

    items = datasets(settings, roles, today)
    runnable = [i for i in items if not i.skip]
    for item in items:
        if item.skip:
            item.status = "skipped"
    from stdio_client import LocalServers, cache_status, result_text  # noqa: PLC0415
    factory = servers_factory or (lambda names: LocalServers(names))
    with factory(sorted({i.server for i in runnable})) as servers:
        results = servers.call_many([(i.server, i.tool, i.args) for i in runnable]) if runnable else []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for item, result in zip(runnable, results):
        if isinstance(result, Exception):
            item.status, item.error = "error", str(result)
            continue
        meta = (result.get("_meta") or {}).get("my_fin/cache") or {}
        item.fetched_at, item.cache = meta.get("fetched_at") or now, cache_status(result)
        item.text = result_text(result)
        if result.get("isError"):
            item.status, item.error = "error", item.text[:500]
        elif (err := data_formats.parse_json(item.text)) and isinstance(err, dict) and err.get("error"):
            item.status, item.error = "error", f"{err.get('error')} (full answer: {item.text[:300]})"
        else:
            item.status = "ok"

    data_dir.mkdir(parents=True, exist_ok=True)
    historical = settings["mode"] == "historical"
    as_of = settings["as_of"]
    bars: dict[str, dict] = {}
    for item in items:
        if item.status != "ok":
            continue
        if item.raw_file and not (historical and item.key == "stock_info"):
            path = run / item.raw_file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item.text, encoding="utf-8")
        if item.view == "bars":
            records = data_formats.parse_json(item.text)
            if data_formats.is_records(records):
                csv_text, bars[item.key] = data_formats.bars_csv(records, as_of=as_of if historical else None)
                (data_dir / f"{item.key}.csv").write_text(csv_text, encoding="utf-8")
            else:
                item.status, item.error = "error", f"no bars returned: {item.text[:200]}"

    indicators = None
    if "market" in roles and any(i.key == "bars_1D" and i.status == "ok" for i in items):
        proc = subprocess.run([sys.executable, str(HERE / "indicators.py"), "--bars", str(run / "1_analysts" / "bars_1D.json"),
                               "--as-of", as_of, "--tail", "5"], capture_output=True, text=True, timeout=120)
        indicators = proc.stdout.strip() if proc.returncode == 0 else f"ERROR: {proc.stderr.strip()[:300]}"

    written = []
    for role in roles:
        extra = []
        if role == "market":
            extra = ["- Read `0_data/bars_1D.csv` and `0_data/bars_1W.csv` in the same parallel batch as this file."]
        lines = _role_header(role, settings, run, now, extra)
        ordered = sorted((i for i in items if role in i.labels), key=lambda i: _step_number(i.labels[role]))
        for item in ordered:
            lines += _section(item, role, as_of, historical, bars)
        if role == "market" and indicators is not None:
            lines += [f"## Step 4 (TradingAgents indicator values) · indicators.py", "",
                      f"Command: `python3 .github/skills/investment-debate/scripts/indicators.py --bars {rel(run)}/1_analysts/bars_1D.json "
                      f"--as-of {as_of} --tail 5` (local computation on the bars above)", ""]
            lines += ["```json", indicators, "```", ""] if not indicators.startswith("ERROR") else [f"**{indicators}**", ""]
        (data_dir / f"{role}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        written.append(f"0_data/{role}.md")

    manifest = {"run": rel(run), "symbol": settings["symbol"], "mode": settings["mode"], "as_of": as_of, "prefetched_at": now,
                "roles": roles, "calls": [{"key": i.key, "server": i.server, "tool": i.tool, "args": i.args, "for": i.labels,
                                           "status": i.status, "cache": i.cache, "fetched_at": i.fetched_at,
                                           "bytes": len(i.text or ""), "error": i.error, "skip": i.skip} for i in items]}
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = {s: sum(i.status == s for i in items) for s in ("ok", "error", "skipped")}
    return {"run": rel(run), "roles": roles, "files": written, "calls": counts,
            "served_from_cache": sum(i.cache in ("hit", "coalesced") for i in items),
            "errors": {i.key: i.error for i in items if i.status == "error"},
            "seconds": round(time.perf_counter() - started, 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, type=Path, help="run folder written by prepare_run.py")
    parser.add_argument("--roles", help="comma-separated analysts (default: the run's analysts without a bundle or report)")
    parser.add_argument("--force", action="store_true", help="fetch again even if a bundle exists")
    parser.add_argument("--today", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        summary = prefetch(args.run, args.roles.split(",") if args.roles else None, args.today, args.force)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
