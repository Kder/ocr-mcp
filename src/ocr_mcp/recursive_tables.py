"""HTTP adapter for the pinned official recursive cross-page pipeline.

All inference stays remote. The optional renderer runs on a worker thread and
uses sanitized offline HTML, local MathJax and explicit image aliases.
"""

import asyncio
import importlib.util
import json
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import pymupdf

from ._vendor.ovisocr3 import document_crosspage as reference
from .artifacts import artifact_job
from .backends.ovisocr3_backend import OpenAICompatibleOCRBackend
from .core.http_config import RemoteOCRConfig
from .document_pipeline import error
from .document_tasks import page_images
from .task_prompts import prompt_text

# Keep long-running browser workers separate from the event loop's short image
# encoding jobs. Otherwise a saturated default pool can deadlock HTTP callbacks.
_DOCUMENT_WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr-document")


def renderer_available() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("bs4", "markdown_it", "playwright"))


class _RemoteFailure(RuntimeError):
    pass


class BrowserRenderer:
    """Own Chromium on its worker thread; no model HTML can access the network."""

    def __init__(self, config: RemoteOCRConfig):
        self.config = config
        self.playwright = self.browser = self.renderer = None

    def render(self, *args):
        if self.renderer is None:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            executable = self.config.chromium_executable or shutil.which("chromium")
            self.browser = self.playwright.chromium.launch(headless=True, executable_path=executable)
            self.renderer = reference.Renderer(
                self.browser,
                Path(reference.__file__).parent / "assets" / "mathjax.js",
                limits=reference.RenderLimits(
                    max_render_pixels=self.config.max_image_pixels, max_source_chars=self.config.max_output_chars
                ),
                # An engineering estimate matching the card's pixel budget, not an
                # assertion about every compatible upstream processor's resize policy.
                max_input_pixels=2880 * 2880,
            )
        return self.renderer.render(*args)

    def close(self):
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self.playwright:
                self.playwright.stop()


def _export_document_pages(output: Path, result: dict, pages: list[int]) -> dict:
    """Keep raw stages local-indexed; final document/crops use actual PDF page IDs."""
    mapping = dict(enumerate(pages, 1))
    document = reference.remap((output / "document.md").read_text(encoding="utf-8"), mapping)
    (output / "document.md").write_text(document, encoding="utf-8")
    temporary = output / "crop-renames"
    temporary.mkdir()
    for local in mapping:
        source = output / f"page_{local}"
        if source.exists():
            source.rename(temporary / f"page_{local}")
    for local, actual in mapping.items():
        source = temporary / f"page_{local}"
        if source.exists():
            source.rename(output / f"page_{actual}")
    temporary.rmdir()
    page_files = list((output / "pages").glob("*.md")) if (output / "pages").exists() else []
    contents = {
        int(file.stem.removeprefix("page_")): reference.remap(file.read_text(encoding="utf-8"), mapping)
        for file in page_files
    }
    for file in page_files:
        file.unlink()
    for local, text in contents.items():
        (output / "pages" / f"page_{mapping[local]:04d}.md").write_text(text, encoding="utf-8")
    groups = [
        {
            **group,
            "source_pages": [mapping[n] for n in group["source_pages"]],
            "fragment_ids": [[mapping[n], index] for n, index in group["fragment_ids"]],
            "html": reference.remap(group["html"], mapping),
        }
        for group in result["merged_tables"]
    ]
    return {
        "document_markdown": document,
        "merged_tables": groups,
        "page_files": [str(output / "pages" / f"page_{mapping[n]:04d}.md") for n in sorted(contents)],
    }


