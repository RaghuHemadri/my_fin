---
name: edge-concept-synthesizer
description: Abstract detector tickets and hints into reusable edge concepts with thesis, invalidation signals, and strategy playbooks before strategy design/export.
---

<!-- tradingview-adapter:start -->
> [!IMPORTANT]
> **Workspace adapter.** Market data comes only from this workspace's MCP data servers (`tradingmcp`, `yahoo-finance`, `technicals` and `tapetide`). Tool names below are canonical TradingView MCP names; `.github/tradingview-adapter.md` maps each one to the configured servers. `tools/install_skills.py` added this block; the upstream text below is unchanged except that skill paths point to `.github/skills/`.
>
> - **Status:** Native. It needs no external market data.
> - **Upstream data:** edge tickets and hints from the other edge skills.
> - **In this workspace:** No direct data access.
> - **Scripts:** the bundled scripts are local and may run.
> - **Always:** follow `.github/tradingview-adapter.md`. Never invent data. Never place orders. Unless the skill names another location, write outputs under `reports/edge-concept-synthesizer/`.
<!-- tradingview-adapter:end -->

# Edge Concept Synthesizer

## Overview

Create an abstraction layer between detection and strategy implementation.
This skill clusters ticket evidence, summarizes recurring conditions, and outputs `edge_concepts.yaml` with explicit thesis and invalidation logic.

## When to Use

- You have many raw tickets and need mechanism-level structure.
- You want to avoid direct ticket-to-strategy overfitting.
- You need concept-level review before strategy drafting.

## Prerequisites

- Python 3.9+
- `PyYAML`
- Ticket YAML directory from detector output (`tickets/exportable`, `tickets/research_only`)
- Optional `hints.yaml`

## Output

- `edge_concepts.yaml` containing:
  - concept clusters
  - support statistics
  - abstract thesis
  - invalidation signals
  - export readiness flag

## Workflow

1. Collect ticket YAML files from auto-detection output.
2. Optionally provide `hints.yaml` for context matching.
3. Run `scripts/synthesize_edge_concepts.py`.
4. Deduplicate concepts: merge same-hypothesis concepts with overlapping conditions (containment > threshold).
5. Review concepts and promote only high-support concepts into strategy drafting.

## Quick Commands

```bash
python3 .github/skills/edge-concept-synthesizer/scripts/synthesize_edge_concepts.py \
  --tickets-dir /tmp/edge-auto/tickets \
  --hints /tmp/edge-hints/hints.yaml \
  --output /tmp/edge-concepts/edge_concepts.yaml \
  --min-ticket-support 2

# With hint promotion and synthetic cap
python3 .github/skills/edge-concept-synthesizer/scripts/synthesize_edge_concepts.py \
  --tickets-dir /tmp/edge-auto/tickets \
  --hints /tmp/edge-hints/hints.yaml \
  --output /tmp/edge-concepts/edge_concepts.yaml \
  --promote-hints \
  --max-synthetic-ratio 1.5

# With custom dedup threshold (or disable dedup)
python3 .github/skills/edge-concept-synthesizer/scripts/synthesize_edge_concepts.py \
  --tickets-dir /tmp/edge-auto/tickets \
  --output /tmp/edge-concepts/edge_concepts.yaml \
  --overlap-threshold 0.6

python3 .github/skills/edge-concept-synthesizer/scripts/synthesize_edge_concepts.py \
  --tickets-dir /tmp/edge-auto/tickets \
  --output /tmp/edge-concepts/edge_concepts.yaml \
  --no-dedup
```

## Resources

- `.github/skills/edge-concept-synthesizer/scripts/synthesize_edge_concepts.py`
- `references/concept_schema.md`
