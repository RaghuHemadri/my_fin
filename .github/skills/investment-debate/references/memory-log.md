# Decision memory log

This is a port of the TradingAgents memory log (`tradingagents/memory/log.py`, `settlement.py` and `reflection.py`). Each run records its decision. Once the holding window has traded, the decision is scored against a benchmark and gets a short reflection. Later runs show those lessons to the portfolio manager.

`scripts/memory_log.py` does all reads and writes. It writes atomically and never fetches data. Do not edit the log by hand.

## File and format

The live log is `memory/decision-log.md`. Each backtest has its own log; use `--log` to select it. Entries use the TradingAgents format.

A pending entry:

```text
[<DATE> | <SYMBOL> | <Rating> | pending]

DECISION:
<text of 5_portfolio/decision.md>
```

The same entry after settlement:

```text
[<DATE> | <SYMBOL> | <Rating> | +x.x% | +y.y% | <N>d | resolved:<YYYY-MM-DD>]

DECISION:
...

REFLECTION:
<2–4 sentences>
```

The tag fields are: decision date, symbol, rating, raw return, alpha versus the benchmark, holding window in trading days, and the date the outcome became known. Entries are separated by `<!-- ENTRY_END -->`.

## Settle pending entries (Step 0 of every run)

1. **List pending entries.** Run `python3 .github/skills/investment-debate/scripts/memory_log.py pending`. It prints each pending entry with its default benchmark as a Yahoo ticker: `^GSPC` for US listings, `^NSEI` for NSE, `^BSESN` for BSE. The run's `benchmark` setting overrides it. For other exchanges, look up the home-market index's Yahoo ticker and confirm it with `yahoo-finance` `get_stock_info`.
2. **Get prices.** Call `yahoo-finance` `get_historical_stock_prices` (`interval` `1d`) for the symbol's Yahoo ticker and the benchmark, with enough bars to cover the decision date plus `holding_days` sessions.
3. **Write the closes file** `RUN/0_settle_<SAFE_SYMBOL>_<DATE>.csv` with the columns `date,close,bench_close`. Include one row per session after the decision date, and copy the closes exactly.
4. **Check the window.** Run `memory_log.py settle --symbol <SYMBOL> --date <DATE> --closes-csv <file> --holding-days <N>`.
   - The entry close is the first close strictly after the decision date. The exit close is `N` sessions later. Today's bar is excluded.
   - The script prints `ready` with the returns, or `not yet settleable`. Leave a not-yet-settleable entry pending.
5. **Reflect.** Write the reflection with the prompt below to `RUN/0_reflection_<SAFE_SYMBOL>_<DATE>.md`.
6. **Settle.** Run the same `settle` command with `--reflection-file <file>`, plus `--max-entries <M>` if the `memory_max_entries` setting is set. Rotation drops the oldest resolved entries and always keeps pending ones.

If prices cannot be retrieved, leave the entry pending and record a memory note in `0_settings.json` (`memory_note`).

### Reflection prompt

This is adapted from the TradingAgents `Reflector`:

> You are a research analyst reviewing your own past decision now that the outcome is known. The outcome covers N trading days from the first close after the analysis date, which may be shorter than the horizon the decision was written for. Write exactly 2–4 sentences of plain prose (no bullets, no headers, no markdown). Cover, in order: (1) what the N-day alpha shows about the directional call (cite the figure and the benchmark), and say so plainly if the window is too short to judge the thesis; (2) which part of the investment thesis this window supports or undercuts; (3) one concrete lesson to apply to the next similar analysis. Be specific and terse. Your text is stored verbatim and re-read by future analysts, so every word must earn its place.

## Build `RUN/0_past_context.md`

Run `memory_log.py context --symbol <SYMBOL> --as-of <as_of> --out RUN/0_past_context.md`. It includes only entries resolved on or before `as_of`:

- up to 5 earlier decisions on the same symbol, in full, with their reflections
- the 3 most recent lessons from other symbols

If there are none, it writes `No resolved past decisions yet.` Only `ta-portfolio-manager` reads this file.

## Record the new decision

`finalize_run.py` calls the same `store` function. It appends a pending entry once per symbol and date.

## Track record

`memory_log.py summarize` scores all settled decisions by rating: count, the share where the direction was right, and the mean alpha. Hold claims no direction, so it has no hit rate. `REVIEW` entries are unscored.
