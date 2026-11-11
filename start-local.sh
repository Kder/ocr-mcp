#!/usr/bin/env bash
# Start the OCR-MCP HTTP server outside the harness sandbox.
# The harness injects proxy env vars (HTTP(S)_PROXY / NO_PROXY with "[::1]")
# that crash httpx, so they are cleared here; the server only talks to 127.0.0.1.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
[ -x .venv/bin/ocr-mcp ] || env UV_PROJECT_ENVIRONMENT=.venv uv sync --frozen

# The launching shell (or the harness) may already export OCR_*/OVISOCR3_*/MCP_* —
# often empty. python-dotenv never overrides an existing variable, so a stale or
# blank inherited value silently wins over .env (e.g. an empty OCR_DOCUMENT_ROOT
# falls back to ./documents and every real path is then rejected). Drop them so
# .env is the single source of truth.
while IFS='=' read -r name _; do
  case "$name" in
    OCR_* | OVISOCR3_* | MCP_HOST | MCP_PORT) unset "$name" || true ;;
  esac
done < <(env)

exec env -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy \
         -u ALL_PROXY -u all_proxy -u SOCKS_PROXY -u socks_proxy \
         -u npm_config_noproxy -u UV_PROJECT_ENVIRONMENT \
         NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 \
         ./.venv/bin/ocr-mcp "$@"
