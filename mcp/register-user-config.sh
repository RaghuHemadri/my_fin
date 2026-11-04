#!/usr/bin/env bash
# Registers this project's MCP servers in the user-level Copilot config (~/.copilot/mcp-config.json).
# The VS Code agent host and Copilot CLI sessions read that file at session start; in the VS Code
# agent host, the project's .mcp.json is not read. Existing servers in the file are kept.
# Usage: bash mcp/register-user-config.sh            add or update tradingmcp, yahoo-finance, technicals, tapetide
#        bash mcp/register-user-config.sh --remove   remove them again
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${COPILOT_MCP_CONFIG:-$HOME/.copilot/mcp-config.json}"
mkdir -p "$(dirname "$CONFIG")"

python3 - "$HERE/launch.sh" "$CONFIG" "${1:-}" <<'PY'
import json, os, sys
launch, path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
servers = cfg.setdefault("mcpServers", {})
names = ("tradingmcp", "yahoo-finance", "technicals", "tapetide")
if mode == "--remove":
    for n in names:
        servers.pop(n, None)
else:
    for n in names[:3]:
        servers[n] = {"type": "local", "command": "bash", "args": [launch, n], "tools": ["*"]}
    servers["tapetide"] = {"type": "http", "url": "https://mcp.tapetide.com/mcp", "tools": ["*"]}
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(("Removed" if mode == "--remove" else "Registered") + f" {', '.join(names)} in {path}")
PY
chmod 600 "$CONFIG"
echo "Start a new chat or session to load the change."
