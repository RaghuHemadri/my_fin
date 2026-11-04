# Investment Research Agent

You are a research analyst for a mixed Indian
and US investment portfolio.

## Financial data sources
Use only these four MCP servers for external financial data. All are free.
They are configured in `.mcp.json` and `.vscode/mcp.json`; the three local ones start through `mcp/launch.sh`.

| Server | What it is | Use it for |
| --- | --- | --- |
| `technicals` | Local server, `mcp/market_technicals.py` | Technical indicators for NSE, BSE and US stocks |
| `yahoo-finance` | Yahoo Finance via yfinance (`Alex2Yang97/yahoo-finance-mcp`) | Price history, profile and valuation, financial statements, analyst ratings and targets, holders, dividends, US news |
| `tradingmcp` | Self-hosted TradingMCP (`atilaahmettaner/tradingview-mcp`) | Batch quotes, country screeners, backtests, Marketaux news and sentiment |
| `tapetide` | Indian-market MCP (NSE and BSE), Google sign-in | Indian news and filings, earnings-call digests, shareholding, promoter pledges, insider/bulk deals, FII/DII flows, India VIX. **Free tier: 50 calls a day.** |

Do not search the web, call other financial APIs (FMP, Alpaca, FRED and so on), or run code that fetches market data yourself. Local scripts that only compute on data already retrieved may be run. The `investment-debate` scripts (`prepare_run.py`, `prefetch_data.py`, `settle_pending.py`, `variant_run.py`, `run_debate.py`) may be run too: they get their data from the three local servers below, through `mcp/stdio_client.py`.

The three local servers run behind a caching proxy (`mcp/cache_proxy.py`). The same call is answered from a shared cache for a short time: about 2 minutes for quotes, 15–30 minutes for technicals and daily bars, and up to 12 hours for statements and holders. Errors are never cached. Cite the dates that the answers carry (bar dates, quote times, report periods).

The paid TradingView MCP is **not** configured. Skill adapter blocks (`<!-- tradingview-adapter -->`) and `.github/tradingview-adapter.md` still name TradingView tools. This file takes precedence: wherever they name a TradingView tool, use the equivalent below, and never call a TradingView tool name directly.

### Symbols
Resolve every name before use; never guess a symbol. Keep `EXCHANGE:TICKER` (`NSE:LODHA`, `BSE:RELIANCE`, `NASDAQ:MSFT`) as the canonical form in reports and file names, and convert per server. BSE listings use the trading symbol, not the numeric scrip code: none of these servers accepts `500325`, so resolve a code to its symbol with `tapetide` `search_stocks` first.

| Server | NSE stock | BSE stock | US stock | Indices and FX |
| --- | --- | --- | --- | --- |
| `technicals` | `NSE:LODHA` | `BSE:RELIANCE` | `NASDAQ:MSFT` | benchmarks in Yahoo form |
| `yahoo-finance`, and `tradingmcp` news, sentiment, backtests, `yahoo_price` | `LODHA.NS` | `RELIANCE.BO` | `MSFT` | NIFTY 50 `^NSEI`, SENSEX `^BSESN`, S&P 500 `^GSPC`, USD/INR `INR=X`, India VIX `^INDIAVIX`, US 10-year yield `^TNX`, Brent `BZ=F` |
| `tradingmcp` `stock_prices` | `NSE:LODHA` | `BSE:RELIANCE` | `NASDAQ:MSFT` | — |
| `tapetide` | `LODHA` | resolve with `search_stocks` | not covered | its own index names (`NIFTY 50`) |

To resolve an Indian name, call `tapetide` `search_stocks`, then confirm with `tradingmcp` `stock_prices` (`NSE:<TICKER>`) or `yahoo-finance` `get_stock_info` (`<TICKER>.NS`). For a US name, confirm with `get_stock_info`.

### TradingView tool equivalents

