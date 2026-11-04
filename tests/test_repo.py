"""Repository integrity: skills, agents, prompts, adapter blocks, manifest coverage and links."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".github" / "skills"
AGENTS = ROOT / ".github" / "agents"
PROMPTS = ROOT / ".github" / "prompts"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "tools"))
from install_skills import OWN_SKILLS  # noqa: E402  (skills this repo writes itself)
FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
BUILTIN_TOOLS = {"read", "search", "edit", "execute", "agent", "web", "todo"}


def frontmatter(path: Path) -> dict:
    match = FRONTMATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError(f"{path.relative_to(ROOT)} has no frontmatter")
    data = yaml.safe_load(match.group(1))
    if not isinstance(data, dict):
        raise AssertionError(f"{path.relative_to(ROOT)} frontmatter is not a mapping")
    return data


def configured_servers() -> set[str]:
    data = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    return set(data.get("mcpServers", data))


@unittest.skipIf(yaml is None, "PyYAML is required: pip install pyyaml")
class RepositoryTest(unittest.TestCase):
    def test_skills(self):
        manifest = {e["name"]: e for e in json.loads((ROOT / "tools" / "skills_manifest.json").read_text())["skills"]}
        installed = sorted(p.parent for p in SKILLS.glob("*/SKILL.md"))
        self.assertGreaterEqual(len(installed), 100)
        for skill_dir in installed:
            with self.subTest(skill=skill_dir.name):
                meta = frontmatter(skill_dir / "SKILL.md")
                self.assertEqual(meta.get("name"), skill_dir.name)
                self.assertRegex(skill_dir.name, r"^[a-z0-9-]{1,64}$")
                description = " ".join(str(meta.get("description", "")).split())
                self.assertTrue(0 < len(description) <= 1024, f"description length {len(description)}")
                if skill_dir.name in OWN_SKILLS:
                    continue
                self.assertIn(skill_dir.name, manifest, "installed skill missing from tools/skills_manifest.json")
                text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                self.assertEqual(text.count("<!-- tradingview-adapter:start -->"), 1)
                if manifest[skill_dir.name]["status"] == "opt-in":
                    self.assertIs(meta.get("disable-model-invocation"), True)
        self.assertEqual(set(manifest) - {p.name for p in installed}, set(), "manifest skills not installed")

    def test_agents_use_configured_servers(self):
        servers = configured_servers()
        agents = sorted(AGENTS.glob("*.agent.md"))
        self.assertGreaterEqual(len(agents), 20)
        for path in agents:
            with self.subTest(agent=path.name):
                meta = frontmatter(path)
                self.assertEqual(meta.get("name"), path.name[: -len(".agent.md")])
                self.assertTrue(meta.get("description"))
                self.assertIsInstance(meta.get("tools"), list)
                for tool in meta["tools"]:
                    if tool not in BUILTIN_TOOLS:
                        self.assertIn(tool.split("/")[0], servers, f"{tool} is not a configured MCP server")

    def test_prompts(self):
        for path in sorted(PROMPTS.glob("*.prompt.md")):
            with self.subTest(prompt=path.name):
                self.assertTrue(frontmatter(path).get("description"))
                self.assertNotIn("$ARGUMENTS", path.read_text(encoding="utf-8"))

    def test_debate_roles_exist(self):
        skill = (SKILLS / "investment-debate" / "SKILL.md").read_text(encoding="utf-8")
        agents = {p.name[: -len(".agent.md")] for p in AGENTS.glob("*.agent.md")}
        for role in set(re.findall(r"`(ta-[a-z-]+)`", skill)):
            with self.subTest(role=role):
                self.assertIn(role, agents)

    def test_mcp_configs_agree(self):
        vscode = json.loads((ROOT / ".vscode" / "mcp.json").read_text(encoding="utf-8"))["servers"]
        self.assertEqual(set(vscode), configured_servers())

    def test_relative_links_resolve(self):
        documents = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "THIRD_PARTY_NOTICES.md",
                     ROOT / ".github" / "copilot-instructions.md", ROOT / ".github" / "tradingview-adapter.md",
                     *(SKILLS / "investment-debate").rglob("*.md"), SKILLS / "trading-workflow" / "SKILL.md",
                     *PROMPTS.glob("*.prompt.md")]
        for doc in documents:
            if not doc.exists():
                continue
            for target in re.findall(r"\[[^\]]+\]\((?!https?://|#|mailto:)([^)\s]+)\)", doc.read_text(encoding="utf-8")):
                with self.subTest(doc=doc.relative_to(ROOT).as_posix(), link=target):
                    self.assertTrue((doc.parent / target.split("#")[0]).exists())

    def test_no_local_data_or_secrets_tracked_by_default(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("portfolio/*.csv", "reports/*", "memory/", "vendor/", "*.env"):
            self.assertIn(pattern, ignore)
        for example in ("holdings", "cash", "transactions"):
            self.assertTrue((ROOT / "portfolio" / f"{example}.example.csv").exists())


if __name__ == "__main__":
    unittest.main()
