# Backtest

This is a port of TradingAgents `backtest.py`. It runs the debate over a grid of instruments and dates, then scores the decisions it produced. It evaluates **decision quality**: for each rating, how often the direction was right and the mean alpha. It is not a portfolio simulator. There are no fills, quantities or cash ledger, and every cell is independent.

## Inputs

- `tickers`: one or more `EXCHANGE:TICKER` symbols.
- `start` and `end`: the date range (`YYYY-MM-DD`).
- `every`: days between analysis dates (default 7).
- `run_id` (optional): resume or name a backtest.
- The normal `investment-debate` settings: analysts, depth, rounds, holding days and so on.

## Cost

Every cell is a full run of about 12 agent calls (more at higher depth). State the number of cells (tickers × dates) and ask the user to confirm before starting. To reduce cost, suggest `depth=shallow` and fewer analysts, for example `analysts=market,news,fundamentals`.

## Steps

1. **Grid.** Generate dates from `start` every `every` days. Stop at `end` or today, whichever is earlier. Each pair of ticker and date is a cell. A ticker or date given twice counts once.
2. **Folder and log.**
   - Set `run_id` to the current time as `YYYYMMDD_HHMMSS`, unless the user gave one. If that ID is taken, add `_2`, `_3` and so on.
   - Set `B` to `reports/backtest/<run_id>/`.
   - The log is `B/decision-log.md`. Never write backtest decisions to `memory/decision-log.md`.
3. **Resume.** Skip cells that already have an entry in `B/decision-log.md`.
4. **Each cell**, in date order, runs `investment-debate` in historical mode:
   - Set `as_of` to the cell date and `RUN` to `B/<SAFE_SYMBOL>/<date>/`.
   - Follow [point-in-time.md](point-in-time.md).
   - Build past context with `memory_log.py --log B/decision-log.md context --symbol <SYMBOL> --as-of <date>`.
   - Finalize with `finalize_run.py ... --memory-log B/decision-log.md --no-html`.
   - If a cell fails, record the symbol, date and reason in `B/failures.md`, then continue.
5. **Settle.** After all cells, settle every pending entry in `B/decision-log.md` as described in [memory-log.md](memory-log.md), using `--log B/decision-log.md`. Entries whose window has not finished trading stay pending.
6. **Summarize.** Write `B/summary.md` containing:
   - the output of `memory_log.py --log B/decision-log.md summarize`
   - a table of cells (date, symbol, rating, raw return, alpha)
   - the failures
   - the caveats below

## Caveats

- **Indicative only.** Each cell samples the model once, and news feeds and current snapshots are not point-in-time archives.
- **Model knowledge.** The model may know about later events. See [point-in-time.md](point-in-time.md).
- **Hold.** Hold claims no direction, so it has no hit rate; only its mean alpha is reported.
- **REVIEW.** Decisions without a readable rating are counted as unscored.
