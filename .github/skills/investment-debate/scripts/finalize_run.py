#!/usr/bin/env python3
"""Finalize an investment-debate run: build the complete report (Markdown and HTML), read the rating, save the
state log, and record the decision in the memory log.

Ports TradingAgents' reporting.py and report_html.py (report order, run details), agents/rating.py (rating
parser), trading_graph._log_state (full state JSON) and memory/log.py store_decision. Local file processing only.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from memory_log import DEFAULT_LOG, store  # noqa: E402

RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
REVIEW = "REVIEW"
TURN_LABELS = {
    "bull": "Bull Researcher",
    "bear": "Bear Researcher",
    "aggressive": "Aggressive Analyst",
    "conservative": "Conservative Analyst",
    "neutral": "Neutral Analyst",
}

# The decision's own rating line ("**Rating**: Buy", "- Final rating - Hold"). Other parties'
# ratings ("Consensus rating: Buy") do not match because only these qualifiers may precede it.
_RATING_LINE = re.compile(
    r"\s*(?:[-+*]\s+)?[\s*_#]*(?:(?:final|our|overall|research)\s+)*"
    r"rating[^\w:\-\u2010-\u2015]*[:\-\u2010-\u2015][\s*_]*(\w+)",
    re.IGNORECASE,
)
_RATING_SCALE = re.compile(r"rating\s*(scale|options|legend)", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})(\s)", re.MULTILINE)


def extract_rating(text: str) -> str:
    """The opening rating line wins; otherwise all rating lines must agree; otherwise REVIEW."""
    lines = [
        line for line in unicodedata.normalize("NFKC", text).splitlines()
        if line.strip() and not _RATING_SCALE.search(line)
    ]

    def rating_of(line: str) -> str | None:
        match = _RATING_LINE.match(line)
        value = match.group(1).capitalize() if match else None
        return value if value in RATINGS else None

    first = next((line for line in lines if not line.lstrip().startswith("#") or rating_of(line)), "")
    if rating_of(first):
        return rating_of(first)
    found = {value for value in map(rating_of, lines) if value}
    return found.pop() if len(found) == 1 else REVIEW


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def embed(text: str) -> str:
    """Drop the agent's own top heading and nest its other headings under the report's."""
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        text = "\n".join(lines[1:]).strip()
    return _HEADING.sub(lambda m: "#" * min(6, len(m.group(1)) + 3) + m.group(2), text)


def absent(label: str) -> str:
    return f"(No {label} output in this run: it is not available, not an empty finding.)"


def turns(folder: Path, roles: tuple[str, ...]) -> list[tuple[str, Path]]:
    found = []
    for path in sorted(folder.glob("[0-9][0-9]_*.md")):
        role = path.stem[3:]
        if role in roles:
            found.append((f"{TURN_LABELS[role]} (turn {int(path.stem[:2])})", path))
    return found


def sections(run: Path) -> list[tuple[str, list[tuple[str, Path]]]]:
    analysts, research = run / "1_analysts", run / "2_research"
    return [
        ("I. Analyst Team Reports", [
            ("Market Analyst", analysts / "market.md"),
            ("Sentiment Analyst", analysts / "sentiment.md"),
            ("News Analyst", analysts / "news.md"),
            ("Fundamentals Analyst", analysts / "fundamentals.md"),
        ]),
        ("II. Research Team Decision", [
            *turns(research, ("bull", "bear")),
            ("Research Manager", research / "manager.md"),
        ]),
        ("III. Trading Team Plan", [("Trader", run / "3_trading" / "trader.md")]),
        ("IV. Risk Management Team Decision", turns(run / "4_risk", ("aggressive", "conservative", "neutral"))),
    ]


def load_settings(run: Path) -> dict:
    try:
        return json.loads((run / "0_settings.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def settings_lines(settings: dict) -> list[str]:
    if not settings:
        return ["- Run settings: not recorded (`0_settings.json` missing)."]
    s = settings.get
    analysts = s("analysts")
    lines = [
        f"- Mode: {s('mode', 'live')}" + (f", as of {s('as_of')}" if s("as_of") else ""),
        f"- Asset type: {s('asset_type', '?')}",
        f"- Analysts: {', '.join(analysts) if isinstance(analysts, list) else analysts or '?'}",
        f"- Debate rounds: research {s('debate_rounds', '?')}, risk {s('risk_rounds', '?')}"
        + (f" (depth {s('depth')})" if s("depth") else ""),
        f"- Models: quick {s('quick_model', 'session default')}, deep {s('deep_model', 'session default')}",
        *([f"- Reasoning effort: quick {s('quick_effort', 'session default')}, deep {s('deep_effort', 'session default')}"]
          if s("quick_effort") or s("deep_effort") else []),
        f"- Output language: {s('language', 'English')}",
        f"- Outcome window: {s('holding_days', '?')} trading days vs {s('benchmark', '?')}",
        f"- Data source: {s('data_source', 'free MCP servers')}",
    ]
    if s("memory_note"):
        lines.append(f"- Memory log: {s('memory_note')}")
    return lines


def build_report(run: Path, symbol: str, run_date: str, rating: str, settings: dict) -> str:
    out = [
        f"# {symbol}: investment research report ({run_date})",
        "",
        f"> Research rating: **{rating}**. A research conclusion for human review, not an order"
        " or personal advice. Nothing was executed. Data source: free MCP servers (Yahoo Finance, TradingMCP, Tapetide, local technicals).",
        "",
        *settings_lines(settings),
        "",
        "## Conclusion: Portfolio Manager",
        "",
        embed(read(run / "5_portfolio" / "decision.md")) or absent("Portfolio Manager"),
        "",
    ]
    for heading, parts in sections(run):
        out += [f"## {heading}", ""]
        if not parts:
            out += ["(No debate turns in this run.)", ""]
        for label, path in parts:
            out += [f"### {label}", "", embed(read(path)) or absent(label), ""]
    out += ["## Appendix: Run Inputs", ""]
    for label, name in (
        ("Instrument", "0_instrument.md"),
        ("Portfolio Context", "0_portfolio.md"),
        ("Past Lessons", "0_past_context.md"),
    ):
        out += [f"### {label}", "", embed(read(run / name)) or absent(label), ""]
    return "\n".join(out).rstrip() + "\n"


def build_state(run: Path, symbol: str, run_date: str, rating: str, settings: dict) -> dict:
    """The run in TradingAgents' AgentState shape, for the full-state log."""
    def joined(paths: list[tuple[str, Path]]) -> str:
        return "\n\n".join(f"{label}: {read(path)}" for label, path in paths)
    research, risk = run / "2_research", run / "4_risk"
    return {
        "company_of_interest": symbol,
        "trade_date": settings.get("as_of") or run_date,
        "asset_type": settings.get("asset_type", "stock"),
        "instrument_context": read(run / "0_instrument.md"),
        "portfolio_context": read(run / "0_portfolio.md"),
        "past_context": read(run / "0_past_context.md"),
        "market_report": read(run / "1_analysts" / "market.md"),
        "sentiment_report": read(run / "1_analysts" / "sentiment.md"),
        "news_report": read(run / "1_analysts" / "news.md"),
        "fundamentals_report": read(run / "1_analysts" / "fundamentals.md"),
        "investment_debate_state": {
            "bull_history": joined(turns(research, ("bull",))),
            "bear_history": joined(turns(research, ("bear",))),
            "history": joined(turns(research, ("bull", "bear"))),
            "count": len(turns(research, ("bull", "bear"))),
        },
        "investment_plan": read(research / "manager.md"),
        "trader_investment_plan": read(run / "3_trading" / "trader.md"),
        "risk_debate_state": {
            "aggressive_history": joined(turns(risk, ("aggressive",))),
            "conservative_history": joined(turns(risk, ("conservative",))),
            "neutral_history": joined(turns(risk, ("neutral",))),
            "history": joined(turns(risk, ("aggressive", "conservative", "neutral"))),
            "count": len(turns(risk, ("aggressive", "conservative", "neutral"))),
        },
        "final_trade_decision": read(run / "5_portfolio" / "decision.md"),
        "final_rating": rating,
        "memory_note": settings.get("memory_note", ""),
        "settings": settings,
    }


