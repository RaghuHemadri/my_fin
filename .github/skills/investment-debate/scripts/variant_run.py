#!/usr/bin/env python3
"""Answer a follow-up by reusing the stages of a finished run that the follow-up cannot change.

A follow-up such as "I hold 20 shares at USD 45" only changes the portfolio context. The analysts and the
bull/bear researchers never see holdings (SKILL.md, isolation), so their files stay valid; only the trader,
the risk debate and the portfolio manager need to run again. This script creates the variant run folder,
copies what stays valid, refreshes the setup files, and reports the stage to resume from. The usual resume
rule (run the first missing stage, in graph order) does the rest.

  --change position   reuse the analyst reports and the whole research debate (a position or cash changed)
  --change horizon    reuse the sentiment, news and fundamentals reports; the market analyst runs again because
                      its volatility range uses the holding period (--holding-days)
  --change question   reuse only the prefetched data (the user's question or emphasis changed: --mandate)
  --change language   reuse only the prefetched data (--language)

The variant keeps the source run's analysis date. A source from an earlier day needs --allow-older.
The memory log keeps the first decision recorded for a symbol and date, so a variant does not add another.

Usage:
  python3 .github/skills/investment-debate/scripts/variant_run.py --from reports/NASDAQ_IREN/2026-11-04 \
      --change position --position 20@45 [--mandate "..."]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
import run_plan  # noqa: E402
from prepare_run import rel  # noqa: E402

CHANGES = ("position", "horizon", "question", "language")


def reusable(settings: dict, change: str) -> list[str]:
    """Files (relative to the run) that stay valid after this change."""
    data = [f"0_data/{name}" for name in ("manifest.json", "stock_info.json", "bars_1D.csv", "bars_1W.csv")]
    data += [f"0_data/{a}.md" for a in run_plan.ANALYSTS if not (change == "horizon" and a == "market")]
    data += ["1_analysts/bars_1D.json", "1_analysts/bars_1W.json"]
    if change in ("question", "language"):
        return data
    stages = run_plan.plan(settings)
    if change == "horizon":
        keep = [f"1_analysts/{a}.md" for a in run_plan.ANALYSTS if a != "market"]
    else:  # position
        keep = [f"1_analysts/{a}.md" for a in run_plan.ANALYSTS]
        keep += [s.output for s in stages if s.kind in ("debate", "manager")]
    return data + keep


def next_variant(src: Path) -> Path:
    base = re.sub(r"-v\d+$", "", src.name)
    n = 2
    while (src.parent / f"{base}-v{n}").exists():
        n += 1
    return src.parent / f"{base}-v{n}"


def variant_instrument(text: str, settings: dict, src: Path, change: str, mandate: str | None, reused: list[str]) -> str:
    text = re.sub(r"^- Holding period: .*$", f"- Holding period: {settings['holding_days']} trading days "
                  "(use it as horizon_days in technicals price_statistics)", text, flags=re.M)
    text = re.sub(r"^- Output language: .*$", f"- Output language: {settings['language']}", text, flags=re.M)
    text = re.sub(r"\n## Variant\n.*?(?=\n## |\Z)", "\n", text, flags=re.S)
    if mandate:
        if "## Research mandate" in text:
            text = re.sub(r"(## Research mandate\n\n).*?(?=\n## |\Z)", lambda m: m.group(1) + mandate.strip() + "\n",
                          text, flags=re.S)
        else:
            text = text.rstrip() + "\n\n## Research mandate\n\n" + mandate.strip() + "\n"
    stages = sorted({p.removesuffix(".md") for p in reused if p.startswith(("1_", "2_")) and p.endswith(".md")})
    return (text.rstrip() + f"\n\n## Variant\n\nThis run is a follow-up to `{rel(src)}` (change: {change}), with the same analysis "
            f"date. It reuses {', '.join(stages) if stages else 'only the prefetched data'} from that run; every later "
            "stage is new.\n")


def make_variant(src: Path, change: str, holding_days: int | None = None, language: str | None = None,
                 mandate: str | None = None, position: str | None = None, allow_older: bool = False,
                 today: str | None = None, setup=None) -> dict:
    settings = run_plan.load_settings(src)
    if not settings:
        raise FileNotFoundError(f"{src}/0_settings.json is missing")
    today = today or date.today().isoformat()
    if settings["as_of"] != today and not allow_older:
        raise ValueError(f"{rel(src)} is from {settings['as_of']}, not today ({today}). Offer a fresh run, or pass "
                         "--allow-older if the user wants to reuse it.")
    if change == "horizon" and not holding_days:
        raise ValueError("--change horizon needs --holding-days")
    if change == "language" and not language:
        raise ValueError("--change language needs --language")
    if change == "position" and not (position or mandate):
        raise ValueError("--change position needs --position (or a --mandate describing the new context)")

    new = dict(settings)
    new.update(run_date=today, fresh=False, derived_from=rel(src),
               variant={"change": change, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if holding_days:
        new["holding_days"] = holding_days
    if language:
        new["language"] = language
    files = [f for f in reusable(settings, change) if (src / f).exists() and (src / f).stat().st_size > 0]
    if change == "position":
        missing = [s.output for s in run_plan.plan(settings) if s.kind in ("analyst", "debate", "manager")
                   and not (src / s.output).exists()]
        if missing:
            raise ValueError(f"{rel(src)} has not finished its research stages ({', '.join(missing)}); resume it instead")

    dst = next_variant(src)
    dst.mkdir(parents=True)
    for name in files:
        (dst / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / name, dst / name)
    for analyst in run_plan.ANALYSTS:  # placeholders of analysts that were not selected stay placeholders
        if analyst not in settings.get("analysts", []) and (src / "1_analysts" / f"{analyst}.md").exists():
            (dst / "1_analysts").mkdir(exist_ok=True)
            shutil.copy2(src / "1_analysts" / f"{analyst}.md", dst / "1_analysts" / f"{analyst}.md")
    new["variant"]["reused"] = files
    (dst / "0_settings.json").write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    instrument = (src / "0_instrument.md").read_text(encoding="utf-8") if (src / "0_instrument.md").exists() else ""
    (dst / "0_instrument.md").write_text(variant_instrument(instrument, new, src, change, mandate, files), encoding="utf-8")

    setup_out = (setup or _default_setup)(dst, new, position)
    return {"run": rel(dst), "derived_from": rel(src), "change": change, "reused": files,
            "resume_from": run_plan.resume_point(dst), "setup": setup_out}


def _default_setup(run: Path, settings: dict, position: str | None) -> dict:
    """Write 0_portfolio.md and 0_past_context.md (and missing data bundles) with prepare_run.py."""
    import prepare_run  # noqa: PLC0415
    import prefetch_data  # noqa: PLC0415
    argv = ["--symbol", settings["symbol"], "--run-dir", str(run)] + (["--position", position] if position else [])
    out = prepare_run.prepare(prepare_run.parser().parse_args(argv))
    out["prefetch"] = prefetch_data.prefetch(run)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="src", required=True, type=Path, help="the finished run to build on")
    parser.add_argument("--change", required=True, choices=CHANGES)
    parser.add_argument("--position", help="QTY@AVG_COST stated by the user (with --change position)")
    parser.add_argument("--holding-days", type=int)
    parser.add_argument("--language")
    parser.add_argument("--mandate", help="the follow-up question, for every role that runs again")
    parser.add_argument("--allow-older", action="store_true", help="reuse a run from an earlier day")
    parser.add_argument("--today", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    src = args.src if args.src.is_absolute() else (Path.cwd() / args.src)
    try:
        out = make_variant(src, args.change, args.holding_days, args.language, args.mandate, args.position,
                           args.allow_older, args.today)
    except (FileNotFoundError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, indent=1))
        return 2
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
