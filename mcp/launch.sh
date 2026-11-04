#!/usr/bin/env bash
# Starts one of this project's local MCP servers over stdio.
# Usage: bash mcp/launch.sh [--direct] <tradingmcp|yahoo-finance|technicals>
# By default the server runs behind mcp/cache_proxy.py: the same tools, plus a response cache shared by
# every session and script on this machine (~/.cache/my_fin/my_fin.sqlite3). --direct starts the server
# itself (the proxy uses it); MY_FIN_CACHE=0 makes every start direct, which is the original behaviour.
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

DIRECT=0
if [ "${1:-}" = "--direct" ]; then
  DIRECT=1
  shift
fi
case "$(printf '%s' "${MY_FIN_CACHE:-1}" | tr '[:upper:]' '[:lower:]')" in
  0|off|false|no) DIRECT=1 ;;
esac

NAME="${1:-}"
case "$NAME" in
  tradingmcp|yahoo-finance|technicals) ;;
  *)
    echo "usage: bash mcp/launch.sh [--direct] <tradingmcp|yahoo-finance|technicals>" >&2
    exit 2
    ;;
esac

if ! command -v uvx >/dev/null 2>&1; then
  echo "uv is not installed. Run: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 127
fi

if [ "$DIRECT" = 0 ]; then
  exec uv run --quiet --script "$HERE/cache_proxy.py" --name "$NAME" -- bash "$HERE/launch.sh" --direct "$NAME"
fi

case "$NAME" in
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
esac
