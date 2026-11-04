# Data adapter

Every installed skill, ported agent and workflow follows these rules. Each skill's `SKILL.md` also starts with a short adapter block that gives that skill's status, data replacements, blocked scripts and gaps. The per-skill summary is in [docs/skill-coverage.md](../docs/skill-coverage.md).

## Canonical tool names

Skill adapter blocks name data tools by their **TradingView MCP** names, for example `get_ohlcv`, `get_financials`, `get_news` and `run_screener`. These are canonical names, not necessarily servers you have:

- **Default setup (free servers):** `technicals`, `yahoo-finance`, `tradingmcp` and `tapetide`. Translate each canonical name with the "TradingView tool equivalents" table in [copilot-instructions.md](copilot-instructions.md), and convert symbols with its "Symbols" table. Never call a canonical name directly unless a configured server provides it.
- **Optional TradingView MCP (paid):** if `.mcp.json` configures the official TradingView server, the canonical names are its tool names, one to one.

`.mcp.json` lists the servers that are actually configured.

## Rules

1. **Configured servers only.** Market data comes only from the MCP servers configured for this workspace. Do not search the web, and do not run code that fetches market data itself (FMP, Alpaca, yfinance, FinViz, FRED, CoinGecko, Binance, FXMacroData, hosted CSV files and so on). Opt-in skills are the only exception (see below). Scripts that call the configured local servers through `mcp/stdio_client.py`, such as the `investment-debate` setup and prefetch scripts, use those servers and are allowed.
2. **Scripts.** Never run a script that calls one of those services; each skill's adapter block lists its blocked scripts. Local scripts (calculators, scorers, report builders) may run on data you fetched from the servers.
3. **Input files for local scripts.** When a script needs a prices, events or candidates file, write it from tool output:
   - Copy the values exactly, and keep only the symbols and bars that are needed.
   - Record the source server, tool and as-of date in the file name or a note.
   - Spot-check two or three values against the tool output before running the script.
4. **Paths.**
   - Upstream `skills/<name>/` paths mean `.github/skills/<name>/`.
   - Upstream `CLAUDE.md` rules are in `.github/copilot-instructions.md`.
   - Workflows are in `workflows/` and skillsets in `skillsets/`.
   - Thesis and journal state is in `state/`.
   - Unless a skill names another location, write outputs under `reports/<skill-name>/`.
5. **Read-only.** Never call a tool that changes state unless the user explicitly asks. That includes watchlist, portfolio and alert write tools on any server. Never place, stage or submit orders.
6. **Gaps.** If no configured server has the data, say so in the output. Never fill a gap with a guess, and label approximations as approximations.
7. **Limits.**
   - Tapetide's free tier allows 50 calls a day and Marketaux 100 requests a day.
   - The TradingView MCP allows about 100 calls a minute.
   - Prefer batch and screener tools to per-symbol loops.
   - Yahoo data is unofficial and may be delayed.

## Data map

| What a skill needs | Upstream source | Canonical tool |
| --- | --- | --- |
| Quote, snapshot, valuation ratios | FMP quote and key metrics, yfinance `info`, Alpaca | `get_symbol_data`, `get_symbol_data_batch` |
| Daily, weekly or intraday bars | FMP historical, yfinance `history`, Alpaca bars | `get_ohlcv` |
| Technical indicators | computed from bars | `get_technicals_rating`, or `python3 .github/skills/investment-debate/scripts/indicators.py` on saved bars |
| Universe, screens, movers, sector and industry performance | FMP screener, FinViz, index lists | `run_screener`, `get_screener_columns` |
| Income statement, balance sheet, cash flow | FMP statements, yfinance financials, SEC EDGAR | `get_financial_history`, `get_financials` |
| Analyst estimates, ratings, price targets | FMP or yfinance estimates | `get_forecasts` |
| Earnings dates | FMP earnings calendar | `get_earnings_calendar` |
| Filings, transcripts, company IR material | SEC EDGAR, FMP transcripts, IR sites | `get_documents`, `get_document_view` |
| Dividends | FMP or yfinance dividends | `get_dividends_calendar` |
| Economic calendar and macro series | FMP, FXMacroData, FRED, official sources | `get_economic_calendar`, `get_economic_data` |
| News | web search, FMP or yfinance news, Marketaux | `get_news`, `get_news_story` |
| Market breadth | TraderMonty public CSV | breadth series found with `search_symbols`, or screener counts. Approximate; label it. |
| Holdings, cash, trades | Alpaca | `portfolio/holdings.csv`, `portfolio/cash.csv`, `portfolio/transactions.csv` |

### Extra data on the free servers

These have no TradingView MCP equivalent:

| Need | Tool |
| --- | --- |
| Institutional and mutual-fund holders, insider transactions | `yahoo-finance` `get_holder_info` (mainly US listings) |
| Option expiries and chains (volume, open interest, IV; no Greeks) | `yahoo-finance` `get_option_expiration_dates`, `get_option_chain` (US listings) |
| Splits and dividends history | `yahoo-finance` `get_stock_actions` |
| Rating upgrades and downgrades | `yahoo-finance` `get_recommendations` (`upgrades_downgrades`) |
| Indian shareholding, promoter pledges, bulk and insider deals, FII/DII flows, India VIX, earnings-call digests | `tapetide` (see its tool list) |
| Strategy backtests, Marketaux news sentiment | `tradingmcp` |
| Returns, volatility, beta, drawdown from daily bars | `technicals` `price_statistics` |

## Not available

| Data | Free servers (default) | TradingView MCP |
| --- | --- | --- |
| Economic calendar and macro series | Not available; use market proxies (`^TNX`, `INR=X`, `^INDIAVIX`, `BZ=F`) and report the gap | Available |
| US filings and transcripts | Not available | Available |
| Option chains | US only, without Greeks (compute Greeks locally with Black-Scholes) | Not available |
| Holders and insider transactions | Mainly US (Yahoo); India via Tapetide | Not available |
| 13F quarter-over-quarter flows, short interest and borrow | Not available | Not available |
| Social-media sentiment, prediction markets | Only Marketaux entity sentiment | Not available |
| Estimate revision history, order-book depth | Not available | Not available |
| Private-company and funding-round data | Not available | Not available |
| Complete RBI, SEBI, NSE, BSE and Government of India circulars | Not guaranteed (Tapetide covers part) | Not guaranteed |

CFTC COT data and some breadth series exist as symbols on some servers. Check with `search_symbols`; if nothing is found, treat it as a gap.

## Opt-in skills

These skills need a source outside the configured servers. They are installed with `disable-model-invocation: true`, so Copilot never picks them on its own.

| Skill | Source |
| --- | --- |
| `finance-sentiment` | Adanos API |
| `fintel-data` | Fintel |
| `hormuz-strait` | Hormuz Strait Monitor |
| `hyperliquid-reader` | Hyperliquid |
| `tradingview-reader` | TradingView desktop app via opencli |
| `yfinance-data` | the yfinance library, run directly |
| `discord-reader`, `linkedin-reader`, `opencli-reader`, `telegram-reader`, `twitter-reader` | social and web sources |
| `yc-reader` | Y Combinator dataset |
| `startup-analysis` | web research |

Use one only when the user invokes it or names its source. Label every figure from it with that source. Its setup (API keys, opencli, tdl) is the user's choice.

## Historical (point-in-time) work

For analysis as of a past date, follow [point-in-time.md](skills/investment-debate/references/point-in-time.md).
