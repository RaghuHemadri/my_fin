#!/usr/bin/env python3
"""Install every Claude Trading Skills and Finance Skills skill into .github/skills, adapted to this workspace's MCP
data servers.

  python3 tools/install_skills.py --fetch      clone upstream at the commits in tools/upstream.lock.json, then install
  python3 tools/install_skills.py              reinstall from vendor/ (after you pulled upstream changes)
  python3 tools/install_skills.py --refresh    regenerate adapter blocks, agent notes and docs in place (no vendor/)
  python3 tools/install_skills.py --update-lock  record vendor/ HEAD commits in tools/upstream.lock.json

A full install copies each skill in tools/skills_manifest.json (and any new upstream skill) into .github/skills/, points
upstream paths (skills/<name>/, ~/.claude/skills/) at .github/skills/, injects an adapter block into each SKILL.md,
marks opt-in skills as manual-only, ports the Claude Trading Skills agents, commands, workflows, skillsets, skills index
and state folders, and writes docs/skill-coverage.md. Agent tool lists use the servers configured in .mcp.json.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
CTS = VENDOR / "claude-trading-skills"
FS = VENDOR / "finance-skills"
SKILLS = ROOT / ".github" / "skills"
AGENTS = ROOT / ".github" / "agents"
PROMPTS = ROOT / ".github" / "prompts"
MANIFEST = Path(__file__).resolve().parent / "skills_manifest.json"
LOCK = Path(__file__).resolve().parent / "upstream.lock.json"
OWN_SKILLS = {"investment-debate", "trading-workflow"}
START, END = "<!-- tradingview-adapter:start -->", "<!-- tradingview-adapter:end -->"
PORT_MARK = "Ported from Claude Trading Skills by `tools/install_skills.py`"

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
SCRIPT_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".sh"}
NETWORK_RE = re.compile(
    r"import requests|from requests|requests\.(get|post|Session)|urllib\.request|urlopen|http\.client|"
    r"import httpx|aiohttp|yfinance|fmp_client|_fmp_compat|financialmodelingprep|alpaca|ccxt|websocket|"
    r"coingecko|binance|fxmacrodata|api\.stlouisfed|finviz\.com|\bfetch\(|curl |wget |opencli|\btdl\b",
    re.I,
)
PROVIDERS = {
    "FMP": r"fmp_client|financialmodelingprep|FMP_API_KEY|_fmp_compat",
    "Alpaca": r"alpaca",
    "yfinance": r"yfinance",
    "FinViz": r"finviz",
    "CoinGecko": r"coingecko",
    "Binance": r"binance",
    "FXMacroData": r"fxmacrodata",
    "opencli": r"opencli",
    "hosted CSV files": r"githubusercontent",
}
OFFLINE_FLAG_RE = re.compile(
    r"add_argument\(\s*['\"](--(?:[a-z0-9-]*-)?(?:json|csv|input|prices|offline|file)[a-z0-9-]*)['\"]", re.I
)
STATUS_TEXT = {
    "native": "Native. It needs no external market data.",
    "adapted": "Adapted. Get its data from the workspace MCP servers as described here, not from the upstream source.",
    "partial": "Partly adapted. The workspace MCP servers cover part of its data; report the rest as a gap.",
    "user-files": "Needs files or software that you provide.",
    "opt-in": (
        "Opt-in only. It uses a data source outside this workspace's MCP data servers. Use it only when the user "
        "explicitly asks for this skill or its source by name, and label every figure with that source. Otherwise, "
        "say that the data is not available from the configured servers."
    ),
    "unreviewed": (
        "Unreviewed: a new upstream skill with no manifest entry yet. Follow `.github/tradingview-adapter.md` and "
        "do not run scripts that call external services."
    ),
}
AGENT_SOURCES = [
    # (folder in Claude Trading Skills, user-invocable, text replacements)
    ("agents", False, {}),
    ("examples/weekly-trade-strategy/.claude/agents", False, {}),
    ("examples/daily-market-dashboard/.claude/agents", True, {"knowledge/": "reports/dashboard/"}),
]


def configured_servers() -> list[str]:
    """MCP data servers of this workspace, from .mcp.json (or .vscode/mcp.json)."""
    for path, key in ((ROOT / ".mcp.json", "mcpServers"), (ROOT / ".vscode" / "mcp.json", "servers")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        servers = data.get(key, data) if isinstance(data, dict) else {}
        names = [name for name, spec in servers.items() if isinstance(spec, dict)]
        if names:
            return names
    return ["tradingview"]


def agent_tools() -> str:
    tools = ["read", "search", "edit", "execute"] + [f"{name}/*" for name in configured_servers()]
    return "tools: [" + ", ".join(f"'{t}'" for t in tools) + "]"


def split_frontmatter(text: str) -> tuple[str | None, str]:
    match = FRONTMATTER_RE.match(text)
    return (match.group(1), text[match.end():]) if match else (None, text)


def frontmatter_fields(frontmatter: str) -> dict:
    try:
        import yaml  # optional; used only for validation
    except ImportError:
        name = re.search(r"^name:\s*(\S+)", frontmatter, re.M)
        return {"name": name.group(1).strip("'\"") if name else None, "description": None}
    data = yaml.safe_load(frontmatter) or {}
    return data if isinstance(data, dict) else {}


def scan_scripts(skill_dir: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    """Network scripts, local scripts, providers used, offline-input flags of the network scripts."""
    network, local, providers, flags = [], [], set(), set()
    for path in sorted(skill_dir.rglob("*")):
        rel = path.relative_to(skill_dir).as_posix()
        if (not path.is_file() or path.suffix not in SCRIPT_SUFFIXES or "/tests/" in f"/{rel}"
                or path.name.startswith("test_") or path.name == "conftest.py"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if NETWORK_RE.search(text):
            network.append(rel)
            providers.update(name for name, pattern in PROVIDERS.items() if re.search(pattern, text, re.I))
            flags.update(OFFLINE_FLAG_RE.findall(text))
        else:
            local.append(rel)
    return network, local, sorted(providers), sorted(flags)


def classify(entry: dict, skill_dir: Path) -> tuple[list[str], list[str], list[str], list[str]]:
    network, local, providers, flags = scan_scripts(skill_dir)
    if "net_scripts" in entry:  # the automatic scan was a false positive
        local, network = sorted(set(local) | set(network) - set(entry["net_scripts"])), entry["net_scripts"]
        flags = flags if network else []
    return network, local, providers, flags


def join_words(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def adapter_block(entry: dict, network: list[str], local: list[str], providers: list[str], flags: list[str]) -> str:
    name, status = entry["name"], entry["status"]
    servers = join_words([f"`{s}`" for s in configured_servers()])
    lines = [
        f"**Workspace adapter.** Market data comes only from this workspace's MCP data servers ({servers}). "
        "Tool names below are canonical TradingView MCP names; `.github/tradingview-adapter.md` maps each one to "
        "the configured servers. `tools/install_skills.py` added this block; the upstream text below is unchanged "
        "except that skill paths point to `.github/skills/`.",
        "",
        f"- **Status:** {STATUS_TEXT[status]}",
        f"- **Upstream data:** {entry.get('upstream') or 'not recorded'}.",
    ]
    if entry.get("use"):
        lines.append(f"- **In this workspace:** {entry['use']}")
    if entry.get("gaps"):
        lines.append(f"- **Gaps:** {entry['gaps']}")
    if network:
        called = join_words(providers) if providers else "external services"
        listed = join_words([f"`{script}`" for script in network])
        if status == "opt-in":
            verb = "calls" if len(network) == 1 else "call"
            lines.append(f"- **Scripts:** {listed} {verb} {called}. Run them only after the user opts in.")
        else:
            verb = "it calls" if len(network) == 1 else "they call"
            lines.append((f"- **Scripts:** Do not run {listed}; {verb} {called}."
                          + (" The other bundled scripts are local and may run." if local else "")))
            if flags:
                lines.append(
                    "- **Offline inputs:** some of those scripts can read local files instead ("
                    + join_words([f"`{flag}`" for flag in flags])
                    + "). You may run them that way with data you fetched from the MCP servers, with any FMP or "
                    "Alpaca keys unset. Stop if a script still tries to reach the network."
                )
    elif local:
        lines.append("- **Scripts:** the bundled scripts are local and may run.")
    lines.append(
        f"- **Always:** follow `.github/tradingview-adapter.md`. Never invent data. Never place orders. "
        f"Unless the skill names another location, write outputs under `reports/{name}/`."
    )
    quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
    return f"{START}\n> [!IMPORTANT]\n{quoted}\n{END}\n"


def agent_note() -> str:
    servers = join_words([f"`{s}`" for s in configured_servers()])
    return (
        f"> [!IMPORTANT]\n> **Workspace adapter.** {PORT_MARK}. Market data comes only from this workspace's MCP data "
        f"servers ({servers}). Where this text says WebSearch or WebFetch, use the news tools; where it expects "
        "chart images, use price history and technical-rating tools, plus any screenshots the user attaches. "
        "`.github/tradingview-adapter.md` maps canonical tool names to the configured servers. Ignore instructions "
        "specific to Claude Code, Streamlit or the Claude Agent SDK. Never invent data. Never place orders.\n"
    )


def rewrite_paths(text: str, names: set[str]) -> str:
    text = text.replace("~/.claude/skills/", ".github/skills/")
    return re.sub(
        r"(?<![\w./-])skills/([a-z0-9-]+)/",
        lambda m: f".github/skills/{m.group(1)}/" if m.group(1) in names else m.group(0),
        text,
    )


def source_dir(entry: dict) -> Path:
    if entry["repo"] == "claude-trading-skills":
        return CTS / "skills" / entry["name"]
    return FS / entry["path"]


def upstream_skill_dirs() -> dict[str, Path]:
    found = {p.parent.name: p.parent for p in (CTS / "skills").glob("*/SKILL.md")}
    found.update({p.parent.name: p.parent for p in FS.glob("plugins/*/skills/*/SKILL.md")})
    return found


def write_skill_md(entry: dict, skill_dir: Path, problems: list[str]) -> dict:
    """Insert or replace the adapter block and the opt-in flag in an installed SKILL.md."""
    network, local, providers, flags = classify(entry, skill_dir)
    skill_md = skill_dir / "SKILL.md"
    frontmatter, body = split_frontmatter(skill_md.read_text(encoding="utf-8"))
    if frontmatter is None:
        problems.append(f"{entry['name']}: SKILL.md has no frontmatter")
        return {}
    if entry["status"] == "opt-in" and not re.search(r"^disable-model-invocation:", frontmatter, re.M):
        frontmatter += "\ndisable-model-invocation: true"
    body = re.sub(rf"\A\s*{re.escape(START)}.*?{re.escape(END)}\n?", "", body, flags=re.S)
    block = adapter_block(entry, network, local, providers, flags)
    skill_md.write_text(f"---\n{frontmatter}\n---\n\n{block}\n{body.lstrip()}", encoding="utf-8")

    fields = frontmatter_fields(frontmatter)
    if fields.get("name") != entry["name"] or not NAME_RE.match(entry["name"]):
        problems.append(f"{entry['name']}: frontmatter name {fields.get('name')!r} does not match its folder")
    description = " ".join(str(fields.get("description") or "").split())
    if fields.get("description") is not None and not 0 < len(description) <= 1024:
        problems.append(f"{entry['name']}: description length {len(description)} is outside 1-1024")
    return {"network": network, "providers": providers}


def install_skill(entry: dict, names: set[str], problems: list[str]) -> dict:
    src, dest = source_dir(entry), SKILLS / entry["name"]
    if not (src / "SKILL.md").is_file():
        problems.append(f"{entry['name']}: source not found at {src}")
        return {}
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", ".DS_Store"))
    for md in dest.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        rewritten = rewrite_paths(text, names)
        if rewritten != text:
            md.write_text(rewritten, encoding="utf-8")
    return write_skill_md(entry, dest, problems)


def agent_header(frontmatter: str, fallback_name: str) -> tuple[str, str]:
    """Name and a one-paragraph description from Claude-format agent frontmatter.

    Upstream descriptions are often invalid strict YAML (unquoted 'Context:' inside <example> blocks with literal
    \\n escapes), which Copilot would reject. Read them as text, keep the summary before the examples, and the
    caller re-emits it as a quoted scalar.
    """
    name = re.search(r"^name:\s*(.+)$", frontmatter, re.M)
    collecting, parts = False, []
    for line in frontmatter.splitlines():
        if line.startswith("description:"):
            collecting = True
            value = line[len("description:"):].strip()
            if value not in (">", "|", ">-", "|-", ">+", "|+"):
                parts.append(value)
        elif collecting and (line.startswith((" ", "\t")) or not line.strip()):
            parts.append(line.strip())
        elif collecting:
            break
    text = " ".join(p for p in parts if p)
    if len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    text = re.split(r"\\n\\n<example>|<example>|\\n\\nExamples?:", text)[0]
    text = " ".join(text.replace("\\n", " ").split())
    text = re.sub(r"\s*[^.]*:\s*$", "", text) or text
    if len(text) > 1000:
        text = text[:997].rsplit(" ", 1)[0] + "..."
    return (name.group(1).strip().strip("'\"") if name else fallback_name), text


def port_agents_and_commands() -> list[str]:
    written = []
    AGENTS.mkdir(parents=True, exist_ok=True)
    PROMPTS.mkdir(parents=True, exist_ok=True)
    skill_names = set(upstream_skill_dirs())
    for folder, invocable, replacements in AGENT_SOURCES:
        for src in sorted((CTS / folder).glob("*.md")):
            frontmatter, body = split_frontmatter(src.read_text(encoding="utf-8"))
            name, description = agent_header(frontmatter or "", src.stem)
            body = rewrite_paths(body, skill_names)
            for old, new in replacements.items():
                description, body = description.replace(old, new), body.replace(old, new)
            header = [f"name: {name}", f"description: {json.dumps(description, ensure_ascii=False)}",
                      agent_tools(), f"user-invocable: {'true' if invocable else 'false'}"]
            dest = AGENTS / f"{src.stem}.agent.md"
            dest.write_text("---\n" + "\n".join(header) + "\n---\n\n" + agent_note() + "\n" + body.lstrip(),
                            encoding="utf-8")
            written.append(dest.relative_to(ROOT).as_posix())
    for src in sorted((CTS / "commands").glob("*.md")):
        frontmatter, body = split_frontmatter(src.read_text(encoding="utf-8"))
        frontmatter = (frontmatter or "").rstrip() + "\nagent: agent"
        body = rewrite_paths(body, skill_names).replace("$ARGUMENTS", "the text the user typed after the command")
        dest = PROMPTS / f"cts-{src.stem}.prompt.md"
        dest.write_text(f"---\n{frontmatter}\n---\n\n{agent_note()}\n{body.lstrip()}", encoding="utf-8")
        written.append(dest.relative_to(ROOT).as_posix())
    return written


def refresh_ported() -> list[str]:
    """Update the tool list and adapter note of already ported agents and prompts, without vendor/."""
    note_re = re.compile(r"> \[!IMPORTANT\]\n> \*\*Workspace adapter\.\*\* Ported from Claude Trading Skills.*?\n(?=\n)",
                         re.S)
    written = []
    for path in sorted(AGENTS.glob("*.agent.md")) + sorted(PROMPTS.glob("cts-*.prompt.md")):
        text = path.read_text(encoding="utf-8")
        if "Ported from Claude Trading Skills" not in text:
            continue
        updated = note_re.sub(lambda _: agent_note(), text, count=1)
        if path.name.endswith(".agent.md"):
            updated = re.sub(r"^tools:.*$", agent_tools(), updated, count=1, flags=re.M)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            written.append(path.relative_to(ROOT).as_posix())
    return written


def copy_trading_os() -> list[str]:
    """The skills index, workflows and skillsets the navigator and workflow runner read, plus state folders."""
    shutil.copy2(CTS / "skills-index.yaml", ROOT / "skills-index.yaml")
    for folder in ("workflows", "skillsets"):
        if (ROOT / folder).exists():
            shutil.rmtree(ROOT / folder)
        shutil.copytree(CTS / folder, ROOT / folder)
    for folder in ("state/journal", "state/theses"):
        (ROOT / folder).mkdir(parents=True, exist_ok=True)
        (ROOT / folder / ".gitkeep").touch()
    return ["skills-index.yaml", "workflows/", "skillsets/", "state/journal/", "state/theses/"]


def write_coverage(entries: list[dict], scans: dict[str, dict]) -> None:
    order = ["native", "adapted", "partial", "user-files", "opt-in", "unreviewed"]
    counts = {status: sum(e["status"] == status for e in entries) for status in order}
    lines = [
        "# Skill coverage",
        "",
        "Generated by `tools/install_skills.py` from `tools/skills_manifest.json`. Do not edit by hand.",
        "",
        f"{len(entries)} upstream skills are installed in `.github/skills/`, plus this workspace's own "
        "`investment-debate` and `trading-workflow` skills. Tool names are canonical TradingView MCP names; "
        "[tradingview-adapter.md](../.github/tradingview-adapter.md) maps them to the configured MCP servers.",
        "",
        "| Status | Count | Meaning |",
        "| --- | --- | --- |",
    ]
    meaning = {
        "native": "Needs no external market data; runs as upstream.",
        "adapted": "Upstream data source replaced by the workspace MCP servers.",
        "partial": "The workspace MCP servers cover part of the data; the rest is reported as a gap.",
        "user-files": "Needs files or software you provide (charts, MT5, manifoldbt).",
        "opt-in": "Needs a source outside the workspace MCP servers; manual invocation only (`disable-model-invocation`).",
        "unreviewed": "New upstream skill without a manifest entry yet.",
    }
    lines += [f"| {s} | {counts[s]} | {meaning[s]} |" for s in order if counts[s]]
    lines += ["", "| Skill | Source | Status | Upstream data | In this workspace | Gaps | Scripts not run |",
              "| --- | --- | --- | --- | --- | --- | --- |"]

    def cell(text: str | None) -> str:
        return (text or "").replace("|", "\\|").replace("\n", " ")

    for e in sorted(entries, key=lambda e: (e["repo"], e["name"])):
        source = "Claude Trading Skills" if e["repo"] == "claude-trading-skills" else "Finance Skills"
        blocked = ", ".join(f"`{s}`" for s in scans.get(e["name"], {}).get("network", [])) or "none"
        lines.append(f"| `{e['name']}` | {source} | {e['status']} | {cell(e.get('upstream'))} | "
                     f"{cell(e.get('use')) or '-'} | {cell(e.get('gaps')) or '-'} | {blocked} |")
    out = ROOT / "docs" / "skill-coverage.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def fetch_upstream() -> list[str]:
    """Check out each upstream repository at its locked commit in vendor/ (shallow)."""
    lock = json.loads(LOCK.read_text(encoding="utf-8"))["repos"]
    done = []
    for name, spec in lock.items():
        repo = VENDOR / name
        if not (repo / ".git").exists():
            repo.mkdir(parents=True, exist_ok=True)
            git("init", "-q", cwd=repo)
            git("remote", "add", "origin", spec["url"], cwd=repo)
        current = git("rev-parse", "HEAD", cwd=repo) if _has_commit(repo) else ""
        if current != spec["commit"]:
            git("fetch", "-q", "--depth", "1", "origin", spec["commit"], cwd=repo)
            git("checkout", "-q", "--detach", spec["commit"], cwd=repo)
        done.append(f"{name}@{spec['commit'][:12]}")
    return done


def _has_commit(repo: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=repo, capture_output=True).returncode == 0


def update_lock() -> dict:
    data = json.loads(LOCK.read_text(encoding="utf-8"))
    for name, spec in data["repos"].items():
        spec["commit"] = git("rev-parse", "HEAD", cwd=VENDOR / name)
    LOCK.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {name: spec["commit"] for name, spec in data["repos"].items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true", help="check out the locked upstream commits into vendor/ first")
    mode.add_argument("--refresh", action="store_true",
                      help="regenerate adapter blocks, ported-agent notes and docs in place; vendor/ not needed")
    mode.add_argument("--update-lock", action="store_true", help="record vendor/ HEAD commits in the lock file")
    args = parser.parse_args(argv)

    if args.update_lock:
        print(json.dumps({"lock": update_lock()}, indent=2))
        return 0
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))["skills"]
    problems: list[str] = []

    if args.refresh:
        scans = {}
        for e in entries:
            if (SKILLS / e["name"] / "SKILL.md").is_file():
                scans[e["name"]] = write_skill_md(e, SKILLS / e["name"], problems)
            else:
                problems.append(f"{e['name']}: not installed; run a full install")
        write_coverage([e for e in entries if e["name"] in scans], scans)
        print(json.dumps({"refreshed_skills": len(scans), "servers": configured_servers(),
                          "ported_updated": refresh_ported(), "problems": problems}, indent=2))
        return 1 if problems else 0

    fetched = fetch_upstream() if args.fetch else []
    if not (CTS / "skills").is_dir() or not (FS / "plugins").is_dir():
        parser.error("vendor/ is missing: run python3 tools/install_skills.py --fetch")
    upstream = upstream_skill_dirs()
    known = {e["name"] for e in entries}
    for name, path in sorted(upstream.items()):
        if name not in known:
            repo = "claude-trading-skills" if CTS in path.parents else "finance-skills"
            entry = {"name": name, "repo": repo, "status": "unreviewed", "upstream": "", "use": "", "gaps": ""}
            if repo == "finance-skills":
                entry["path"] = path.relative_to(FS).as_posix()
            entries.append(entry)
            problems.append(f"{name}: new upstream skill with no manifest entry (installed as unreviewed)")
    for name in sorted(known - set(upstream)):
        problems.append(f"{name}: in the manifest but no longer upstream (skipped)")
    entries = [e for e in entries if e["name"] in upstream]
    clashes = OWN_SKILLS & {e["name"] for e in entries}
    if clashes:
        parser.error(f"upstream skill names clash with workspace skills: {sorted(clashes)}")

    SKILLS.mkdir(parents=True, exist_ok=True)
    names = {e["name"] for e in entries} | OWN_SKILLS
    scans = {e["name"]: install_skill(e, names, problems) for e in entries}
    ported = port_agents_and_commands()
    copied = copy_trading_os()
    write_coverage(entries, scans)

    by_status: dict[str, int] = {}
    for e in entries:
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
    print(json.dumps({"fetched": fetched, "installed_skills": len(entries), "by_status": by_status,
                      "servers": configured_servers(), "ported": ported, "copied": copied,
                      "coverage": "docs/skill-coverage.md", "problems": problems}, indent=2))
    severe = ("no frontmatter", "does not match", "description length", "not found")
    return 1 if any(s in p for p in problems for s in severe) else 0


if __name__ == "__main__":
    sys.exit(main())
