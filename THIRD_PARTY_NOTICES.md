# Third-party notices

This repository includes and adapts work from the projects below. Each keeps its own license. The full license texts are in [licenses/](licenses/).

## Claude Trading Skills

- Source: https://github.com/tradermonty/claude-trading-skills (commit pinned in `tools/upstream.lock.json`)
- License: MIT. Copyright (c) 2026 TraderMonty. Full text: [licenses/claude-trading-skills-MIT.txt](licenses/claude-trading-skills-MIT.txt)
- Included:
  - 74 skills in `.github/skills/`
  - `workflows/`, `skillsets/` and `skills-index.yaml`
  - the agents `scenario-analyst`, `strategy-reviewer`, `market-advisor`, `druckenmiller-strategy-planner`, `market-news-analyzer`, `technical-market-analyst`, `us-market-analyst` and `weekly-trade-blog-writer` in `.github/agents/`
  - the prompts `cts-scenario-analyzer` and `cts-us-undervalued-growth-screen` in `.github/prompts/`
- Changes:
  - Each `SKILL.md` has a workspace adapter block added at the top, and its skill paths point to `.github/skills/`. Opt-in skills are marked `disable-model-invocation: true`.
  - The agents and commands were converted to GitHub Copilot custom-agent and prompt-file formats, and an adapter note was added.
  - All changes are made by `tools/install_skills.py`.

## Finance Skills

- Source: https://github.com/himself65/finance-skills (commit pinned in `tools/upstream.lock.json`)
- License: MIT. Copyright (c) 2025 Alex Yang. Full text: [licenses/finance-skills-MIT.txt](licenses/finance-skills-MIT.txt)
- Included: 26 skills in `.github/skills/`.
- Changes: the same adapter blocks, path rewrites and opt-in flags, made by `tools/install_skills.py`.

## TradingAgents

- Source: https://github.com/TauricResearch/TradingAgents (commit pinned in `tools/upstream.lock.json`), by Tauric Research
- License: Apache License 2.0. Full text: [licenses/TradingAgents-Apache-2.0.txt](licenses/TradingAgents-Apache-2.0.txt)
- Adapted:
  - **Role prompts:** the twelve `.github/agents/ta-*.agent.md` files are modified versions of the TradingAgents agent prompts. They are rewritten as GitHub Copilot custom agents, with different data sources and added rules.
  - **Workflow:** `.github/skills/investment-debate/SKILL.md` and its `references/` reimplement the TradingAgents graph, debate logic, memory log and backtest procedure.
  - **Scripts:** `.github/skills/investment-debate/scripts/` are new Python ports of the TradingAgents rating parser, memory log, settlement, report layout and indicator set.
- This is not an official TradingAgents distribution.

## Runtime services and packages (not included in this repository)

`mcp/launch.sh` and `.mcp.json` download or connect to these at run time. Each is governed by its own license or terms of service:

| Component | Where it comes from |
| --- | --- |
| TradingMCP (`tradingview-mcp-server`) | https://github.com/atilaahmettaner/tradingview-mcp, via PyPI |
| Yahoo Finance MCP server | https://github.com/Alex2Yang97/yahoo-finance-mcp, at a pinned commit |
| Python packages of `mcp/market_technicals.py` (`mcp`, `yfinance`, `pandas`, `numpy`, `tradingview-ta`) | PyPI, through uv |
| Tapetide MCP | https://tapetide.com (remote service) |
| Marketaux | https://www.marketaux.com (optional API key) |
| TradingView MCP (optional, paid) | https://www.tradingview.com/mcp/docs |

Market data from these services is subject to their terms. Yahoo Finance data is unofficial. This project is not affiliated with or endorsed by any of these providers, or by GitHub, TradingView, Yahoo, Tapetide, Marketaux, TraderMonty, Alex Yang or Tauric Research.
