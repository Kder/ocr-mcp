#!/usr/bin/env bash
# Start the OCR-MCP HTTP server outside the harness sandbox.
# The harness injects proxy env vars (HTTP(S)_PROXY / NO_PROXY with "[::1]")
# that crash httpx, so they are cleared here; the server only talks to 127.0.0.1.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ -x .venv/bin/ocr-mcp ] || env UV_PROJECT_ENVIRONMENT=.venv uv sync --frozen
exec env -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy \
         -u ALL_PROXY -u all_proxy -u SOCKS_PROXY -u socks_proxy \
         -u npm_config_noproxy -u UV_PROJECT_ENVIRONMENT \
         NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 \
         ./.venv/bin/ocr-mcp "$@"
