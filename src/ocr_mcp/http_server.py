"""Minimal HTTP-only MCP entry point. No UI, scanner, model loader or stdio mode."""

import argparse
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastmcp import FastMCP

from .backends.ovisocr3_backend import OvisOCR3Backend
from .core.http_config import RemoteOCRConfig
from .document_pipeline import process_remote_document
from .document_tasks import analyze_page
from .document_tasks import reconstruct_table as reconstruct_pair
from .recursive_tables import reconstruct_document_tables as reconstruct_full
from .recursive_tables import renderer_available


def create_server(config: RemoteOCRConfig | None = None) -> FastMCP:
    config = config or RemoteOCRConfig()
    remote_backend = OvisOCR3Backend(config)
    mcp = FastMCP(
        "OCR-MCP HTTP",
        instructions="OCR images and PDFs using an external OpenAI-compatible OCR service. Paths refer to files on the MCP server. No local models are loaded.",
    )

    async def process_document(
        source_path: str,
        operation: Literal["process_document"] = "process_document",
        backend: Literal["ovisocr3", "openai-ocr", "auto"] = "ovisocr3",
        ocr_mode: Literal["text", "markdown", "format", "auto"] = "markdown",
        pages: list[int] | None = None,
        dpi: int = 200,
        force_ocr: bool = False,
        pdf_strategy: Literal["auto", "text", "ocr"] = "auto",
        region: list[int] | None = None,
    ) -> dict:
        """OCR a server-local image or PDF. PDF pages are 1-based, sorted and processed sequentially.

        PDF auto mode uses embedded text only on text-only pages. pdf_strategy=text
        explicitly extracts text without OCR; pages lacking text fail visibly.
        pdf_strategy=ocr renders all pages. force_ocr overrides any strategy to ocr. Partial failures preserve successful pages and page errors.
        backend names are aliases for the configured remote model; there is no fallback.
        """
        return await process_remote_document(
            source_path,
            backend=remote_backend,
            config=config,
            mode=ocr_mode,
            pages=pages,
            dpi=dpi,
            force_ocr=force_ocr,
            pdf_strategy=pdf_strategy,
            region=region,
        )

    async def get_status(check_upstream: bool = False) -> dict:
        """Report configuration; check_upstream explicitly queries /models (no inference)."""
        result = {
            "success": True,
            "transport": "http",
            "document_root": str(config.document_root),
            "backend": "ovisocr3",
            "model": config.ovisocr3_model,
            "upstream_checked": check_upstream,
            "capabilities": remote_backend.get_capabilities(),
            "tool_profile": config.tool_profile,
            "exposed_tools": sorted(selected),
            "recursive_renderer_installed": renderer_available(),
        }
        if check_upstream:
            result["upstream"] = await remote_backend.health_check()
            result["success"] = result["upstream"]["success"]
        return result

    async def analyze_document(
        source_path: str,
        task: Literal[
            "document_markdown",
            "document_structured",
            "document_layout",
            "figure_captioning",
            "plain_ocr",
            "word_spotting",
            "reference_grounding",
            "region_ocr",
            "formula_recognition",
            "chart_parsing",
            "text_translation",
            "image_classification",
            "visual_question_answering",
            "position_aware_vqa",
        ] = "document_structured",
        page: int = 1,
        dpi: int = 200,
        question: str = "",
        references: list[str] | None = None,
        target_region: list[int] | None = None,
        target_language: str = "",
        candidate_labels: list[str] | None = None,
        target_description: str = "the main subject",
    ) -> dict:
        """Analyze one image/PDF page using a pinned OvisOCR3 task prompt, always using the image.

        Supports structured/layout JSON, figure captions (including Mermaid), formulas,
        chart data, spotting, grounding, region OCR, translation, classification and VQA.
        target_region is normalized [left,top,right,bottom] in 0..999, unlike the
        pixel [x,y,width,height] region in process_document. Boxes refer to the supplied
        rendered page. Raw output is retained; invalid task JSON returns success=false.
        """
        return await analyze_page(
            source_path,
            page,
            dpi,
            task,
            remote_backend,
            config,
            question=question,
            references=references,
            target_region=target_region,
            target_language=target_language,
            candidate_labels=candidate_labels,
            target_description=target_description,
        )

    async def extract_information(
        source_path: str,
        page: int = 1,
        dpi: int = 200,
        fields: list[str] | None = None,
        json_template: dict | None = None,
    ) -> dict:
        """Extract visible key-value records from one page (no guessing or multi-page merge).

        Omit both options for open extraction, or supply fields OR json_template.
        json_template is an example output object with empty string placeholders, not
        a JSON Schema. The service checks keys/types against the example; missing values
        may be empty strings. Recognition accuracy still requires caller validation.
        """
        if fields is not None and json_template is not None:
            return {"success": False, "error_code": "INVALID_TASK_INPUT", "error": "Use fields OR json_template"}
        task = "kie_fields" if fields is not None else "kie_schema" if json_template is not None else "kie_open"
        return await analyze_page(
            source_path, page, dpi, task, remote_backend, config, keys=fields, template=json_template
        )

    async def reconstruct_table(source_path: str, pages: list[int], dpi: int = 200) -> dict:
        """Reconstruct tables continuing across exactly two adjacent PDF pages, e.g. [2,3].

        First parses each full page, then supplies both images and parsed Markdown to
        OvisOCR3 using its official cross-page prompt. Returns HTML, actual source page
        provenance, raw stages, or an explicit no-continuation result. Never edits or
        replaces the original page text. This is a pairwise result, not reconstruction
        of a complete table spanning 3+ pages. No recursive rendering/browser is loaded.
        """
        return await reconstruct_pair(source_path, pages, dpi, remote_backend, config)

    async def reconstruct_document_tables(
        source_path: str,
        pages: list[int] | None = None,
        dpi: int = 200,
        stage1_prompt: Literal["document_markdown", "document_structured"] = "document_structured",
    ) -> dict:
        """Reconstruct tables across a consecutive PDF page range, including 3+ pages.

        Requires the optional crosspage extra and Chromium. Uses the official backwards
        adjacent-pair algorithm, re-rendering updated pages with crop alias restoration.
        Conservatively binds the earlier last table to the later first table; ambiguous
        multiple-table merges fail with partial artifacts. Returns final Markdown, table
        provenance, raw stages and server-local artifact paths. Truncated/recovered Stage 1
        output is explicitly PARTIAL; Stage 2 must finish completely. Artifacts persist
        under OCR_ARTIFACT_DIR and must be managed by the deployment.
        """
        return await reconstruct_full(source_path, pages, dpi, stage1_prompt, remote_backend, config)

    tools = {
        tool.__name__: tool
        for tool in (
            process_document,
            get_status,
            analyze_document,
            extract_information,
            reconstruct_table,
            reconstruct_document_tables,
        )
    }
    selected = {"process_document", "get_status"}
    if config.tool_profile in {"document", "crosspage"}:
        selected |= {"analyze_document", "extract_information", "reconstruct_table"}
    if config.tool_profile == "crosspage":
        selected.add("reconstruct_document_tables")
    if config.exposed_tools.strip():
        selected = {name.strip() for name in config.exposed_tools.split(",") if name.strip()}
        if not selected or selected - tools.keys():
            raise ValueError("OCR_MCP_TOOLS contains unknown tools or an empty selection")
    for name in sorted(selected):
        mcp.tool()(tools[name])

    return mcp


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(description="HTTP-only OCR MCP (external OvisOCR3/OpenAI-compatible service)")
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "10859")))
    args = parser.parse_args()
    # OCR tools need no session state; old client IDs must not break calls after a restart.
    create_server().run(transport="http", host=args.host, port=args.port, path="/mcp", stateless_http=True)


if __name__ == "__main__":
    main()
