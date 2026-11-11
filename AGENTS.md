# OCR-MCP agent guide

Default workflow: lightweight HTTP-only OCR using an external Halogen/OvisOCR3 or
OpenAI-compatible service. Python 3.12+, uv. Use the existing checkout; cloud tasks
are isolated, so do not create worktrees unless requested.

- Install: `uv sync --frozen --extra dev`
- Run: `uv run --frozen ocr-mcp` (HTTP `/mcp`, default 127.0.0.1:10859)
- Config: `.env.example`, `src/ocr_mcp/core/http_config.py`
- Tests: `uv run --frozen pytest -c pyproject.toml --confcutdir=tests/http_only tests/http_only -o addopts='' -q`
- Lint: `uv run --frozen ruff check src/ocr_mcp/http_server.py src/ocr_mcp/document_pipeline.py src/ocr_mcp/core/http_config.py src/ocr_mcp/core/backend.py src/ocr_mcp/backends/ovisocr3_backend.py src/ocr_mcp/document_tasks.py src/ocr_mcp/task_prompts.py src/ocr_mcp/output_adapters.py src/ocr_mcp/recursive_tables.py src/ocr_mcp/artifacts.py tests/http_only`

Do not import legacy model/scanner/optimizer modules into the lightweight entry
point. Keep real model confidence null when the provider does not supply it.
Return page errors alongside successful PDF pages. Never fall back to another
model. Do not log image data, upstream response bodies or API keys.

Legacy functionality requires `--extra full`; its entry point is `ocr-mcp-full`.
Legacy test fixtures also need `--extra legacy-dev`. See README before following
older install/entry point instructions elsewhere in the repo.

Tool exposure: OCR_MCP_PROFILE=basic (two tools, default) or document (adds
analyze_document, extract_information, reconstruct_table). OCR_MCP_TOOLS can set
an explicit comma-separated allowlist. Prompts are vendored from a pinned model
revision under src/ocr_mcp/prompts with Apache-2.0 attribution. Preserve raw task
outputs; validate task JSON. `reconstruct_table` supports one adjacent two-page PDF boundary. The optional
`crosspage` extra/profile adds `reconstruct_document_tables` for recursive ranges.
Its pinned upstream implementation is under src/ocr_mcp/_vendor/ovisocr3: do not
edit it or instantiate VllmBackend. Adapt via recursive_tables.py. Preserve source
hashes/licensing and the separate base dependency boundary. Optional tests:
`uv run --frozen --extra dev --extra crosspage pytest -c pyproject.toml --confcutdir=tests/crosspage tests/crosspage -o addopts='' -q`.
Default/document profiles must not require browser dependencies. No environment
publication or Git push is implied by code changes.

HTTP file access is confined to OCR_DOCUMENT_ROOT (default ./documents, Docker
/documents); blank configuration must never disable confinement. PDF auto remains
conservative; text/ocr strategies are explicit, and force_ocr takes precedence.
Artifact jobs are private/marked/locked. Cleanup runs independently using
ocr-mcp-clean-artifacts (preview) or --apply; never scan/prune in OCR requests,
follow symlinks, delete unmarked directories or evict active jobs.
