---
description: "Daily market dashboard (Claude Trading Skills example): FTD, uptrend, breadth, theme and VCP readings in one dashboard, then Q&A with the market-advisor agent."
argument-hint: "[YYYY-MM-DD]"
agent: agent
---

# Daily market dashboard

This is a port of the Claude Trading Skills `examples/daily-market-dashboard` app. The Streamlit app and Claude Agent SDK are replaced by Copilot: this prompt builds the dashboard, and the `market-advisor` agent answers questions about it. Use the date the user typed after the command, or today's date. Call it `DATE`.

## Steps
1. Run these five skills. Each one gets its data from TradingView MCP as its adapter block describes. They are independent, so delegate them in parallel if the harness allows.
   - `ftd-detector`: follow-through-day state for `SP:SPX` and `NASDAQ:IXIC`.
   - `uptrend-analyzer`: uptrend participation. This is an approximation; label it.
   - `market-breadth-analyzer`: breadth composite. This is an approximation; label it.
   - `theme-detector`: leading and lagging themes.
   - `vcp-screener`: VCP candidates.
2. Write `reports/dashboard/daily_dashboard_DATE.md` with:
   - a header with `generated_at` (the current date and time; never guess the date)
   - a summary table (skill, reading, score or state, data date)
   - one section per skill
   - a "Data gaps and approximations" section
3. Optionally, use the `generative-ui` skill to write `reports/dashboard/daily_dashboard_DATE.html`.
4. Tell the user they can switch to the `market-advisor` agent to ask questions about the dashboards in `reports/dashboard/`.

## Rules
- Follow `.github/tradingview-adapter.md`. If a skill cannot produce a reading, show "N/A" with the reason; never fill it in.
- Research only. Never place orders.
