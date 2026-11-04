---
name: trading-workflow
description: >-
  Run a Claude Trading Skills workflow from workflows/*.yaml step by step with the installed skills and TradingView
  MCP data: market-regime-daily, core-portfolio-weekly, swing-opportunity-daily, multi-asset-opportunity-daily,
  stockbee-ep-daily, stockbee-20pct-study-daily, stockbee-fluency-loop, trade-memory-loop,
  monthly-performance-review, shapiro-contrarian, kanchi-dividend-weekly, strategy-research-pipeline. Use when the
  user names one of these workflows or a skillset, or asks for a daily, weekly or monthly trading routine.
argument-hint: "<workflow-id> [YYYY-MM-DD]"
---

# Trading workflow runner

Workflow manifests in `workflows/` (from Claude Trading Skills) chain the installed skills into routines. Each step names one skill, decision gates wait for a human, and named artifacts flow between steps. Workflows are semi-automated: a person stays in the loop, and nothing is ever executed as a trade.

## Steps

1. **Choose.** If the user gave no workflow ID, show the table in `workflows/README.md`, or use the `trading-skills-navigator` skill to recommend one. Skillsets in `skillsets/*.yaml` are skill bundles; use them to suggest which workflow fits.
2. **Read** `workflows/<id>.yaml`. Note these fields:
   - `when_to_run` and `when_not_to_run`
   - `required_skills` and `optional_skills`
   - `prerequisite_workflows` and `manual_inputs`
   - `artifacts` and `steps`
   - `manual_review` and `journal_destination`

   The `_ja` fields are Japanese translations. Ignore them unless the user writes in Japanese.
3. **Check skills.** Confirm each required skill exists in `.github/skills/` and read its adapter block.
   - **Opt-in skill:** ask before using its source.
   - **Partial skill:** tell the user what will be approximated or missing.
   - **`api_profile`:** values such as `fmp-required` are served by TradingView MCP through the adapters.
4. **Run folder.** Create `reports/workflows/<id>/<YYYY-MM-DD>/`. If an upstream workflow listed in `prerequisite_workflows` already produced its artifact today, reuse that artifact and say so.
5. **Manual inputs.** Ask the user for any `manual_inputs` that were not provided. Skip the steps that need them if the user declines.
6. **Steps, in order.**
   - Load the step's skill and run it as its adapter block describes.
   - Save its artifact as `<NN>_<artifact_id>.md` (or `.json` if the skill emits JSON).
   - Pass artifacts to later steps as the manifest names them.
   - Run optional steps only when the user asked for them or their inputs exist.
7. **Decision gates.** At a step with `decision_gate: true`:
   1. Stop.
   2. Show the `decision_question` with the evidence so far.
   3. Wait for the user's answer, and record it in the run folder.
   4. Continue as the answer directs.
8. **Finish.** Write `summary.md` in the run folder with:
   - the artifacts produced, the gate decisions and the data gaps
   - the `manual_review` checklist for the user to confirm

   Then record the outcome with the `journal_destination` skill (`trader-memory-core`, which writes to `state/`).

## Rules

- TradingView MCP is the only data source; follow `.github/tradingview-adapter.md`.
- Outputs are postures, plans and research. Never place, stage or submit orders.
- If a required skill cannot run (for example, a gap such as COT data), stop the workflow at that step. Report what is missing; do not substitute guesses.