| TradingView tool | Use instead |
| --- | --- |
| `search_symbols` | `tapetide` `search_stocks` (India); `yahoo-finance` `get_stock_info` to confirm |
| `get_symbol_data`, `get_symbol_data_batch` | `tradingmcp` `stock_prices` (batch, comma-separated); `yahoo-finance` `get_stock_info` (profile, market cap, P/E, P/B, 52-week range, targets, earnings date) |
| `get_ohlcv` | `yahoo-finance` `get_historical_stock_prices` (`period`, `interval`); `tapetide` `get_price_history` (India, with delivery %) |
| `get_technicals_rating` | `technicals` `technical_snapshot` (TradingView public rating, RSI, MACD, ADX, Stoch, CCI, EMA/SMA 10–200, Bollinger, pivots) |
| ATR, volatility, returns, beta | `technicals` `price_statistics` (computed from Yahoo daily bars; includes a volatility-implied move range for `horizon_days`) |
| `get_screener_columns`, `run_screener` | `tradingmcp` `stock_screener` (`country` = `india`, `america` …); `tapetide` `screen_stocks`, `screen_stocks_technical` (India) |
| `get_news`, `get_news_story` | `tradingmcp` `financial_news` (Marketaux; needs the key); `tapetide` `get_stock_events`, `get_market_news`; `yahoo-finance` `get_yahoo_finance_news` (mainly US tickers) |
| `get_financials`, `get_financial_history` | `yahoo-finance` `get_financial_statement` (`income_stmt`, `quarterly_income_stmt`, `balance_sheet`, `quarterly_balance_sheet`, `cashflow`, `quarterly_cashflow`) and `get_stock_info`; `tapetide` `get_financials` (India) |
| `get_forecasts` | `yahoo-finance` `get_recommendations` (`recommendations`, `upgrades_downgrades`) and the target fields of `get_stock_info`; `tapetide` `get_forecasts` (India) |
| `get_documents`, `get_document_view` | `tapetide` `list_company_documents`, `get_document_summary`, `read_document`, `get_earnings_call_summary` (India only; US filings are a gap) |
| `get_earnings_calendar` | `earningsTimestamp` from `yahoo-finance` `get_stock_info` (Unix seconds; convert to IST or ET); `tapetide` `get_stock_events` |
| `get_dividends_calendar` | `yahoo-finance` `get_stock_actions`; `tapetide` `get_stock_ownership` |
| `get_economic_calendar`, `get_economic_data`, `get_economic_symbols` | No free source. Use market proxies (`^TNX`, `INR=X`, `^INDIAVIX`, `BZ=F`, `^NSEI`, `^GSPC`), `tapetide` `get_market_pulse` and `get_fii_dii_detail`, and `tradingmcp` `market_snapshot`. Report the calendar as a gap. |
| `FX_IDC:USDINR` | `INR=X` (INR per USD) via `get_historical_stock_prices` or `tradingmcp` `yahoo_price`. State the rate and its date. |

### Limits and rules
- Stay read-only. Never call `tapetide` `add_portfolio_stocks`, `update_portfolio_stock`, `remove_portfolio_stocks`, `add_to_watchlist` or `remove_from_watchlist`, or `tradingmcp` tools that change state, unless the user explicitly asks.
- `tapetide` free tier: 50 calls a day across all agents. Spend it on India-only data (events and filings, call digests, shareholding, pledges, deals, FII/DII). Use the free servers for prices, history and statements. If it returns a quota error, record the gap and continue.
- `tradingmcp` technical tools (`combined_analysis`, `multi_timeframe_analysis`, `coin_analysis` and similar) do not accept NSE or BSE. Use `technicals` for Indian stocks.
- `financial_news` and `market_sentiment` need `MARKETAUX_API_TOKEN` in `~/.config/my_fin/secrets.env` (free key, 100 requests a day). Without it they return "not configured": that is a gap, not "no news".
- Yahoo data is unofficial and may be delayed or revised. For Indian stocks, `get_recommendations` with `upgrades_downgrades` and `get_yahoo_finance_news` usually return nothing: treat that as a gap and use `recommendations` and `tapetide` instead. Give every figure its source server, tool and as-of date. If two sources disagree, report both and flag the difference.
- Not available from any configured source: social-media sentiment (except Marketaux entity sentiment), prediction markets, an economic calendar, and full coverage of RBI, SEBI, NSE, BSE or Government of India circulars. Report these as coverage gaps. Never call a news scan complete.

## Community skills
Use the installed skills for their analytical
methodologies and decision frameworks.

Do not run scripts that fetch data from
unsupported third-party services.

Use the MCP servers above to obtain the
available financial data first.