# --- Minimal Markdown to HTML (headings, paragraphs, lists, tables, quotes, code, emphasis, links) ---

def _inline(text: str) -> str:
    codes: list[str] = []

    def keep_code(match: re.Match) -> str:
        codes.append(match.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", keep_code, text)
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(((?:https?://|\.{0,2}/|#)[^)\s]*)\)",
                  lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
    return re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{html.escape(codes[int(m.group(1))])}</code>", text)


def _cells(row: str) -> list[str]:
    row = row.strip().replace("\\|", "\x01")
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") else row
    return [c.strip().replace("\x01", "|") for c in row.split("|")]


def markdown_to_html(text: str) -> str:
    lines, out, para, i = text.splitlines(), [], [], 0
    list_item = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)")

    def flush() -> None:
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            flush()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(block))}</code></pre>")
        elif not stripped:
            flush()
        elif heading := re.match(r"(#{1,6})\s+(.*)", stripped):
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush()
            out.append("<hr>")
        elif stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{3,}", lines[i + 1].strip()):
            flush()
            head = _cells(stripped)
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            i -= 1
            out.append('<div class="table"><table><thead><tr>'
                       + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows)
                       + "</tbody></table></div>")
        elif list_item.match(lines[i]):
            flush()
            ordered = list_item.match(lines[i]).group(1)[0].isdigit()
            items: list[str] = []
            while i < len(lines) and lines[i].strip():
                item = list_item.match(lines[i])
                if item:
                    items.append(item.group(2))
                elif items:
                    items[-1] += " " + lines[i].strip()
                i += 1
            i -= 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
        elif stripped.startswith(">"):
            flush()
            quoted = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quoted.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            i -= 1
            out.append(f"<blockquote>{markdown_to_html(chr(10).join(quoted))}</blockquote>")
        else:
            para.append(stripped)
        i += 1
    flush()
    return "\n".join(out)


