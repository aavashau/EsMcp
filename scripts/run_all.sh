#!/usr/bin/env bash
# Starts the agent UI (which auto-spawns the MCP server as a subprocess via stdio).
# Do NOT use uvicorn --reload; it orphans the MCP subprocess.
set -e
cd "$(dirname "$0")/.."
echo "Starting Healthcare Agent UI on http://localhost:8000"
echo "MCP server will be spawned automatically."
uv run agent-ui
