"""Optional remote document tasks, including the model card's two-image table protocol."""

import math
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pymupdf

from ._vendor.ovisocr3.document_crosspage import IMAGE_REF, TABLE, apply_pair, page_markdown, parse_tables, remap
from .backends.ovisocr3_backend import OpenAICompatibleOCRBackend
from .core.http_config import RemoteOCRConfig
from .document_pipeline import error
from .output_adapters import clean_markdown, validate_task_contract
from .task_prompts import TASK_FORMATS, parse_task_result, prompt_text, task_prompt


@contextmanager
def page_images(source: str, pages: list[int], dpi: int, config: RemoteOCRConfig):
    path = Path(source).expanduser().resolve()
    if config.document_root is not None and not path.is_relative_to(config.document_root.resolve()):
        raise ValueError("Document must be inside OCR_DOCUMENT_ROOT")
    if not path.is_file() or path.stat().st_size > config.max_file_bytes:
        raise ValueError("Missing document or file size limit exceeded")
    if type(dpi) is not int or not 150 <= dpi <= 300:
        raise ValueError("DPI must be an integer from 150 to 300")
    if not pages or any(type(page) is not int or page < 1 for page in pages):
        raise ValueError("Pages must be positive 1-based integers")
    if len(pages) > config.max_pdf_pages:
        raise ValueError("Selected page count exceeds OCR_MAX_PDF_PAGES")
    if path.suffix.lower() != ".pdf":
        if pages != [1]:
            raise ValueError("Images have only page 1; cross-page reconstruction requires a PDF")
        yield [str(path)]
        return
    with pymupdf.open(path) as document, tempfile.TemporaryDirectory(prefix="ovis-task-") as directory:
        if not document.is_pdf or document.needs_pass or max(pages) > len(document):
            raise ValueError("Invalid, encrypted PDF or out-of-range page")
        paths = []
        for number in pages:
            page = document[number - 1]
            scale = dpi / 72
            if math.ceil(page.rect.width * scale) * math.ceil(page.rect.height * scale) > config.max_image_pixels:
                raise ValueError("Rendered page exceeds OCR_MAX_IMAGE_PIXELS")
            target = Path(directory) / f"page_{number}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False).save(target)
            paths.append(str(target))
        yield paths


async def analyze_page(
    source_path: str,
    page: int,
    dpi: int,
    task: str,
    backend: OpenAICompatibleOCRBackend,
    config: RemoteOCRConfig,
    **options: Any,
) -> dict:
    try:
        prompt = task_prompt(task, **options)
        with page_images(source_path, [page], dpi, config) as images:
            result = await backend.run_prompt(images, prompt, TASK_FORMATS[task])
        result = parse_task_result(result, task)
        result = validate_task_contract(result, task, options)
        if result.get("projected_markdown"):
            result["projected_markdown"] = page_markdown(result["projected_markdown"], page)
        result.update(page=page, coordinate_system="normalized_xyxy_0_999", coordinate_origin="supplied_page_image")
        return result
    except (OSError, ValueError, RuntimeError):
        return error(
            "INVALID_TASK_INPUT", "Check task arguments, permitted document path, page number and rendering limits"
        )


async def reconstruct_table(
    source_path: str, pages: list[int], dpi: int, backend: OpenAICompatibleOCRBackend, config: RemoteOCRConfig
) -> dict:
    """Only an adjacent two-page boundary; not recursive document-level reconstruction."""
    if len(pages) != 2 or any(type(n) is not int or n < 1 for n in pages) or pages[1] != pages[0] + 1:
        return error("INVALID_PAGES", "Provide exactly two adjacent PDF pages in reading order, e.g. [2,3]")
    try:
        with page_images(source_path, pages, dpi, config) as images:
            stages = []
            markdowns = []
            for index, image in enumerate(images, 1):
                result = await backend.run_prompt([image], prompt_text("document_markdown"), "markdown")
                stages.append({**result, "page": pages[index - 1]})
                if not result.get("success"):
                    return {"success": False, "error_code": "PAGE_PARSING_FAILED", "pages": stages, "tables": []}
                text = page_markdown(clean_markdown(result["text"]), index)
                stages[-1]["markdown"] = text

                markdowns.append(text)
            common = {
                "pages": stages,
                "input_pages": pages,
                "scope": "adjacent_pair",
                "recursive_multi_page": False,
                "image_reference_pages": {"page_1": pages[0], "page_2": pages[1]},
                "confidence": None,
            }
            if not all(TABLE.search(text) for text in markdowns):
                return {
                    **common,
                    "success": True,
                    "continuation": False,
                    "tables": [],
                    "reason": "Both parsed pages must contain HTML tables; no stage-2 request was made",
                }
            context = "\n\n".join(
                f'<INPUT_PAGE index="{i}">\n{text}\n</INPUT_PAGE>' for i, text in enumerate(markdowns, 1)
            )
            prompt = prompt_text("crosspage_table").removesuffix("\n") + "\n\nInput Markdown:\n\n" + context
            result = parse_task_result(await backend.run_prompt(images, prompt, "json"), "crosspage_table")
            if not result.get("success"):
                return {**common, **result, "tables": []}
            try:
                records = parse_tables(result["text"])
                tables = []
                for record in records:
                    # Validate complete, non-nested single-table fragments without writing back.
                    apply_pair(tuple(markdowns), [record], bindings=[(0, 0)])
                    html = remap(record["html"], {1: pages[0], 2: pages[1]})
                    if any(match[1] is None for match in IMAGE_REF.finditer(html)):
                        raise ValueError("Output crop lacks a source page")
                    tables.append({"source_pages": pages, "input_source_pages": [1, 2], "html": html})
            except (ValueError, TypeError, KeyError):
                return {
                    **common,
                    **result,
                    "success": False,
                    "error_code": "INVALID_TABLE_RESULT",
                    "error": "Invalid table structure or image reference; raw response retained",
                    "tables": [],
                }
            return {
                **common,
                **result,
                "continuation": bool(tables),
                "tables": tables,
                "image_references": "actual_document_pages",
            }

    except (OSError, ValueError, RuntimeError):
        return error("INVALID_TABLE_INPUT", "Check readable PDF, adjacent pages, prompt size and rendering limits")