TONE = {"Buy": "#1a7f37", "Overweight": "#2da44e", "Hold": "#6e7781", "Underweight": "#cf6e19", "Sell": "#cf222e"}


def build_html(markdown: str, symbol: str, rating: str) -> str:
    color = TONE.get(rating, "#9a6700")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(symbol)} research report</title>
<style>
body {{ font: 15px/1.55 system-ui, -apple-system, Segoe UI, sans-serif; max-width: 980px; margin: 2rem auto;
       padding: 0 1rem; color: #1f2328; }}
h1 {{ border-bottom: 1px solid #d0d7de; padding-bottom: .3rem; }}
h2 {{ margin-top: 2.2rem; border-bottom: 1px solid #eaeef2; padding-bottom: .2rem; }}
.rating {{ display: inline-block; padding: .15rem .7rem; border-radius: 1rem; color: #fff; background: {color};
          font-weight: 600; }}
.table {{ overflow-x: auto; }} table {{ border-collapse: collapse; margin: .8rem 0; }}
th, td {{ border: 1px solid #d0d7de; padding: .3rem .6rem; vertical-align: top; }} th {{ background: #f6f8fa; }}
blockquote {{ margin: .8rem 0; padding: .1rem 1rem; border-left: 4px solid #d0d7de; color: #57606a; }}
code {{ background: #f6f8fa; padding: .1rem .3rem; border-radius: 4px; }} pre code {{ display: block; padding: .8rem; overflow-x: auto; }}
</style></head><body>
<p><span class="rating">{html.escape(rating)}</span> &nbsp;Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
{markdown_to_html(markdown)}
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", required=True, type=Path, help="run folder, e.g. reports/NSE_RELIANCE/2026-10-29")
    parser.add_argument("--symbol", required=True, help="EXCHANGE:TICKER, e.g. NSE:RELIANCE")
    parser.add_argument("--date", required=True, help="analysis date, YYYY-MM-DD")
    parser.add_argument("--memory-log", type=Path, default=DEFAULT_LOG,
                        help="decision log (backtests pass their own log)")
    parser.add_argument("--no-html", action="store_true", help="skip complete_report.html")
    args = parser.parse_args(argv)

    if args.symbol.count(":") != 1 or any(ch.isspace() for ch in args.symbol):
        parser.error("--symbol must look like EXCHANGE:TICKER")
    try:
        date.fromisoformat(args.date)
    except ValueError:
        parser.error("--date must be YYYY-MM-DD")
    if not args.run.is_dir():
        parser.error(f"run folder not found: {args.run}")

    settings = load_settings(args.run)
    decision = read(args.run / "5_portfolio" / "decision.md")
    rating = extract_rating(decision) if decision else REVIEW
    markdown = build_report(args.run, args.symbol, args.date, rating, settings)
    report = args.run / "complete_report.md"
    report.write_text(markdown, encoding="utf-8")
    html_path = args.run / "complete_report.html"
    if args.no_html:
        html_path.unlink(missing_ok=True)
    else:
        html_path.write_text(build_html(markdown, args.symbol, rating), encoding="utf-8")
    state_path = args.run / "state.json"
    state_path.write_text(json.dumps(build_state(args.run, args.symbol, args.date, rating, settings),
                                     indent=2, ensure_ascii=False), encoding="utf-8")

    if decision:
        added = store(args.memory_log, args.symbol, args.date, rating, decision)
        memory = "added" if added else "already present"
    else:
        memory = "skipped: no portfolio-manager decision"
    print(json.dumps({
        "rating": rating,
        "report": str(report),
        "html": None if args.no_html else str(html_path),
        "state": str(state_path),
        "memory_log": str(args.memory_log),
        "memory_entry": memory,
    }, indent=2))
    return 0 if decision else 1


if __name__ == "__main__":
    raise SystemExit(main())