async def reconstruct_document_tables(
    source_path: str,
    pages: list[int] | None,
    dpi: int,
    stage1_prompt: Literal["document_markdown", "document_structured"],
    backend: OpenAICompatibleOCRBackend,
    config: RemoteOCRConfig,
    *,
    _renderer_factory=BrowserRenderer,
) -> dict:
    if stage1_prompt not in {"document_markdown", "document_structured"}:
        return error("INVALID_TASK_INPUT", "Stage 1 must use document_markdown or document_structured")
    if _renderer_factory is BrowserRenderer and not renderer_available():
        return error("DEPENDENCY_MISSING", "Install the crosspage extra and Chromium for document-level reconstruction")
    try:
        path = Path(source_path).expanduser().resolve()
        if config.document_root is not None and not path.is_relative_to(config.document_root.resolve()):
            raise ValueError("Outside document root")
        if path.suffix.lower() != ".pdf" or not path.is_file() or path.stat().st_size > config.max_file_bytes:
            raise ValueError("Readable bounded PDF required")
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError("Encrypted PDF")
            if pages is None:
                if len(pdf) > min(config.max_pdf_pages, config.crosspage_max_pages):
                    raise ValueError("Select fewer pages")
                pages = list(range(1, len(pdf) + 1))
            if (
                not pages
                or len(pages) > min(config.max_pdf_pages, config.crosspage_max_pages)
                or any(type(n) is not int or not 1 <= n <= len(pdf) for n in pages)
                or pages != list(range(pages[0], pages[-1] + 1))
            ):
                raise ValueError("Consecutive PDF pages in reading order required")
    except (OSError, ValueError, RuntimeError):
        return error("INVALID_DOCUMENT_INPUT", "Check permitted PDF, consecutive page range, and page/file limits")

    loop = asyncio.get_running_loop()
    stop = threading.Event()
    pending = []

    def work():
        try:
            with artifact_job(config.crosspage_artifact_dir, uuid.uuid4().hex) as job:
                return run_job(job)
        except (OSError, ValueError, RuntimeError):
            return error(
                "ARTIFACT_STORAGE_UNAVAILABLE", "Artifact root must be private, owned by this user and writable"
            )

    def run_job(job):
        output = job / "result"
        spent = 0
        raw_stages = []

        def infer(paths, prompt, folder, stage):
            nonlocal spent
            if stop.is_set():
                raise _RemoteFailure("CANCELLED")
            future = asyncio.run_coroutine_threadsafe(
                backend.run_prompt(
                    paths,
                    prompt,
                    "markdown" if stage == 1 and stage1_prompt == "document_markdown" else "json",
                    allow_truncated=stage == 1,
                ),
                loop,
            )
            pending.append(future)
            try:
                generation = future.result()
            finally:
                pending.remove(future)
            raw = generation.get("text", generation.get("raw_output", ""))
            if spent + len(raw) > config.max_output_chars:
                raw_stages.append(
                    {"stage": stage, "success": False, "error_code": "DOCUMENT_OUTPUT_LIMIT", "omitted_chars": len(raw)}
                )
                raise _RemoteFailure("DOCUMENT_OUTPUT_LIMIT")
            spent += len(raw)
            raw_stages.append(
                {
                    "stage": stage,
                    "source_pages": [pages[int(n) - 1] for n in re.findall(r"\d+", folder.name)],
                    "raw_output": raw,
                    "finish_reason": generation.get("finish_reason"),
                    "success": generation.get("success", False),
                }
            )
            if not generation.get("success"):
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "raw.txt").write_text(raw, encoding="utf-8")
                raise _RemoteFailure(generation.get("error_code", "UPSTREAM_FAILED"))
            return raw, generation["finish_reason"]

        renderer = _renderer_factory(config)
        try:
            with page_images(str(path), pages, dpi, config) as rendered:
                inputs = job / "inputs"
                inputs.mkdir(parents=True)
                images = []
                for number, image in zip(pages, rendered, strict=True):
                    target = inputs / f"page_{number:04d}.png"
                    if Path(image).stat().st_size > config.max_file_bytes:
                        raise ValueError("Rendered input exceeds file limit")
                    shutil.copyfile(image, target)
                    images.append(str(target))
                result = reference._run_document(
                    images,
                    output,
                    infer,
                    renderer,
                    page_prompt=prompt_text(stage1_prompt).removesuffix("\n"),
                    table_prompt=prompt_text("crosspage_table").removesuffix("\n"),
                    stage1_prompt=stage1_prompt,
                )
            exported = _export_document_pages(output, result, pages)
            warnings = [{**warning, "page": pages[warning["page"] - 1]} for warning in result["stage1_parse_warnings"]]
            steps = [
                {
                    **step,
                    "pages": [pages[n - 1] for n in step["pages"]],
                    "rendered_pages": [pages[n - 1] for n in step["rendered_pages"]],
                }
                for step in result["steps"]
            ]
            render_warnings = []
            for file in output.glob("stage2/**/*.render.json"):
                diagnostics = json.loads(file.read_text(encoding="utf-8"))
                if diagnostics.get("status") != "COMPLETE":
                    render_warnings.append(
                        {
                            "artifact": str(file.relative_to(job)),
                            "status": diagnostics.get("status"),
                            "formula_fallbacks": diagnostics.get("formula_fallbacks", 0),
                            "error": diagnostics.get("error"),
                        }
                    )
            failure = result["error"]
            if failure is not None and failure.get("pages"):
                failure = {**failure, "pages": [pages[n - 1] for n in failure["pages"]]}
            if failure is None and render_warnings:
                failure = {"type": "RenderDegraded", "recoverable": True}
            complete = result["status"] == "COMPLETE" and not render_warnings
            response = {
                "success": complete,
                "partial_success": not complete and result["parsed_pages"] > 0,
                "status": "COMPLETE" if complete else "PARTIAL",
                "scope": "document",
                "recursive_multi_page": True,
                "input_pages": pages,
                "parsed_pages": result["parsed_pages"],
                "steps": steps,
                "stage1_parse_warnings": warnings,
                "error": failure,
                "render_warnings": render_warnings,
                "image_reference_pages": {f"page_{n}": page for n, page in enumerate(pages, 1)},
                "image_export_errors": result["image_export_errors"],
                "raw_stages": raw_stages,
                "artifact_dir": str(job),
                "confidence": None,
                **exported,
            }
            # Preserve the unmodified reference result alongside our document-page projection.
            (output / "reference-result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (job / "result.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
            return response
        except (OSError, ValueError, RuntimeError):
            return {
                **error("DOCUMENT_RECONSTRUCTION_FAILED", "Document reconstruction failed; inspect retained artifacts"),
                "artifact_dir": str(job),
                "raw_stages": raw_stages,
            }
        finally:
            renderer.close()

    worker = loop.run_in_executor(_DOCUMENT_WORKERS, work)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        for future in list(pending):
            future.cancel()
        # The worker owns all temporary files and closes its browser in finally.
        await asyncio.shield(worker)
        raise
