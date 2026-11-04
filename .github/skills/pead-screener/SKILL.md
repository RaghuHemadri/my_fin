---
name: pead-screener
description: Screen post-earnings gap-up stocks for PEAD (Post-Earnings Announcement Drift) patterns. Analyzes weekly candle formation to detect red candle pullbacks and breakout signals. Supports two input modes - FMP earnings calendar (Mode A) or earnings-trade-analyzer JSON output (Mode B). Use when user asks about PEAD screening, post-earnings drift, earnings gap follow-through, red candle breakout patterns, or weekly earnings momentum setups.
---

<!-- tradingview-adapter:start -->
> [!IMPORTANT]
> **Workspace adapter.** Market data comes only from this workspace's MCP data servers (`tradingmcp`, `yahoo-finance`, `technicals` and `tapetide`). Tool names below are canonical TradingView MCP names; `.github/tradingview-adapter.md` maps each one to the configured servers. `tools/install_skills.py` added this block; the upstream text below is unchanged except that skill paths point to `.github/skills/`.
>
> - **Status:** Adapted. Get its data from the workspace MCP servers as described here, not from the upstream source.
> - **Upstream data:** FMP API.
> - **In this workspace:** Post-earnings gap-ups from `run_screener` and weekly bars from `get_ohlcv`; report dates from `get_earnings_calendar`. The script accepts `--candidates-json`.
> - **Scripts:** Do not run `scripts/_fmp_compat.py`, `scripts/fmp_client.py` and `scripts/screen_pead.py`; they call FMP. The other bundled scripts are local and may run.
> - **Offline inputs:** some of those scripts can read local files instead (`--candidates-json`). You may run them that way with data you fetched from the MCP servers, with any FMP or Alpaca keys unset. Stop if a script still tries to reach the network.
> - **Always:** follow `.github/tradingview-adapter.md`. Never invent data. Never place orders. Unless the skill names another location, write outputs under `reports/pead-screener/`.
<!-- tradingview-adapter:end -->

# PEAD Screener - Post-Earnings Announcement Drift

Screen post-earnings gap-up stocks for PEAD (Post-Earnings Announcement Drift) patterns using weekly candle analysis to detect red candle pullbacks and breakout signals.

## When to Use

- User asks for PEAD screening or post-earnings drift analysis
- User wants to find earnings gap-up stocks with follow-through potential
- User requests red candle breakout patterns after earnings
- User asks for weekly earnings momentum setups
- User provides earnings-trade-analyzer JSON output for further screening

## Prerequisites

- FMP API key (set `FMP_API_KEY` environment variable or pass `--api-key`)
  ```bash
  export FMP_API_KEY=your_api_key_here
  ```
- Free tier (250 calls/day) is sufficient for default screening
- For Mode B: earnings-trade-analyzer JSON output file with schema_version "1.0"

## Workflow

### Step 1: Prepare and Execute Screening

Run the PEAD screener script in one of two modes:

**Mode A (FMP earnings calendar):**
```bash
# Default: last 14 days of earnings, 5-week monitoring window
python3 .github/skills/pead-screener/scripts/screen_pead.py --output-dir reports/

# Custom parameters
python3 .github/skills/pead-screener/scripts/screen_pead.py \
  --lookback-days 21 \
  --watch-weeks 6 \
  --min-gap 5.0 \
  --min-market-cap 1000000000 \
  --output-dir reports/
```

**Mode B (earnings-trade-analyzer JSON input):**
```bash
# From earnings-trade-analyzer output
python3 .github/skills/pead-screener/scripts/screen_pead.py \
  --candidates-json reports/earnings_trade_analyzer_YYYY-MM-DD_HHMMSS.json \
  --min-grade B \
  --output-dir reports/
```

**Scheduled US-equity routine pitfall:** Prefer Mode B for pre-market / US-equity cron briefs after running `earnings-trade-analyzer`. Mode A can pull the global FMP earnings calendar, spend the API budget on non-US symbols, and return weak/non-actionable foreign listings before reaching the intended US watchlist. If Mode A is used anyway and the script reports budget trimming or non-US symbols, mark PEAD output as degraded and treat it as manual-review only rather than a clean candidate source.

### Step 2: Review Results

1. Read the generated JSON and Markdown reports
2. Load `references/pead_strategy.md` for PEAD theory and pattern context
3. Load `references/entry_exit_rules.md` for trade management rules

### Step 3: Present Analysis

For each candidate, present:
- Stage classification (MONITORING, SIGNAL_READY, BREAKOUT, EXPIRED)
- Weekly candle pattern details (red candle location, breakout status)
- Composite score and rating
- Trade setup: entry, stop-loss, target, risk/reward ratio
- Liquidity metrics (ADV20, average volume)

### Step 4: Provide Actionable Guidance

Based on stages and ratings:
- **BREAKOUT + Strong Setup (85+):** High-conviction PEAD trade, full position size
- **BREAKOUT + Good Setup (70-84):** Solid PEAD setup, standard position size
- **SIGNAL_READY:** Red candle formed, set alert for breakout above red candle high
- **MONITORING:** Post-earnings, no red candle yet, add to watchlist
- **EXPIRED:** Beyond monitoring window, remove from watchlist

## Output

- `pead_screener_YYYY-MM-DD_HHMMSS.json` - Structured results with stage classification
- `pead_screener_YYYY-MM-DD_HHMMSS.md` - Human-readable report grouped by stage

### Unknown earnings timing

FMP does not confirm a bmo/amc session for every earnings row; unconfirmed
rows carry `earnings_timing: "unknown"` in Mode A and the price gap
calculation assumes the AMC window as a fallback. The Mode A report shows
`timing_unknown_count` out of `timing_candidates_total` so this assumption
stays visible (Mode B reports `n/a` since timing is inherited from the input
JSON). `timing_candidates_total` is the post-budget-trim population that was
actually analyzed, not the raw earnings-calendar row count.

## Resources

- `references/pead_strategy.md` - PEAD theory and weekly candle approach
- `references/entry_exit_rules.md` - Entry, exit, and position sizing rules