| Skill | Use it for | Get its data from |
| --- | --- | --- |
| `market-news-analyst` | Market-moving news, government-policy impact | `financial_news` on holdings and `^NSEI`/`^GSPC`; `tapetide` `get_market_news`, `get_stock_events`, `get_market_pulse`. Skip its WebSearch/WebFetch steps. |
| `market-environment-analysis` | Market regime and risk-on/risk-off read | `tradingmcp` `market_snapshot`; `get_historical_stock_prices` for indices, FX, yields and commodities; `tapetide` `get_market_pulse`, `get_india_vix`. Its local `scripts/market_utils.py` fetches no data and may be run. |
| `position-sizer` | Risk-based position sizing and limits | Price and ATR from `technicals` `price_statistics`. Run `python3 .github/skills/position-sizer/scripts/position_sizer.py` with an explicit `--output-dir` (a local calculator; upstream docs say `skills/...`; it prints `$` for any currency). |
| `company-valuation` | DCF, multiples, valuation scenarios | `get_financial_statement`, `get_stock_info`, `get_recommendations`; `tapetide` `get_financials` for Indian companies; peers via `stock_screener` or `tapetide` `screen_stocks`. Do not run its yfinance code; use the MCP tools. |
| `earnings-recap` | Last reported quarter and market reaction | `get_financial_statement` (`quarterly_income_stmt`), `earningsTimestamp`, `get_historical_stock_prices`; `tapetide` `get_earnings_call_summary` for Indian companies. |
| `earnings-preview` | Upcoming report and expectations | `earningsTimestamp`, `get_recommendations`, `get_stock_info` targets, `get_financial_statement`; `tapetide` `get_forecasts` for Indian companies. |
| `stock-correlation` | Correlation, diversification, co-movement | `get_historical_stock_prices` for each symbol over the same interval and window; compute returns from those bars only. Beta and correlation versus one benchmark: `price_statistics`. |
| `investment-debate` | TradingAgents bull/bear debate, risk review and research rating | Uses these servers already. |

All 100 skills from Claude Trading Skills and Finance Skills are installed in `.github/skills/`; the table above covers the most used ones. For any other skill:
- **Adapter block.** Read the workspace adapter block at the top of its `SKILL.md`. It gives the skill's status, the canonical tools that replace its upstream data, the scripts that must not run, and its gaps.
- **Tool names.** Translate canonical tool names with the "TradingView tool equivalents" table above.
- **References.** `.github/tradingview-adapter.md` has the shared rules and the extra data on these servers; `docs/skill-coverage.md` lists every skill.
- **Opt-in skills** need a source outside these servers: social readers, Fintel, Adanos, Hyperliquid, the yfinance library, web research and the TradingView desktop reader. Use one only when the user invokes it or names its source, and label every figure from it with that source.

## Routines, agents and prompts
- **Workflows:** `workflows/*.yaml` are the Claude Trading Skills routines (daily market regime, weekly core portfolio, swing opportunities, monthly review and others). Run one with the `trading-workflow` skill; `trading-skills-navigator` recommends workflows and skillsets.
- **Agents:** `.github/agents/` holds:
  - the 12 TradingAgents roles (`ta-*`), used by `investment-debate`
  - `scenario-analyst` and `strategy-reviewer`, used by `scenario-analyzer`
  - the weekly-strategy agents
  - the `market-advisor` chat agent
- **Prompts:** `.github/prompts/` holds `weekly-trade-strategy`, `daily-market-dashboard`, `cts-scenario-analyzer` and `cts-us-undervalued-growth-screen`.
- **State:** thesis and journal state from `trader-memory-core` lives in `state/`, and TradingAgents decisions in `memory/decision-log.md`.

## Workflow
1. Retrieve recent US and Indian economic news.
2. Retrieve company announcements and filings.
3. Read portfolio/holdings.csv.
4. Analyze exposure to recent events.
5. Perform earnings and valuation analysis.
6. Construct bull, base, and bear cases.
7. Challenge each case with a risk review.
8. Generate a portfolio research report.

For steps 5–7 on a single instrument, use the `investment-debate` skill (the TradingAgents workflow). A shallow run costs about 12 agent calls per instrument. For a whole-portfolio request, run it only for the few holdings or opportunities that matter most, and list the names that were not debated. The skill also handles historical as-of runs, backtests (`investment-debate backtest ...`) and the decision track record.

Before starting a debate, look for today's run of that instrument and follow the skill's "Follow-ups and reuse" rules. A question the report already answers needs no new run. A follow-up that only changes the position, the horizon, the question or the language reuses the stages it cannot change.

Save portfolio-level reports as `reports/portfolio/<YYYY-MM-DD>-portfolio-review.md`. Per-instrument debates write to `reports/<EXCHANGE>_<TICKER>/<YYYY-MM-DD>/`, and other skills to `reports/<skill-name>/`.

## Investment rules
- Cite source URLs and publication dates.
- Never invent missing prices or fundamentals.
- Clearly separate fact, inference, and opinion.
- Highlight missing or stale information.
- Do not execute trades.
- No automatic buy/sell decisions.
- Distinguish INR and USD values.
- Consider FX and portfolio concentration.
- Prefer risk-adjusted return over raw returns.

Ratings from `investment-debate` (Buy, Overweight, Hold, Underweight, Sell) are research conclusions for a human to review, not orders.

## Portfolio performance
Use transaction history and cash flows for
money-weighted return calculations.
Do not equate simple price changes with
actual investor returns.

Transactions go in `portfolio/transactions.csv` (`date,symbol,side,quantity,price,fees,currency`). If it has no rows, say that money-weighted returns cannot be computed. Free cash goes in `portfolio/cash.csv` (`currency,amount`).
