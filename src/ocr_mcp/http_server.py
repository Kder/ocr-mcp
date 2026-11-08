"""Minimal HTTP-only MCP entry point. No UI, scanner, model loader or stdio mode."""

import argparse
import os
from typing import Literal

from dotenv import load_dotenv
from fastmcp import FastMCP

from .backends.ovisocr3_backend import OvisOCR3Backend
from .core.http_config import RemoteOCRConfig
from .document_pipeline import process_remote_document


def create_server(config: RemoteOCRConfig | None = None) -> FastMCP:
    config = config or RemoteOCRConfig()
    remote_backend = OvisOCR3Backend(config)
    mcp = FastMCP(
        "OCR-MCP HTTP",
        instructions="OCR images and PDFs using an external OpenAI-compatible OCR service. Paths refer to files on the MCP server. No local models are loaded.",
    )

    @mcp.tool()
    async def process_document(
        source_path: str,
        operation: Literal["process_document"] = "process_document",
        backend: Literal["ovisocr3", "openai-ocr", "auto"] = "ovisocr3",
        ocr_mode: Literal["text", "markdown", "format", "auto"] = "markdown",
        pages: list[int] | None = None,
        dpi: int = 200,
        force_ocr: bool = False,
        region: list[int] | None = None,
    ) -> dict:
        """OCR a server-local image or PDF. PDF pages are 1-based, sorted and processed sequentially.

        Text-only PDF pages use embedded text; raster/mixed pages use OCR. Set force_ocr
        to OCR every page. Partial failures preserve successful pages and page errors.
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
            region=region,
        )

    @mcp.tool()
    async def get_status(check_upstream: bool = False) -> dict:
        """Report configuration; check_upstream explicitly queries /models (no inference)."""
        result = {
            "success": True,
            "transport": "http",
            "backend": "ovisocr3",
            "model": config.ovisocr3_model,
            "upstream_checked": check_upstream,
            "capabilities": remote_backend.get_capabilities(),
        }
        if check_upstream:
            result["upstream"] = await remote_backend.health_check()
            result["success"] = result["upstream"]["success"]
        return result

    return mcp


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="HTTP-only OCR MCP (external OvisOCR3/OpenAI-compatible service)")
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "10859")))
    args = parser.parse_args()
    create_server().run(transport="http", host=args.host, port=args.port, path="/mcp")


if __name__ == "__main__":
    main()
