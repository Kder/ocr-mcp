# OCR-MCP — OvisOCR3 HTTP edition

Lightweight **Streamable HTTP MCP** service for images and PDFs. Calls an external
OpenAI-compatible vision endpoint, defaulting to **Halogen / OvisOCR3**. No local
weights, GPU runtime, scanner, web UI, Ollama or tool calling on the OCR model.
Python **3.12+**. MIT license.

Maintained fork: [Kder/ocr-mcp](https://github.com/Kder/ocr-mcp), based on
[sandraschi/ocr-mcp](https://github.com/sandraschi/ocr-mcp). Original author and
copyright notices are retained. This fork defaults to external HTTP OCR and keeps
local models, scanners and the Web UI behind the optional legacy full installation.

```text
DSH → HTTP MCP /mcp → OCR-MCP → Halogen /v1/chat/completions → OvisOCR3
                         └── PyMuPDF: PDF text extraction / page rendering
```

## Install and run

From the checkout:

```bash
uv sync --frozen
mkdir -p documents
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

By default, only **`process_document`** and **`get_status`** are registered.
Optional model-card capabilities are described below.
Connect DSH using its Streamable HTTP MCP configuration and the endpoint above.
Example `process_document` arguments:

```json
{
  "operation": "process_document",
  "source_path": "documents/scan.pdf",
  "backend": "ovisocr3",
  "ocr_mode": "markdown",
  "pages": [1, 3, 5],
  "dpi": 200,
  "force_ocr": false
}
```

- `source_path` is a **server-local file path**. DSH and OCR-MCP must share a
  volume/path, or the document must already exist on the MCP server. There is no
  arbitrary URL fetch or upload endpoint. File access is confined to
  `OCR_DOCUMENT_ROOT`: default `./documents` resolved at startup, or `/documents`
  in the container image. Blank/unset values use the safe default; they do not
  allow unrestricted reads. Traversal and symlink escapes are rejected. `get_status`
  reports the resolved root so clients can use the correct server-local path.
- Images: PNG, JPEG, WebP, single-frame TIFF/BMP; decoded and sent as PNG. Optional
  `region: [x, y, width, height]` crops an image. Animated/multi-frame images are rejected.
- PDFs: 1-based page selection, deduplicated and sorted in original page order.
  `pdf_strategy=auto` (default) retains the conservative rule: text-only pages use
  embedded text; pages with raster images use OCR. It does not infer that logos or
  vector-drawn text are harmless. `pdf_strategy=text` explicitly uses embedded text
  without OCR (including pages with logos); pages without text return `NO_TEXT_LAYER`,
  preserving successful pages. `pdf_strategy=ocr` renders every selected page.
  `force_ocr=true` overrides any strategy to `ocr` for compatibility. Text mode can
  omit visual content and never reconstructs table layout; choose it only when the
  text layer is known to be complete. Single-page analytical/table tools always use
  images, independent of this basic PDF strategy.
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

## Optional OvisOCR3 capabilities and tool exposure

```bash
OCR_MCP_PROFILE=document uv run --frozen ocr-mcp
# Or expose only selected tools:
OCR_MCP_TOOLS=process_document,get_status,reconstruct_table uv run --frozen ocr-mcp
```

The `basic` profile (default) keeps two tools; `document` exposes five.
The opt-in `crosspage` profile exposes six, adding `reconstruct_document_tables`. An explicit
`OCR_MCP_TOOLS` comma-separated allowlist overrides the profile; unknown names fail
startup. All profiles use the same lightweight dependencies and HTTP transport.

| Optional tool | Purpose | Key arguments |
|---|---|---|
| `analyze_document` | Analyze one image or PDF page with an official task prompt | `task`, `page`, plus task-specific inputs |
| `extract_information` | Open, specified-field, or output-template KIE | `fields` OR `json_template`, `page` |
| `reconstruct_table` | Native reconstruction of tables across two adjacent PDF pages | `source_path`, `pages: [2,3]`, `dpi` |
| `reconstruct_document_tables` (crosspage only) | Recursive document-level reconstruction with re-rendering | `source_path`, consecutive `pages`, `stage1_prompt`, `dpi` |

`analyze_document.task` supports `document_markdown`, `document_structured`,
`document_layout`, `figure_captioning`, `plain_ocr`, `word_spotting`,
`reference_grounding`, `region_ocr`, `formula_recognition`, `chart_parsing`,
`text_translation`, `image_classification`, `visual_question_answering`, and
`position_aware_vqa`. Conditional arguments include `question`, `references`,
`target_region`, `target_language`, `candidate_labels` and `target_description`.
These tasks always use the page image, including digital PDFs; text extraction
cannot substitute for layout, visual analysis or table structure.

Task boxes and `target_region` use normalized **[left, top, right, bottom]** in
0..999, relative to the supplied image. This differs from the pixel
**[x, y, width, height]** crop in `process_document`. Structured outputs preserve
`raw_output` and include parsed `data`; invalid JSON or basic task structure is an
error. Category/box constraints, chart string values, position-aware answer boxes,
requested labels/keys and example-template structure are checked; recognition
accuracy is not established by these checks. Structured parsing preserves complete
prefix objects and their content with diagnostics on malformed/truncated JSON;
that partial result never reports complete success. `json_template` is an output example with
empty-string placeholders, **not** formal JSON Schema. Visual captions and VQA
answers may contain unsupported details; verify against the original page.

The table tool follows the model card's two-stage protocol: parse both full pages
with the official Markdown prompt, then send their images plus parsed Markdown
in a single two-image request. It returns complete table HTML for that boundary,
actual source page numbers, local-to-actual page reference mapping, raw stage
outputs, or an explicit negative prediction. It preserves page results rather
than replacing the original text. Truncated parsing/reconstruction is an error.

**`reconstruct_table` scope: exactly one adjacent page pair.** Independent pairwise
results are not an assembled table spanning three or more pages. For that use the
optional document-level tool below. Table-bearing content missing from
stage 1 can cause continuation to be missed. HTML/Mermaid are returned as untrusted
text, not executed or rendered. Image references describe crops; files are not
created automatically.

Official prompts and their Apache-2.0 license are bundled at a fixed revision;
no runtime model-card downloads are needed. `temperature=0` and `max_tokens=16384`
match the model card's usage defaults; `OVISOCR3_MAX_TOKENS` can lower the limit.
See [the research and design notes](docs/OVISOCR3_CAPABILITIES.md) for capability
coverage, evidence and remaining work.

### Optional recursive document tables

```bash
uv sync --frozen --extra dev --extra crosspage
# Not needed if a usable system Chromium is already installed:
uv run --frozen --extra crosspage playwright install chromium
OCR_MCP_PROFILE=crosspage uv run --frozen --extra crosspage ocr-mcp
```

This extra adds BeautifulSoup, markdown-it-py and Playwright; base installs still
have no browser or ML dependencies. The service uses a system `chromium` if found,
or Playwright's installed Chromium. `OCR_CHROMIUM_EXECUTABLE` can select an existing
browser. Local MathJax is bundled at the same pinned revision, so model-supplied
HTML has no external asset/network access. No vLLM/Torch backend is instantiated.

`reconstruct_document_tables` accepts a server-local PDF and an optional consecutive
page range, e.g. `pages: [2,3,4]`; omitting `pages` selects the whole bounded PDF.
`stage1_prompt` is `document_structured` (default) or `document_markdown`. It follows
the official algorithm: find table-bearing runs, merge from last to first, put the
result in the earlier page, then re-render that updated Markdown before the next
prediction. Crop aliases are restored to original input images. Final Markdown,
HTML provenance and crop paths use actual PDF page IDs, including selected ranges.

The reference algorithm conservatively binds the earlier last table to the later
first table. It rejects multiple ambiguous merges, nested/incomplete table writeback
and unmappable image references. It preserves non-table text. Stage-1 truncation,
JSON prefix recovery or invalid structured fields produce explicit `PARTIAL` status;
stage-2 truncation stops further merges while keeping prior results. Degraded formula
rendering is surfaced as partial, rather than silently reporting complete success.

Results include `document_markdown`, `merged_tables`, `steps`, `raw_stages`,
`stage1_parse_warnings`, `render_warnings` and a server-local `artifact_dir`. Input
images, raw generations, renderer diagnostics and final Markdown/crops remain under
`OCR_ARTIFACT_DIR` (default: the OS temporary directory's `ocr-mcp-tables/`). The
returned top-level `result.json` uses actual PDF page IDs; intermediate reference
stage folders retain input-local indices and an explicit page mapping. New jobs use
owner-only directories and cross-process locks; no artifact HTTP server is exposed.
Use the independent cleanup command below for deployment-managed retention. The default document limit is 20 pages (`OCR_CROSSPAGE_MAX_PAGES`)
and total raw generated characters are bounded by `OCR_MAX_OUTPUT_CHARS`. Readability,
image/prompt/output limits can stop a long table with retained partial artifacts.

Optional browser tests (mock OCR, real rendering):

```bash
uv run --frozen --extra dev --extra crosspage pytest -c pyproject.toml \
  --confcutdir=tests/crosspage tests/crosspage -o addopts='' -q
```

### Artifact retention (independent command)

`OCR_ARTIFACT_RETENTION_DAYS=7` expires jobs by age since completion (or last marker
write before a process crash). `OCR_ARTIFACT_MAX_GB=5` then prunes oldest inactive
jobs to a managed-data size target. Set either to `0` to disable that policy. These
are cleanup policies, not a real-time write quota or an automatically started timer.

```bash
# Preview only; does not delete documents:
uv run --frozen ocr-mcp-clean-artifacts
# Apply configured retention/quota:
uv run --frozen ocr-mcp-clean-artifacts --apply
```

Run the installed executable directly from cron/systemd to avoid changing the
server environment's installed extras. For example, after setting its environment
(or working directory with the appropriate `.env`), run daily:

```cron
0 3 * * * cd /path/to/ocr-mcp && /path/to/ocr-mcp/.venv/bin/ocr-mcp-clean-artifacts --apply
```

The command scans only the configured artifact root and deletes only marked UUID
job directories created by this version, including failed jobs. It does not follow
symlinks, remove unrelated directories, or delete running jobs. Active jobs can keep
the quota above its target; the JSON report includes skipped jobs and quota status.
Legacy unmarked artifacts are left for manual review. The root must be owned by
the service user and not group/world writable on POSIX; new job directories are
`0700`. No cleanup scan occurs during an OCR request. Dry-run remaining-byte counts
are projections of what applying the reported plan would leave.

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
| `OCR_DOCUMENT_ROOT` | `./documents` (Docker: `/documents`) | Required document confinement; blank uses default |
| `OCR_ARTIFACT_RETENTION_DAYS` | `7` | Cleanup age target; `0` disables expiry |
| `OCR_ARTIFACT_MAX_GB` | `5` | Cleanup managed-size target; `0` disables quota |

Each ordinary OCR request contains one image (cross-page requests contain two) and **no `tools`**. Requests to the upstream
are serialized across documents within one backend instance; PDF pages are processed sequentially.
The semaphore is not shared across processes or containers. Multi-instance queueing
must be enforced at the Halogen service/deployment layer.
Failures are returned without upstream response bodies or document data. Requests
are not retried automatically, preventing duplicate long-running inference.
HTTP failures, timeouts, missing models, invalid responses and truncated completions
are explicit errors. When a response includes a `model`, it must match the configured
ID. The remote server remains responsible for honoring that ID and device placement.

Defaults limit input files to 50 MiB, decoded/rendered images to 25 million pixels,
selected PDF pages to 100 and OCR/extracted content to 1 million characters per document.
See `.env.example` for overrides. DPI range is 150–300. Password-protected PDFs are
unsupported. Long PDFs can exceed client timeouts/context limits: request fewer pages
per call. `OVISOCR3_TIMEOUT` is an upstream per-page request timeout, not a whole-PDF
or DSH deadline; measure actual dense-page performance and set both upstream/client
timeouts accordingly. Requests use `temperature=0` and the configured token cap;
mock contract tests do not establish that a particular Halogen/NPU version accepts
these settings or correctly reports truncation. Live model-list and known-image/PDF
tests are still required. OCR caching and automatic retries remain outside this edition.

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
Keep the container listener at `0.0.0.0` for port forwarding; the `-p 127.0.0.1:...`
argument above restricts the host publication address. Keep documents mounted `:ro`.
For remote DSH, use an authenticated TLS reverse proxy or a trusted private network;
changing the document root does not provide authentication or transport encryption.
The container publishes only MCP; it includes no UI or local OCR binaries.

## Development and validation

```bash
uv sync --frozen --extra dev
uv run --frozen pytest -c pyproject.toml --confcutdir=tests/http_only tests/http_only -o addopts='' -q
uv run --frozen ruff check src/ocr_mcp/http_server.py src/ocr_mcp/document_pipeline.py \
  src/ocr_mcp/core/http_config.py src/ocr_mcp/core/backend.py \
  src/ocr_mcp/backends/ovisocr3_backend.py src/ocr_mcp/document_tasks.py \
  src/ocr_mcp/task_prompts.py src/ocr_mcp/output_adapters.py src/ocr_mcp/recursive_tables.py src/ocr_mcp/artifacts.py tests/http_only
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

The lightweight path shares `RemoteOCRConfig`, the remote backend and document
pipeline with the legacy remote-OCR adapter; the legacy registry remains for local
engines. Improvements to shared behavior belong in that core, rather than duplicated
implementations. File confinement described above applies to the lightweight HTTP
file tools; it is not a security review of all legacy scanner/Web UI tools.
