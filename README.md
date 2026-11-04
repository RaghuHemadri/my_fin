# Investment research agent

A GitHub Copilot research setup for a mixed Indian and US portfolio. It combines all 100 skills from [Claude Trading Skills](https://github.com/tradermonty/claude-trading-skills) and [Finance Skills](https://github.com/himself65/finance-skills), a Copilot port of the [TradingAgents](https://github.com/TauricResearch/TradingAgents) multi-agent debate, and free market-data MCP servers.

```text
Free MCP data servers     technicals (local) · Yahoo Finance · TradingMCP (self-hosted) · Tapetide (India)
        │
GitHub Copilot            the AI engine (VS Code or Copilot CLI)
  ├─ Claude Trading Skills    all 74 skills, plus workflows, skillsets, agents and commands
  ├─ Finance Skills           all 26 skills
  └─ investment-debate        TradingAgents: analysts → bull vs bear → trader → risk debate → rating,
                              with memory, reflection, historical runs and backtests
        │
portfolio/ + reports/      your holdings and the research output
```

> [!WARNING]
> **Not investment advice.** This is a research tool. Its ratings, plans and reports are produced by AI from third-party data that can be delayed, incomplete or wrong. Verify everything yourself before you act on it. Nothing in this setup places trades.

## Quick start

1. **Install the tools.** You need [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`), and either VS Code with GitHub Copilot (Agent mode) or the Copilot CLI. On Windows, use WSL or Git Bash, because the server launcher is a bash script.
2. **Clone and register the data servers:**
   ```bash
   git clone <repository-url> investment-agent
   cd investment-agent
   bash mcp/register-user-config.sh
   ```
3. **Add your portfolio.** These files are git-ignored, so they stay on your machine:
   ```bash
   cp portfolio/holdings.example.csv portfolio/holdings.csv          # then replace the sample rows
   cp portfolio/cash.example.csv portfolio/cash.csv                  # optional
   cp portfolio/transactions.example.csv portfolio/transactions.csv  # optional
   ```
4. **Try it.** Open the folder in VS Code, start a **new** Copilot chat in Agent mode, and try `/investment-debate NSE:TCS`. The first server start takes a minute. Tapetide asks for a Google sign-in the first time it is used.

[Setup](#setup) has the details, including the optional Marketaux key and the Copilot CLI.

## Layout

| Path | What it is |
| --- | --- |
| `.mcp.json` | The four data servers for Copilot CLI and the VS Code Copilot harness (portable `mcpServers` format) |
| `.vscode/mcp.json` | The same servers for the VS Code Local harness (`servers` format) |
| `mcp/register-user-config.sh` | Registers the four servers in `~/.copilot/mcp-config.json`, which Copilot agent sessions read at start |
| `mcp/launch.sh` | Starts the three local servers (`tradingmcp`, `yahoo-finance`, `technicals`) with pinned versions, behind the caching proxy, and loads `~/.config/my_fin/secrets.env` |
| `mcp/market_technicals.py` | Local `technicals` server: TradingView's public technical rating and indicators computed from Yahoo bars, for NSE, BSE and US stocks |
| `mcp/cache_proxy.py`, `mcp/cache_core.py`, `mcp/datastore.py` | The caching proxy the local servers run behind, its freshness policy, and the SQLite store (`~/.cache/my_fin/my_fin.sqlite3`) for cached answers, call logs, daily bars and run timings |
| `mcp/cache_admin.py`, `mcp/stdio_client.py`, `mcp/servers.py` | Cache statistics and clearing; the MCP client the scripts use (with a `health` check); listing or stopping the idle servers of old chats |
| `.github/copilot-instructions.md` | Always-on rules: the data servers and a map from TradingView tool names to them, read-only, investment rules, how to use the skills |
| `.github/tradingview-adapter.md` | Data adapter for all skills: the canonical tool names used in skill adapter blocks, the rules, the data map, extra data on the free servers, gaps for each server setup, and the opt-in policy |
| `.github/skills/` | 100 upstream skills, each with a workspace adapter block at the top of its `SKILL.md`, plus `investment-debate` and `trading-workflow` |
| `.github/agents/` | The 12 TradingAgents roles (`ta-*`); the Claude Trading Skills agents `scenario-analyst` and `strategy-reviewer`; the weekly-strategy agents; `market-advisor` |
| `.github/prompts/` | `weekly-trade-strategy`, `daily-market-dashboard`, `cts-scenario-analyzer`, `cts-us-undervalued-growth-screen` |
| `workflows/`, `skillsets/`, `skills-index.yaml` | Claude Trading Skills routines and catalog, used by `trading-workflow` and `trading-skills-navigator` |
| `state/` | Thesis and journal state for `trader-memory-core` and related skills (contents git-ignored) |
| `portfolio/` | `*.example.csv` templates. Copy them to `holdings.csv` (`symbol,quantity,avg_cost,currency`), `cash.csv` (`currency,amount`) and `transactions.csv`. Your copies are git-ignored. |
| `reports/` | All output: debate runs, backtests, workflow runs, dashboards, skill reports (git-ignored) |
| `memory/decision-log.md` | Created by the first debate run: past ratings, their outcomes and reflections (git-ignored) |
| `tools/install_skills.py`, `tools/skills_manifest.json`, `tools/upstream.lock.json` | Install and adapt every upstream skill from pinned upstream commits (`--fetch`, `--refresh`, `--update-lock`; see [CONTRIBUTING.md](CONTRIBUTING.md)) |
| `tools/precompute.py`, `tools/schedule/register-windows-task.ps1` | Debates for your holdings and `portfolio/watchlist.csv` after the markets close, and its Windows Task Scheduler registration |
| `tools/profile_session.py` | Where a run's time went, read from a Copilot session log |
| `docs/skill-coverage.md` | Generated list of every skill, its status, the data it uses and its gaps |
| `tests/`, `.github/workflows/ci.yml` | Tests for the scripts and the repository structure, run by CI |
| `LICENSE`, `THIRD_PARTY_NOTICES.md`, `licenses/` | This project's MIT license, and the upstream licenses and notices |
| `vendor/` | Upstream clones for maintainers (git-ignored). `python3 tools/install_skills.py --fetch` re-creates them. |

## Setup

Every data source is free. No TradingView account is needed.

| Server | Source | Account or key |
| --- | --- | --- |
| `technicals` | Local `mcp/market_technicals.py`: TradingView's public technical rating, plus indicators computed from Yahoo bars | None |
| `yahoo-finance` | [Alex2Yang97/yahoo-finance-mcp](https://github.com/Alex2Yang97/yahoo-finance-mcp), pinned to a commit | None |
| `tradingmcp` | [TradingMCP](https://github.com/atilaahmettaner/tradingview-mcp), self-hosted from PyPI `tradingview-mcp-server==0.8.1` | Optional free [Marketaux](https://www.marketaux.com/) key for news and sentiment (100 requests a day) |
| `tapetide` | [Tapetide](https://tapetide.com/mcp), remote, NSE and BSE | Google sign-in. Free tier: 50 tool calls a day |

1. **uv.** The local servers run with [uv](https://docs.astral.sh/uv/), which downloads the right Python (3.12, and 3.14 for `yahoo-finance`) and packages on first start:
   `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Marketaux key (optional, recommended).** Sign up at marketaux.com, then put the key in `~/.config/my_fin/secrets.env` (outside the repo, `chmod 600`):
   `MARKETAUX_API_TOKEN=your_key`
   Restart the `tradingmcp` server after changing it. Without a key, the news and sentiment tools return "not configured" and the agents record a gap.
3. **Register the servers for Copilot sessions:** `bash mcp/register-user-config.sh`
   - It adds the four servers to `~/.copilot/mcp-config.json` with absolute paths, keeping any servers already there. `--remove` takes them out again.
   - This is required for the VS Code Copilot (agent host) harness: its sessions read only that user-level file and servers VS Code passes to them, not this folder's `.mcp.json`. Copilot CLI reads it too.
   - Servers load when a session starts, so open a **new** chat or session afterwards. Tapetide asks for Google sign-in the first time it is used.
   - The servers then start in every Copilot session, in any folder. Run `--remove` if you do not want that.
4. **VS Code Local harness (optional).** It reads `.vscode/mcp.json`.
   - Run **MCP: List Servers** and start `technicals`, `yahoo-finance` and `tradingmcp`. The first start takes a minute while uv installs packages.
   - Start `tapetide` and complete the Google sign-in when prompted.
   - In the chat's tool picker, enable the tools of all four servers.
   - Test it in a new chat: "Use the technicals, yahoo-finance, tradingmcp and tapetide tools to get the latest price of NSE:RELIANCE from each."
5. **Copilot CLI (optional).**
   - Install it with `npm install -g @github/copilot` (needs Node.js 22 or later).
   - Run `copilot` in this folder and confirm folder trust when asked. Project MCP servers are skipped silently in untrusted folders.
   - Run `/login`, then use `/mcp` to check the four servers and complete the Tapetide sign-in.
   - If `/mcp` does not list them, run `git init` in this folder (the CLI looks for `.mcp.json` from the working directory up to the Git root), or add them with `copilot mcp add`.
6. **Check the local servers from a shell (optional):** `bash mcp/launch.sh technicals` should start and wait silently for input; stop it with Ctrl+C.
7. **Portfolio.** Copy `portfolio/holdings.example.csv` to `portfolio/holdings.csv` and replace the sample rows with your holdings. Use `EXCHANGE:TICKER` symbols such as `NSE:TCS` or `NASDAQ:MSFT`. Optionally, do the same with `cash.example.csv` and `transactions.example.csv`; transactions enable money-weighted returns. Your copies are git-ignored.

### Optional: TradingView MCP (paid)

With a TradingView Essential plan or higher (trial plans excluded), you can add the official TradingView MCP server. It adds an economic calendar, macro series, and US filings and transcripts. To add it:
1. Add `"tradingview": {"type": "http", "url": "https://mcp.tradingview.com/mcp", "tools": ["*"]}` under `mcpServers` in `.mcp.json` and in `~/.copilot/mcp-config.json`.
2. Add the same entry, without `tools`, under `servers` in `.vscode/mcp.json`.
3. In `.github/copilot-instructions.md`, add `tradingview` to the "Financial data sources" table and say which data to take from it. The canonical tool names in the skill adapter blocks are its own tool names.
4. Run `python3 tools/install_skills.py --refresh`, so the adapter blocks and ported agents list the new server.

Sign-in uses OAuth.

## Use

**Portfolio review:**

> Analyze my Indian and US portfolio using the MCP data servers, Claude Trading Skills, Finance Skills, and the investment-debate skill. Review the most important government economic announcements and company events from the last seven days. Explain the potential impact on my existing positions, assess concentration risk, identify deteriorating fundamentals, and propose investment opportunities worth researching. Include a bull/base/bear debate and clearly distinguish verified information from missing data.

**TradingAgents debate on one instrument:** `/investment-debate NSE:TCS`

The run writes every stage to `reports/<EXCHANGE>_<TICKER>/<date>/`, then `complete_report.md`, `complete_report.html` and `state.json`. If a run stops, ask Copilot to run it again; it resumes from the first missing stage. Options, as in TradingAgents:

| Option | Values |
| --- | --- |
| `depth` | `shallow` (1 round, about 12 agent calls), `medium` (3), `deep` (5) |
| `analysts` | a subset of `market,sentiment,news,fundamentals` |
| `language` | any language; rating labels stay in English |
| `date` | a past date gives a historical, point-in-time run |
| `deep_model`, `quick_model` | models for the judge roles and the other roles |
| `deep_effort`, `quick_effort` | reasoning effort for the judge roles and the other roles (unset: the session's) |
| other | `holding_days`, `benchmark`, `fresh=true`, `html=false` |

**Follow-up questions:** a question that today's report already answers is answered from it. A follow-up that only changes your position ("I hold 20 shares at USD 45"), the horizon, the emphasis or the language reuses the finished stages it cannot change, so a position follow-up runs about 5 agents instead of 12. See "Follow-ups and reuse" in the [investment-debate skill](.github/skills/investment-debate/SKILL.md).

**Headless and scheduled runs:** `python3 .github/skills/investment-debate/scripts/run_debate.py NSE:TCS --holding-days 60` runs the same debate outside the chat, with the Copilot CLI (log in once with `copilot`, then `/login`). `python3 tools/precompute.py` runs it after the close for your holdings and `portfolio/watchlist.csv` (copy `watchlist.example.csv`); `tools/schedule/register-windows-task.ps1` schedules that on Windows.

**Backtest:** `/investment-debate backtest tickers=NSE:TCS,NASDAQ:MSFT start=2026-06-01 end=2026-09-30 every=14`. Each cell is a full run, so Copilot states the cost and asks first.

**Track record:** each run first settles past decisions whose holding window has passed (each decision's own `holding_days`, 20 trading days by default), with `settle_pending.py`. It scores them against the home-market index (S&P 500, NIFTY 50 or SENSEX) and writes a short reflection, which the portfolio manager reads next time. Run `python3 .github/skills/investment-debate/scripts/memory_log.py summarize` for hit rate and mean alpha by rating.

**Claude Trading Skills routines:** `/trading-workflow market-regime-daily` (or `core-portfolio-weekly`, `monthly-performance-review` and the others in `workflows/README.md`). `/weekly-trade-strategy` writes the weekly strategy post. `/daily-market-dashboard` builds the dashboard; then switch to the `market-advisor` agent to ask about it.

**Any single skill:** ask naturally ("run a CANSLIM screen", "value MSFT with a DCF", "what moves with TCS?"), or type `/<skill-name>`.

**Opt-in skills:** these need a source outside the configured MCP servers and run only when you invoke them by name:
- `twitter-reader`, `discord-reader`, `telegram-reader`, `linkedin-reader`, `opencli-reader`
- `finance-sentiment`, `fintel-data`, `hyperliquid-reader`, `hormuz-strait`
- `yfinance-data`, `yc-reader`, `startup-analysis`, `tradingview-reader`

Their own setup steps (API keys, opencli, tdl) apply.

## How runs stay fast

A shallow debate is 12 agents, and 8 of them must run one after another, so most of a run is the agents writing their reports. Profiling the October runs (`tools/profile_session.py`) showed that data calls were only about 4% of the time and that about 30% of each agent's time went to work that is not analysis. These parts remove that work without changing the roles, their inputs, their methods or their reports:

| Part | What it does |
| --- | --- |
| Setup and data in seconds | `prepare_run.py` writes the run's setup files, and `prefetch_data.py` makes every analyst's routine data calls once, in parallel, into one file per analyst. Together they take about 10 s, instead of several minutes of model turns. |
| Shared cache | The local servers run behind `mcp/cache_proxy.py`. Within a run, all agents see one snapshot, and repeated calls from any chat or script are answered from `~/.cache/my_fin/my_fin.sqlite3`: quotes for 2 minutes, technicals and daily bars for 15–30 minutes, statements and holders for 12 hours. Errors are never cached. The proxy also drops the copy of each Yahoo answer that the server sent twice, which halves what the model reads. |
| Fewer model turns | Every role reads its inputs in one batch; the analysts batch their remaining calls; the orchestrator launches each role right away. |
| Reuse | Questions that today's report answers are answered from it, and follow-ups re-run only the stages they change. |
| Run before you ask | `tools/precompute.py` runs your holdings and watchlist after the close. |
| Optional effort tiers | `quick_effort` (for example `high` instead of `xhigh`) speeds up the ten non-judge roles. It is off by default because it can make their arguments shallower. |

To see where a run's time went, run `python3 tools/profile_session.py --latest` after it finishes. `python3 mcp/cache_admin.py stats` shows cache hits by tool, and `RUN/run_log.json` holds the stage timings of headless runs.

## What is included from each project

### TradingAgents (ported into `investment-debate`)

| TradingAgents feature | Here |
| --- | --- |
| Market, Sentiment, News, Fundamentals analysts; selectable; parallel | `ta-*-analyst` agents and the `analysts` option (crypto drops fundamentals) |
| Bull ⇄ Bear debate, Research Manager plan (5-tier), Trader proposal | `ta-bull-researcher`, `ta-bear-researcher`, `ta-research-manager`, `ta-trader` (sizing via `position-sizer`) |
| Aggressive → Conservative → Neutral risk debate, Portfolio Manager rating | `ta-*-risk-analyst`, `ta-portfolio-manager` (adds scenarios, portfolio fit and data gaps) |
| Debate and risk rounds, research depth presets | `debate_rounds`, `risk_rounds`, `depth` (1/3/5) |
| Input isolation, empty-report markers, "opponent has not spoken" rule | Agent input lists and rules |
| Instrument identity and portfolio context (positions, cash) | `0_instrument.md`, `0_portfolio.md` from `holdings.csv` and `cash.csv` |
| Verified market snapshot, `get_indicators` | `get_symbol_data`, `get_technicals_rating`, `scripts/indicators.py` (same indicator names) |
| Tool-round cap with wrap-up | Per-analyst MCP call budgets (with a Tapetide sub-cap) |
| Structured outputs, rating parser, REVIEW sentinel | Labelled sections and `finalize_run.py` |
| Output language | `language` option |
| Historical trade date, point-in-time data | `date` option and `references/point-in-time.md` |
| Memory log, settlement vs regional benchmark, reflection, as-of lessons, rotation | `scripts/memory_log.py`, `references/memory-log.md` |
| Backtest over tickers × dates, summary by rating | `backtest` command, `references/backtest.md`, `memory_log.py summarize` |
| Checkpoint and resume, clear checkpoints | Stage files, `fresh=true` |
| Markdown and HTML report tree, full-state JSON log | `complete_report.md`, `complete_report.html`, `state.json` |
| Quick and deep model tiers | `quick_model`, `deep_model` |

These are not reproduced, because Copilot or the MCP servers replace them:
- the LangGraph runtime
- the 20+ LLM provider clients and ChatGPT-plan login
- the CLI's live token statistics
- the data vendors: yfinance, Alpha Vantage, SEC EDGAR, FRED, Polymarket, StockTwits, Reddit, and Jev screening

### Claude Trading Skills

- **Skills:** all 74 are installed. 26 run natively, 33 are adapted to the MCP servers, and 12 are partly covered (the servers lack part of their data). 3 need your own files or software: chart images, MetaTrader 5, manifoldbt.
- **Agents:** `scenario-analyst` and `strategy-reviewer` are ported.
- **Commands:** both are ported as prompts.
- **Workflows and catalog:** all 12 workflows, 5 skillsets and the skills index are included, with the `trading-workflow` runner.
- **State:** the `state/` folders are included.
- **Examples:** both are ported as Copilot agents and prompts. The weekly trade strategy is a prompt plus 5 agents. The Streamlit dashboard becomes a prompt plus the `market-advisor` agent.
- **Not included:** repository maintenance tooling (CI checks, docs-site and catalog generators, launchd jobs that generate new skills, audits, packaged `.skill` zips, FMP client generator). These maintain that repository and are not trading features.

### Finance Skills

- **Skills:** all 26 are installed. 2 run natively, 7 are adapted, 4 are partly covered, and 13 are opt-in (social readers, external data APIs, yfinance, web research).
- **`tradingview-mcp`:** the community TradingView MCP server this skill was written for runs here as `tradingmcp`, a pinned PyPI release.
- **opencli plugins:** the readers that need them install them from GitHub when you opt in.
- **Not included:** the plugin marketplace and packaging scripts. Copilot reads skills from `.github/skills/` directly.

Per-skill details: [docs/skill-coverage.md](docs/skill-coverage.md).

## Limitations

- **Indian regulatory news.** No configured source, Tapetide included, guarantees complete coverage of RBI, SEBI, NSE, BSE or Government of India circulars. The agents report this as a coverage gap.
- **Data the free servers do not provide.** An economic calendar and macro series (market proxies such as `^TNX` and `INR=X` are used instead); social-media sentiment beyond Marketaux entity sentiment; prediction markets; order-book depth; US filings and transcripts. Skills that need these say so instead of guessing.
- **Free-tier limits.** Tapetide allows 50 calls a day, so each debate run caps its Tapetide use at about 10 calls (about five runs a day). Marketaux allows 100 requests a day. Yahoo Finance data is unofficial and can be delayed, revised or rate-limited.
- **TradingMCP technical tools do not accept NSE or BSE** (`combined_analysis`, `multi_timeframe_analysis` and similar). The local `technicals` server covers Indian stocks instead.
- **Canonical tool names.** Skill adapter blocks name tools by their TradingView MCP names. `.github/copilot-instructions.md` and `.github/tradingview-adapter.md` map them to the free servers. Some TradingView features, such as `run_screener` columns or breadth series, have only partial equivalents.
- **Approximations.** Breadth and uptrend skills use breadth series and screener counts instead of TraderMonty's CSV data, and label the results as approximations.
- **Historical runs and backtests are indicative.** News feeds keep only recent items, current snapshots are not point-in-time archives, and the model may know later events.
- **Scripts that call FMP, Alpaca, yfinance, FinViz and similar services are never run directly.** Their methods are applied to data from the MCP servers instead (Yahoo Finance is reached only through the `yahoo-finance`, `technicals` and `tradingmcp` servers). Local calculators and scorers run normally.

## Privacy

Your data stays on your machine:
- `portfolio/*.csv`, `reports/`, `memory/` and the contents of `state/` are git-ignored.
- API keys live in `~/.config/my_fin/secrets.env`, outside the repository. You can point `MY_FIN_SECRETS` at another file.
- The cache, `~/.cache/my_fin/my_fin.sqlite3` (or `MY_FIN_DB`), holds tool answers with their arguments, which include the symbols you hold, plus run timings. `python3 mcp/cache_admin.py clear` empties the answers.

The servers send your queries (symbols, not holdings) to Yahoo Finance, Tapetide, Marketaux and, if you add it, TradingView. Review their terms.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Copilot does not see the data tools | Run `bash mcp/register-user-config.sh`, then start a **new** chat. In the VS Code Local harness, start the servers from **MCP: List Servers**. |
| `uv is not installed` in the server log | Install uv, then restart VS Code so it sees `~/.local/bin`. |
| `bash` not found on Windows | Open the folder from WSL (VS Code "WSL: Open Folder"), or install Git for Windows and make sure its `bash` is on `PATH`. |
| `yahoo-finance` is slow to start the first time | uv is downloading Python 3.14 and the server. Wait a minute, then retry. |
| News or sentiment says "not configured" | Add `MARKETAUX_API_TOKEN` to `~/.config/my_fin/secrets.env`, then restart `tradingmcp`. |
| Tapetide stops answering | The free tier allows 50 calls a day. The agents record the gap; try again the next day. |
| Copilot CLI does not list the servers | Confirm folder trust, then run `/mcp`. The user-level registration from step 3 also applies to the CLI. |
| A tool answer looks stale or wrong | `python3 mcp/cache_admin.py clear` empties the cache. To run the servers without the proxy, add `MY_FIN_CACHE=0` to `~/.config/my_fin/secrets.env` and start a new chat. `python3 mcp/stdio_client.py health` checks the three local servers. |
| The headless runner says the CLI is not logged in | Run `copilot` once and use `/login`, or set `COPILOT_GITHUB_TOKEN`. |
| WSL uses a lot of memory | Each chat keeps its own servers running. `python3 mcp/servers.py` lists them by chat, and `--stop-older-than 24` stops the ones older than a day. |

## Maintaining

Upstream skills are pinned in `tools/upstream.lock.json`, and `tools/install_skills.py` rebuilds everything from them. Run the tests with `python3 -B -m unittest discover -s tests -v`; `MY_FIN_INTEGRATION=1` adds the caching-proxy test, which needs uv. To update, regenerate or extend the skills, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first. Never include real holdings, reports or API keys.

## License

This project is under the [MIT License](LICENSE). It includes and adapts:
- Claude Trading Skills (MIT)
- Finance Skills (MIT)
- prompts and logic from TradingAgents (Apache-2.0)

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [licenses/](licenses/). This project is not affiliated with any of these projects or with the data providers.
