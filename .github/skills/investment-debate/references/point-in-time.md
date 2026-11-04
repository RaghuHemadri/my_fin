# Point-in-time rules (historical mode)

A run is **historical** when its analysis date (`as_of`) is earlier than today. TradingAgents serves all data "as of" the trade date. Several of the MCP tools serve only current snapshots, so using them in a historical run would leak information from after the analysis date. Follow these rules in every historical run, including every backtest cell.

## By tool

| Tool| Tool | Use in historical mode |
| --- | --- |
| `yahoo-finance` `get_historical_stock_prices`, `tapetide` `get_price_history` | **Allowed.** Request enough bars to reach from today back past the analysis date, plus the lookback you need. Use only bars dated on or before `as_of`. Compute indicators with `python3 .github/skills/investment-debate/scripts/indicators.py --bars <file> --as-of <as_of>`. |
| Macro proxies (`^TNX`, `INR=X`, `BZ=F`, `^INDIAVIX`) via `get_historical_stock_prices` | **Allowed** for bars on or before `as_of`. There is no economic calendar in any mode. |
| `tapetide` `list_company_documents`, `get_document_summary`, `read_document` | **Allowed** for filings dated on or before `as_of`. |
| `yahoo-finance` `get_financial_statement`; `tapetide` `get_financials` | **Allowed** only for periods published on or before `as_of`. `tapetide` `get_financials` stamps each period with its publication date. For Yahoo, assume publication 45 days after a quarter end and 90 days after a fiscal-year end. Figures may have been restated since. |
| `tradingmcp` `financial_news`, `market_sentiment`; `tapetide` `get_stock_events`, `get_market_news`; `yahoo-finance` `get_yahoo_finance_news` | **Allowed** only for headlines published on or before `as_of`. These feeds keep only recent items, so older windows are often empty. Report that as a gap. |
| `yahoo-finance` `get_stock_actions` | **Allowed** for ex-dates on or before `as_of`. |
| `tapetide` `search_stocks`, identity fields of `yahoo-finance` `get_stock_info` | **Allowed** for name, exchange, type, currency and sector only. |
| `get_stock_info` (other fields), `get_recommendations`, `tradingmcp` `stock_prices`, `stock_screener`, backtests, `technicals` `technical_snapshot` and `price_statistics`, `tapetide` quotes, forecasts, scores, shareholding, flows and screeners | **Not allowed.** They return current values or use data after `as_of`. Compute valuation multiples from the historical close and the published statements instead. |

role

- **Market analyst.** The "verified snapshot" is the last bar on or before `as_of`. Save the `get_historical_stock_prices` bars exactly as returned to `RUN/1_analysts/bars_<interval>.json`, then run `indicators.py --as-of`.
- **Sentiment analyst.** Use only news on or before `as_of`. Analyst consensus and technical consensus are not available historically, so confidence is at most "low" unless news is plentiful.
- **News analyst.** Use only news, documents and calendar entries on or before `as_of`.
- **Fundamentals analyst.** Use financial history and documents within the publication lag. Analyst consensus is not available historically.
- **Portfolio context.** `portfolio/holdings.csv` describes today's book. In historical runs it is a standing book, as in TradingAgents' backtest, unless the user supplies a book as of that date. Say which one you used.
- **Past lessons.** Build `0_past_context.md` with `memory_log.py context --as-of <as_of>`, so only outcomes known by then are used.

## Caveats (state them in the report)

- Even with these rules, a historical run is not fully point-in-time: macro data is revised, financials are restated, and news feeds keep only recent items, which truncates old windows.
- The model may know about later events. Every agent must reason only from data dated on or before `as_of`, and must not use knowledge of what happened afterwards.
- Label historical results as indicative.
