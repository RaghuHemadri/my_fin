---
description: "Weekly trade strategy (Claude Trading Skills example): technical, US-market and news analyses, then a 200-300 line weekly strategy post, with an optional 18-month Druckenmiller-style plan."
argument-hint: "[YYYY-MM-DD] [druckenmiller]"
agent: agent
---

# Weekly trade strategy

This is a port of the Claude Trading Skills `examples/weekly-trade-strategy` project. It uses TradingView MCP data instead of chart screenshots and web search. Use the date the user typed after the command, or today's date. Call it `DATE`.

## Step 0: Prepare
1. Create `reports/weekly/DATE/` and `blogs/`.
2. If the user attached chart screenshots, save or list them in `charts/DATE/`. The agents use them alongside TradingView data.

## Step 1: Technical market analysis
Delegate to the `technical-market-analyst` agent. Tell it to analyze the weekly trend, momentum and key levels with `get_ohlcv` (`1W`) and `get_technicals_rating` (`1W`), using `search_symbols` to confirm each symbol, for:
- `CBOE:VIX`
- `TVC:US10Y`
- the S&P 500 breadth series (% of members above their 200-day MA; find it with `search_symbols`)
- `NASDAQ:NDX`, `SP:SPX`, `TVC:RUT`, `DJ:DJI`
- `COMEX:GC1!`, `COMEX:HG1!`, `NYMEX:CL1!`, `NYMEX:NG1!`, `AMEX:URA`
- sector and industry performance (1 week and 1 month) from `run_screener`

Output: `reports/weekly/DATE/technical-market-analysis.md`.

## Step 2: US market analysis
Delegate to the `us-market-analyst` agent. It applies the `market-environment-analysis` and `us-market-bubble-detector` skills, using their adapters.

Output: `reports/weekly/DATE/us-market-analysis.md`.

## Step 3: Market news analysis
Delegate to the `market-news-analyzer` agent. It covers news from the past 10 days and the events of the next 7 days. It uses the `market-news-analyst`, `economic-calendar-fetcher` and `earnings-calendar` skills, with their adapters.

Output: `reports/weekly/DATE/market-news-analysis.md`.

## Step 4: Weekly strategy post
Delegate to the `weekly-trade-blog-writer` agent. It reads the three reports, plus last week's post in `blogs/` if one exists, to keep sector allocations consistent. It writes a concise, action-focused 200–300 line post.

Output: `blogs/DATE-weekly-strategy.md`.

## Step 5 (optional): 18-month strategy
Run this step only if the user typed `druckenmiller` or asked for a long-term plan. Delegate to the `druckenmiller-strategy-planner` agent with the three reports. It writes the base, bull, bear and tail-risk scenarios.

Output: `reports/weekly/DATE/druckenmiller-strategy.md`.

## Rules
- Follow `.github/tradingview-adapter.md`. Cite each figure's source tool and date, and list any data gaps at the end of each report.
- The upstream example targets US markets and Japanese readers. Write in English unless the user asks otherwise. If the user holds Indian positions (`portfolio/holdings.csv`), add a short India note covering `NSE:NIFTY`, `FX_IDC:USDINR` and `TVC:IN10Y`.
- Research only. Never place orders.
