# OCR-MCP — OvisOCR3 HTTP edition

Lightweight **Streamable HTTP MCP** service for images and PDFs. Calls an external
OpenAI-compatible vision endpoint, defaulting to **Halogen / OvisOCR3**. No local
weights, GPU runtime, scanner, web UI, Ollama or tool calling on the OCR model.
Python **3.12+**. MIT license.

```text
DSH → HTTP MCP /mcp → OCR-MCP → Halogen /v1/chat/completions → OvisOCR3
                         └── PyMuPDF: PDF text extraction / page rendering
```

## Install and run

From the checkout:

```bash
uv sync --frozen
cp .env.example .env
# Edit OVISOCR3_BASE_URL to point to your existing Halogen server.
uv run --frozen ocr-mcp
```

Or install the package with `pip install .`, then run `ocr-mcp`.
`python -m ocr_mcp` uses the same HTTP-only entry point. There is no transport
switch in the lightweight service. MCP endpoint: `http://127.0.0.1:10859/mcp`.
`--host` and `--port` override the listening address. Halogen must already serve
`OvisOCR3`; OCR-MCP does not deploy or download the model.

Base dependencies: FastMCP, httpx, Pillow, PyMuPDF, Pydantic, python-dotenv, and
their transitive dependencies. Torch, Transformers, EasyOCR, OpenCV, Diffusers,
NumPy, FastAPI, Tesseract and Poppler are **not required** by this edition.
PyMuPDF renders PDFs without a Poppler subprocess.

## Tools

Only **`process_document`** and **`get_status`** are registered.
Connect DSH using its Streamable HTTP MCP configuration and the endpoint above.
Example `process_document` arguments:

```json
{
  "operation": "process_document",
  "source_path": "/documents/scan.pdf",
  "backend": "ovisocr3",
  "ocr_mode": "markdown",
  "pages": [1, 3, 5],
  "dpi": 200,
  "force_ocr": false
}
```

- `source_path` is a **server-local file path**. DSH and OCR-MCP must share a
  volume/path, or the document must already exist on the MCP server. There is no
  arbitrary URL fetch or upload endpoint. Set `OCR_DOCUMENT_ROOT` to restrict paths.
- Images: PNG, JPEG, WebP, single-frame TIFF/BMP; decoded and sent as PNG. Optional
  `region: [x, y, width, height]` crops an image. Animated/multi-frame images are rejected.
- PDFs: 1-based page selection, deduplicated and sorted in original page order.
  Text-only pages use embedded text. Pages with raster images (including scans with
  digital headers) use full-page OCR. `force_ocr=true` renders every selected page.
  Embedded PDF text is plain text; its extraction does not reconstruct Markdown tables.
- `ocr_mode`: `markdown` (default), `text`, `format` or `auto`. Markdown/format/auto
  use the configurable prompt. HTML tables and LaTeX are returned verbatim.
- `backend`: `ovisocr3`, `openai-ocr`, and `auto` address the **same configured
  remote model**. There is no fallback to a GPU model or another engine.
- PDF results include `pages`, per-page `method`/errors, numbered combined `text`,
  `processed_pages` and `failed_pages`. Any failed page sets `success=false`;
  `partial_success=true` indicates retained successful pages. Callers must inspect
  these fields. No invented model confidence: `confidence=null`.
- `get_status({"check_upstream": true})` explicitly queries `/v1/models` and verifies
  the configured model ID. The default status only reports local configuration.
  A successful model-list check does not establish OCR quality or device placement.

## Configuration

Copy [.env.example](.env.example). Core settings:

| Variable | Default | Meaning |
|---|---|---|
| `OVISOCR3_BASE_URL` | `http://127.0.0.1:8731/v1` | OpenAI-compatible API root, including `/v1` |
| `OVISOCR3_MODEL` | `OvisOCR3` | Exact model ID; also configurable for other vision OCR servers |
| `OVISOCR3_API_KEY` | empty | Optional Bearer authentication |
| `OVISOCR3_TIMEOUT` | `180` | Per-page upstream timeout in seconds |
| `OVISOCR3_PROMPT` | `Convert this page to Markdown.` | Prompt for formatted OCR |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `10859` | MCP listening address |
| `OCR_DOCUMENT_ROOT` | unset | Optional permitted document directory |

Each OCR request contains one image and **no `tools`**. Requests to the upstream
are serialized across documents; PDF pages are rendered and released one at a time.
Failures are returned without upstream response bodies or document data. Requests
are not retried automatically, preventing duplicate long-running inference.
HTTP failures, timeouts, missing models, invalid responses and truncated completions
are explicit errors. When a response includes a `model`, it must match the configured
ID. The remote server remains responsible for honoring that ID and device placement.

Defaults limit input files to 50 MiB, decoded/rendered images to 25 million pixels,
selected PDF pages to 100 and OCR/extracted content to 1 million characters per document.
See `.env.example` for overrides. DPI range is 150–300. Password-protected PDFs are
unsupported. Long PDFs can exceed client timeouts/context limits: request fewer pages
per call. OCR caching, batch directories, advanced table parsing and output-file export
are outside this minimal edition.

## Container / Podman

```bash
podman build -t ocr-mcp-http .
podman run --rm -p 127.0.0.1:10859:10859 \
  -v ./documents:/documents:ro \
  -e OCR_DOCUMENT_ROOT=/documents \
  -e OVISOCR3_BASE_URL=http://host.containers.internal:8731/v1 \
  ocr-mcp-http
```

Inside a container, `127.0.0.1` refers to that container. Use an address reachable
from the OCR-MCP container (a shared container-network service name also works).
The image runs as a non-root user; mounted documents need read permission.
The lightweight endpoint has no built-in authentication: keep it bound to localhost
or put an authenticated reverse proxy in front of it when deploying remotely.
The container publishes only MCP; it includes no UI or local OCR binaries.

## Development and validation

```bash
uv sync --frozen --extra dev
uv run --frozen pytest -c pyproject.toml --confcutdir=tests/http_only tests/http_only -o addopts='' -q
uv run --frozen ruff check src/ocr_mcp/http_server.py src/ocr_mcp/document_pipeline.py \
  src/ocr_mcp/core/http_config.py src/ocr_mcp/core/backend.py \
  src/ocr_mcp/backends/ovisocr3_backend.py tests/http_only
```

The isolated test root avoids importing the legacy suite's heavy fixtures. Tests
cover the API contract, image formats/cropping, errors, PDF page ordering and partial
failures, mixed PDFs, resource limits, and an installed HTTP server over TCP against
a simulated Halogen endpoint. They do **not** validate actual OvisOCR3 recognition
accuracy or NPU execution. For real deployment, run `get_status(check_upstream=true)`
and process a known image and mixed PDF through your Halogen instance.

## Legacy full application

Existing local backends, scanner, web UI and agent workflows remain in the source
as optional legacy functionality. To use them explicitly:

```bash
uv sync --frozen --extra full --extra legacy-dev
uv run --frozen ocr-mcp-full  # old stdio/HTTP application, original environment settings
```

The old Web UI entry point remains `ocr-mcp-webapp` and requires the `full` extra
plus the separate frontend install. Existing documentation under `docs/` largely
refers to that full application; its plain `uv sync`/`ocr-mcp` instructions are
superseded by this README. `poetry.lock` is historical; `uv.lock` is authoritative.
