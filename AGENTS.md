# OCR-MCP agent guide

Default workflow: lightweight HTTP-only OCR using an external Halogen/OvisOCR3 or
OpenAI-compatible service. Python 3.12+, uv. Use the existing checkout; cloud tasks
are isolated, so do not create worktrees unless requested.

- Install: `uv sync --frozen --extra dev`
- Run: `uv run --frozen ocr-mcp` (HTTP `/mcp`, default 127.0.0.1:10859)
- Config: `.env.example`, `src/ocr_mcp/core/http_config.py`
- Tests: `uv run --frozen pytest -c pyproject.toml --confcutdir=tests/http_only tests/http_only -o addopts='' -q`
- Lint: `uv run --frozen ruff check src/ocr_mcp/http_server.py src/ocr_mcp/document_pipeline.py src/ocr_mcp/core/http_config.py src/ocr_mcp/core/backend.py src/ocr_mcp/backends/ovisocr3_backend.py tests/http_only`

Do not import legacy model/scanner/optimizer modules into the lightweight entry
point. Keep real model confidence null when the provider does not supply it.
Return page errors alongside successful PDF pages. Never fall back to another
model. Do not log image data, upstream response bodies or API keys.

Legacy functionality requires `--extra full`; its entry point is `ocr-mcp-full`.
Legacy test fixtures also need `--extra legacy-dev`. See README before following
older install/entry point instructions elsewhere in the repo.
