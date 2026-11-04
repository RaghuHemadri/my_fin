---
name: investment-debate
description: >-
  TradingAgents-style multi-agent research on one Indian or US instrument (stock, ETF, index, crypto, FX or futures).
  Selectable market, sentiment, news and fundamentals analysts feed a bull-vs-bear debate, a research-manager plan, a
  trader proposal, an aggressive/conservative/neutral risk debate and a portfolio-manager research rating
  (Buy/Overweight/Hold/Underweight/Sell). Includes research depth, output language, historical as-of runs, a
  decision memory log with reflection, and backtests over tickers and dates. Use to evaluate, debate or deep-dive a
  holding or opportunity, or to backtest the process. Data comes only from the free MCP servers in .github/copilot-instructions.md (technicals, Yahoo Finance, TradingMCP, Tapetide). Never places trades.
argument-hint: "<EXCHANGE:TICKER> [depth=shallow|medium|deep] [analysts=...] [date=YYYY-MM-DD] [language=...] | backtest tickers=... start=... end=..."
---

# Investment Debate: the TradingAgents workflow for Copilot

This skill ports the [TradingAgents](https://github.com/TauricResearch/TradingAgents) graph (Apache-2.0) to Copilot. It keeps:
- the roles and their order
- the debate rules and the 5-tier rating
- the isolation of inputs between roles
- the selectable analysts, research depth and output language
- historical as-of runs
- the memory log with settlement and reflection
- the backtest

LangGraph state becomes files in a run folder, and each role runs as a custom agent from `.github/agents/`.

## Graph

```text
 Setup (identity, settings, portfolio, memory)
   │
   ├─► ta-market-analyst ───────┐
   ├─► ta-sentiment-analyst ────┤  selected analysts, in parallel
   ├─► ta-news-analyst ─────────┤
   └─► ta-fundamentals-analyst ─┘
                                 ▼
   ta-bull-researcher ⇄ ta-bear-researcher    2 × debate_rounds turns, bull opens
                                 ▼
   ta-research-manager                         investment plan (5-tier)
                                 ▼
   ta-trader                                   transaction proposal (hypothetical)
                                 ▼
   ta-aggressive-risk-analyst → ta-conservative-risk-analyst → ta-neutral-risk-analyst
                                               3 × risk_rounds turns
                                 ▼
   ta-portfolio-manager                        final research rating
                                 ▼
   Finalize                                    report (Markdown, HTML), state log, memory-log entry
```

## Settings

The user can set any of these in the request, for example `NSE:TCS depth=medium language=Hindi`.

| Setting | Default | TradingAgents equivalent |
| --- | --- | --- |
| `analysts` | `market,sentiment,news,fundamentals` (crypto, FX and futures drop `fundamentals`) | `selected_analysts` |
| `depth` | `shallow` | research depth: `shallow` = 1, `medium` = 3, `deep` = 5 rounds, for both debates |
| `debate_rounds`, `risk_rounds` | from `depth` | `max_debate_rounds`, `max_risk_discuss_rounds` |
| `language` | English | `output_language` (labelled lines stay in English) |
| `date` | today | `trade_date`. A date earlier than today makes the run historical (see below). |
| `asset_type` | from `quoteType` in `yahoo-finance` `get_stock_info`: stock, fund/ETF, index, crypto, forex, futures | `asset_type` (stock or crypto) |
| `quick_model`, `deep_model` | the session model | `quick_think_llm` and `deep_think_llm` (see "Delegation") |
| `holding_days` | 20 trading days | `holding_period_days` (5 there; 20 suits an investing horizon) |
| `benchmark` | Yahoo tickers: US listings `^GSPC`, NSE `^NSEI` (NIFTY 50), BSE `^BSESN` (SENSEX), otherwise the home index | `benchmark_ticker`, `benchmark_map` |
| `html` | true | HTML report |
| `memory_max_entries` | none | `memory_log_max_entries` |
| `fresh` | false | `clear_checkpoints`: start again instead of resuming |
| Tool budget per analyst | 15 MCP calls (12 for sentiment). `tapetide` caps: news 4, fundamentals 3, sentiment 2, setup 1 (free tier: 50 calls a day) | `max_tool_rounds` = 20 |
| Memory log | `memory/decision-log.md` | `memory_log_path` |
| Reports | `reports/<SAFE_SYMBOL>/<as_of>/` | `results_dir` |
| Portfolio | `portfolio/holdings.csv` and `portfolio/cash.csv` | portfolio context (positions, cash, currency) |

A medium or deep run costs much more: each extra round adds 2 debate calls and 3 risk calls. Before starting a run at that depth, tell the user the approximate number of agent calls.

## Step 0: Set up the run

1. **Instrument.** Resolve the request to `EXCHANGE:TICKER`: for an Indian name use `tapetide` `search_stocks`; for a US name, or if Tapetide is unavailable, try the likely Yahoo ticker. Then confirm the description, type, exchange, currency, sector and industry with `yahoo-finance` `get_stock_info` (Yahoo ticker such as `LODHA.NS`), and the live price with `tradingmcp` `stock_prices` (`NSE:LODHA`). Never guess: if neither confirms the symbol, ask the user. If the match is ambiguous, ask the user, or choose the primary listing and say so. Set `asset_type` from the symbol type.
2. **Mode.** Set `as_of` to the `date` setting, or today. If `as_of` is before today, the mode is `historical`, and every role follows [point-in-time.md](./references/point-in-time.md). A date in the future is an error.
3. **Run folder.** Set `SAFE_SYMBOL` to the symbol with `:` replaced by `_`, and `RUN` to `reports/<SAFE_SYMBOL>/<as_of>`. The stage files are the checkpoint:
   - If `RUN` already has stage files, resume from the first missing stage.
   - If `fresh` is set, use `<as_of>-2`, `<as_of>-3` and so on instead.
4. **`RUN/0_settings.json`.** Write all the settings above as JSON with these keys:
   - `symbol`, `run_date` (today), `mode` (live or historical), `as_of`, `asset_type`
   - `analysts` (a list), `depth`, `debate_rounds`, `risk_rounds`, `language`
   - `quick_model`, `deep_model`, `holding_days`, `benchmark`
   - `memory_log`, `memory_note`, `data_source` (`"free MCP servers (technicals, Yahoo Finance, TradingMCP, Tapetide)"`)
5. **`RUN/0_instrument.md`**, read by every agent. Write these lines:
   - `Symbol`, `Company`, `Type`, `Asset type`, `Exchange`, `Currency`, `Sector / Industry`
   - `Analysis date`, `Mode: live` (or `Mode: historical as of <as_of>`), `Output language`
   - `Data source: free MCP servers (technicals, Yahoo Finance, TradingMCP, Tapetide)`
   - `Yahoo ticker: <e.g. LODHA.NS>` and, for Indian companies, `Tapetide symbol: <e.g. LODHA>`, so agents need not convert
   - This identity rule: "Analyze exactly this instrument and refer to it by this exact symbol. Do not substitute a different company unless a tool result disproves this identity."
6. **`RUN/0_portfolio.md`**, read only by the trader, the risk analysts and the portfolio manager. Build it from `portfolio/holdings.csv` and `portfolio/cash.csv`:
   - The current position in this symbol (quantity, average cost, currency), or "No current position in <symbol>".
   - Every holding with its latest price from one `tradingmcp` `stock_prices` call with all symbols comma-separated (in historical mode, the close on `as_of` from `yahoo-finance` `get_historical_stock_prices`), its market value in its own currency, and its weight. Convert with a dated USD/INR rate (`INR=X`, INR per USD, via `get_historical_stock_prices` or `tradingmcp` `yahoo_price`), and state the rate and its date.
   - Concentration: the largest names, and weights by sector, country and currency.
   - Cash for each currency in `cash.csv`, or `Cash: not provided`.
   - If there are no holdings, write only: "Portfolio context: not provided. Do not assume a flat book."
   - In historical mode, state that the holdings are today's book, used as a standing book.
7. **Memory step.** Settle any pending decisions whose window has passed, as described in [memory-log.md](./references/memory-log.md). Then write `RUN/0_past_context.md` with `memory_log.py context --as-of <as_of>`. Only the portfolio manager reads it.

## Step 1: Analyst team

Delegate the selected analysts in parallel if the harness supports it. Pass each one the symbol, `as_of`, the mode, the language, `RUN` and its output path.

| Agent | Output |
| --- | --- |
| `ta-market-analyst` | `RUN/1_analysts/market.md` |
| `ta-sentiment-analyst` | `RUN/1_analysts/sentiment.md` |
| `ta-news-analyst` | `RUN/1_analysts/news.md` |
| `ta-fundamentals-analyst` | `RUN/1_analysts/fundamentals.md` |

If an analyst was not selected, or fails twice, write this to its file: `(No <name> report in this run: it is not available, not an empty finding.)`

## Step 2: Research debate

This step follows `should_continue_debate` in TradingAgents.

```text
count = 0
while count < 2 × debate_rounds:
    speaker = ta-bull-researcher if count is even else ta-bear-researcher
    NN = count + 1, zero-padded            # 01, 02, …
    output = RUN/2_research/<NN>_bull.md or <NN>_bear.md
    inputs = 0_instrument.md, the analyst reports, all earlier turn files in order
    count += 1
```

Then run `ta-research-manager`. Its inputs are `0_instrument.md` and all turn files. Its output is `RUN/2_research/manager.md`.

## Step 3: Trader

Run `ta-trader`. Its inputs are `0_instrument.md`, `0_portfolio.md`, `1_analysts/market.md` and `2_research/manager.md`. Its output is `RUN/3_trading/trader.md`.

## Step 4: Risk debate and portfolio manager

This step follows `should_continue_risk_analysis` in TradingAgents.

```text
order = [ta-aggressive-risk-analyst, ta-conservative-risk-analyst, ta-neutral-risk-analyst]
count = 0
while count < 3 × risk_rounds:
    speaker = order[count mod 3]
    NN = count + 1, zero-padded
    output = RUN/4_risk/<NN>_aggressive.md, <NN>_conservative.md or <NN>_neutral.md
    inputs = 0_instrument.md, 0_portfolio.md, the analyst reports, 3_trading/trader.md, all earlier risk turns in order
    count += 1
```

Then run `ta-portfolio-manager`. Its inputs are `0_instrument.md`, `0_portfolio.md`, `0_past_context.md`, `2_research/manager.md`, `3_trading/trader.md` and all risk turns. Its output is `RUN/5_portfolio/decision.md`.

## Step 5: Finalize

Run:

```bash
python3 .github/skills/investment-debate/scripts/finalize_run.py --run <RUN> --symbol <SYMBOL> --date <as_of> [--no-html] [--memory-log <log>]
```

The script does four things:
- It writes `complete_report.md` (TradingAgents section order, with a run-settings header) and `complete_report.html`.
- It writes `state.json`, the full state in TradingAgents' shape.
- It reads the rating from the decision. A missing or ambiguous rating becomes `REVIEW`.
- It appends a pending entry to the memory log, once per symbol and date.

It processes local files only. Then reply to the user with:
- the rating and confidence
- 3–5 deciding reasons
- the main risks
- the data gaps, including Indian regulatory coverage where relevant
- links to `complete_report.md` and `complete_report.html`

State that the rating is a research conclusion, not an order.

## Other commands

- **Backtest:** `backtest tickers=<a,b> start=<date> end=<date> [every=7]`. Follow [backtest.md](./references/backtest.md).
- **Track record:** run `python3 .github/skills/investment-debate/scripts/memory_log.py summarize` to show hit rate and mean alpha by rating.
- **Settle only:** run Step 0, item 7, without a new debate.

## Delegation protocol

- **Tool.** Run each role as a subagent with its custom agent. In Copilot CLI and the VS Code Copilot harness, delegate with the task tool. In the VS Code Local harness, use `runSubagent`.
- **What to pass.** Pass the symbol, `as_of`, the mode, the language, `RUN`, the exact input file paths, the output file path and, for debate turns, `NN`. Pass paths, not report contents. Never edit an agent's output file.
- **Models.** TradingAgents runs the two judges (`ta-research-manager`, `ta-portfolio-manager`) on a deep-thinking model and the other roles on a quick one. If `deep_model` or `quick_model` is set and the delegation tool accepts a model, pass it. Otherwise every role uses the session model; record that in `0_settings.json`.
- **Isolation**, as in TradingAgents:
  - Analysts see only the instrument.
  - The bull and bear researchers and the research manager never see holdings.
  - Only the portfolio manager sees past lessons.
- **Fallback.** If custom-agent subagents are not available, perform each role yourself, strictly in graph order. For each role:
  1. Read `.github/agents/<agent>.agent.md`.
  2. Read only that role's inputs, plus the MCP data servers for the analysts.
  3. Write the role's output file before starting the next role.
- **Scale.** A shallow run takes about 12 agent calls. For a whole portfolio, debate only the names that matter most, and list the names you skipped.

## Not reproduced from TradingAgents

- **LangGraph runtime and checkpointer.** Replaced by stage files.
- **The 20+ LLM provider clients and ChatGPT-plan login.** Copilot is the model provider.
- **The CLI's live token and tool-call statistics.**
- **Data vendors** (Alpha Vantage, SEC EDGAR, FRED, Polymarket, StockTwits, Reddit, Jev post screening). The free MCP servers replace them: Yahoo Finance (as upstream, via MCP), Marketaux news through TradingMCP, and Tapetide for Indian filings and insider deals. There is no economic calendar, no social-media feed and no prediction-market data; the agents report these as unavailable.
