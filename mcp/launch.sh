#!/usr/bin/env bash
# Starts one of this project's local MCP servers over stdio.
# Usage: bash mcp/launch.sh <tradingmcp|yahoo-finance|technicals>
# Secrets (optional) are read from ~/.config/my_fin/secrets.env, outside the repo.
# Nothing may be printed to stdout except MCP protocol messages.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
SECRETS="${MY_FIN_SECRETS:-$HOME/.config/my_fin/secrets.env}"
if [ -f "$SECRETS" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$SECRETS"
  set +a
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v uvx >/dev/null 2>&1; then
  echo "uv is not installed. Run: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 127
fi

case "${1:-}" in
  tradingmcp)
    # Self-hosted TradingMCP (github.com/atilaahmettaner/tradingview-mcp). Python package only:
    # npm packages with this name are malicious copies.
    exec uvx --python 3.12 --from "tradingview-mcp-server==0.8.1" tradingview-mcp
    ;;
  yahoo-finance)
    # github.com/Alex2Yang97/yahoo-finance-mcp, pinned to a reviewed commit. It needs Python 3.14,
    # which uv downloads on first run. Its startup banner goes to stdout, so drop that one line.
    exec > >(grep --line-buffered -v '^Starting Yahoo Finance MCP server')
    exec uvx --python 3.14 \
      --from "git+https://github.com/Alex2Yang97/yahoo-finance-mcp@b9c176547d3797b2196adeba0210ae67861ac589" \
      yahoo-finance-mcp
    ;;
  technicals)
    exec uv run --quiet --script "$HERE/market_technicals.py"
    ;;
  *)
    echo "usage: bash mcp/launch.sh <tradingmcp|yahoo-finance|technicals>" >&2
    exit 2
    ;;
esac
