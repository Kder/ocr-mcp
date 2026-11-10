"""Model-card/reference regression tests without browser or model dependencies."""

import base64
import hashlib
import io
import json
from importlib.resources import files
from pathlib import Path

import httpx
import pymupdf
import pytest
from fastmcp import Client
from PIL import Image

from ocr_mcp._vendor.ovisocr3 import document_crosspage as reference
from ocr_mcp.backends.ovisocr3_backend import OvisOCR3Backend
from ocr_mcp.core.http_config import RemoteOCRConfig
from ocr_mcp.document_tasks import analyze_page, reconstruct_table
from ocr_mcp.http_server import create_server
from ocr_mcp.output_adapters import clean_markdown
from ocr_mcp.recursive_tables import reconstruct_document_tables
from ocr_mcp.task_prompts import parse_task_result


def response(text, reason="stop"):
    return httpx.Response(
        200, json={"model": "OvisOCR3", "choices": [{"message": {"content": text}, "finish_reason": reason}]}
    )


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "document.pdf"
    with pymupdf.open() as document:
        for _ in range(4):
            document.new_page(width=120, height=120).insert_text((20, 40), "Original page")
        document.save(path)
    return path


class FakeRenderer:
    calls = []

    def __init__(self, config):
        pass

    def render(self, source, owner, paths, directory, name):
        self.calls.append((source, owner))
        path = Path(directory) / "updated.png"
        Image.new("RGB", (100, 100), "red").save(path)
        return reference.RenderResult(str(path), source, {}, {})

    def close(self):
        pass


def backend(config, handler):
    return OvisOCR3Backend(config, transport=httpx.MockTransport(handler))


def test_frozen_sources_and_all_prompts_match_hashes():
    for component in (files("ocr_mcp").joinpath("prompts"), files("ocr_mcp._vendor.ovisocr3")):
        manifest = json.loads(component.joinpath("SHA256.json").read_text())
        for name, expected in manifest.items():
            assert hashlib.sha256(component.joinpath(name).read_bytes()).hexdigest() == expected, name


def test_model_card_markdown_cleanup_retains_short_repetitions():
    repeated = "x" * 8100
    assert clean_markdown(repeated) == reference._clean_truncated_repeats(repeated)
    assert clean_markdown("repeat\n" * 10) == ("repeat\n" * 10).strip()
    raw = '```markdown\nA\n\n<img src="images/bbox_1_2_3_4.jpg" />\n```'
    assert "images/bbox_" in clean_markdown(raw)
    assert clean_markdown(raw, filter_imgtags=True) == "A"  # filter only requested explicitly


def test_structured_prefix_recovery_never_claims_complete():
    raw = '[{"bbox":[1,2,3,4],"category":"text","content":"kept"},{"content":"unfinished'
    result = parse_task_result({"success": True, "text": raw}, "document_structured")
    assert not result["success"] and result["partial_success"]
    assert result["projected_markdown"] == "kept"
    assert result["raw_output"] == raw and not result["parse_diagnostics"]["json_complete"]


def test_unknown_structured_category_is_reported_with_content_preserved():
    raw = '[{"bbox":[1,2,3,4],"category":"invented","content":"visible"}]'
    result = parse_task_result({"success": True, "text": raw}, "document_structured")
    assert not result["success"] and result["projected_markdown"] == "visible"
    assert result["parse_diagnostics"]["schema_issues"] == [{"index": 0, "field": "category"}]


@pytest.mark.parametrize(
    "task,raw,options",
    [
        ("chart_parsing", '{"title":"None","source":"None","x_title":"x","y_title":["a","b"],"values":{"a":"1"}}', {}),
        ("position_aware_vqa", '{"answer":"42","bbox":"(1,2,30,40)"}', {}),
        ("kie_fields", '{"total":"42"}', {"keys": ["total"]}),
        ("kie_schema", '{"items":[{"amount":"42"}]}', {"template": {"items": [{"amount": ""}]}}),
    ],
)
async def test_task_contracts_accept_official_shapes(pdf, task, raw, options, tmp_path):
    config = RemoteOCRConfig()
    remote = backend(config, lambda request: response(raw))
    # PVA requires its own question but uses a string bbox, unlike spotting/layout.
    if task == "position_aware_vqa":
        options = {"question": "What is the value?"}
    result = await analyze_page(str(pdf), 1, 150, task, remote, config, **options)
    assert result["success"] and result["contract_validated"]


@pytest.mark.parametrize(
    "task,raw,options",
    [
        ("chart_parsing", '{"title":"t","source":"None","x_title":"x","y_title":"y","values":{"x":42}}', {}),
        ("position_aware_vqa", '{"answer":"42","bbox":[1,2,3,4]}', {"question": "Value?"}),
        ("kie_fields", '{"wrong":"42"}', {"keys": ["total"]}),
        ("kie_schema", '{"items":[{"wrong":"42"}]}', {"template": {"items": [{"amount": ""}]}}),
        ("reference_grounding", '[{"bbox":[1,2,3,4],"reference":"wrong"}]', {"references": ["Total"]}),
        ("image_classification", "unknown", {"candidate_labels": ["invoice", "letter"]}),
    ],
)
async def test_invalid_task_contracts_preserve_raw(pdf, task, raw, options):
    config = RemoteOCRConfig()
    result = await analyze_page(str(pdf), 1, 150, task, backend(config, lambda r: response(raw)), config, **options)
    assert not result["success"] and result["error_code"] == "INVALID_TASK_CONTRACT"
    assert result["raw_output"] == raw


async def test_pair_restores_html_image_page_references(pdf):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls <= 2:
            return response("<table><tr><td>fragment</td></tr></table>")
        return response(
            json.dumps(
                [
                    {
                        "source_pages": [1, 2],
                        "html": '<table><tr><td><img src="page_2/images/bbox_1_2_30_40.jpg" /></td></tr></table>',
                    }
                ]
            )
        )

    config = RemoteOCRConfig()
    result = await reconstruct_table(str(pdf), [2, 3], 150, backend(config, handle), config)
    assert result["success"]
    assert "page_3/images/bbox_" in result["tables"][0]["html"]


@pytest.mark.parametrize(
    "html",
    [
        "<table></table><table></table>",
        "<table><table></table></table>",
        '<table><tr><td><img src="images/bbox_1_2_30_40.jpg" /></td></tr></table>',
        '<table><tr><td><img src="page_99/images/bbox_1_2_30_40.jpg" /></td></tr></table>',
    ],
)
async def test_pair_rejects_ambiguous_tables_or_image_provenance(pdf, html):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return (
            response("<table><tr><td>fragment</td></tr></table>")
            if calls <= 2
            else response(json.dumps([{"source_pages": [1, 2], "html": html}]))
        )

    config = RemoteOCRConfig()
    result = await reconstruct_table(str(pdf), [1, 2], 150, backend(config, handle), config)
    assert not result["success"] and result["error_code"] == "INVALID_TABLE_RESULT"
    assert html in result["raw_output"] or '\\"' in result["raw_output"]


async def test_recursive_three_pages_re_render_before_second_merge(pdf, tmp_path):
    FakeRenderer.calls = []
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) <= 3:
            n = len(calls)
            return response(
                json.dumps(
                    [
                        {"bbox": [1, 2, 900, 950], "category": "text", "content": f"# Heading {n}"},
                        {
                            "bbox": [1, 2, 900, 950],
                            "category": "table",
                            "content": f"<table><tr><td>Part {n}</td></tr></table>",
                        },
                        {"bbox": [1, 2, 900, 950], "category": "footer", "content": f"Footer {n}"},
                    ]
                )
            )
        if len(calls) == 5:
            data = payload["messages"][0]["content"][1]["image_url"]["url"].split(",")[1]
            with Image.open(io.BytesIO(base64.b64decode(data))) as image:
                assert image.getpixel((0, 0)) == (255, 0, 0)  # actual new image, not stale original
        return response(
            json.dumps([{"source_pages": [1, 2], "html": f"<table><tr><td>Merged {len(calls)}</td></tr></table>"}])
        )

    config = RemoteOCRConfig(crosspage_artifact_dir=tmp_path / "artifacts")
    result = await reconstruct_document_tables(
        str(pdf), [2, 3, 4], 150, "document_structured", backend(config, handle), config, _renderer_factory=FakeRenderer
    )
    assert result["success"] and result["status"] == "COMPLETE"
    assert [step["pages"] for step in result["steps"]] == [[3, 4], [2, 3]]
    assert result["steps"][1]["rendered_pages"] == [3]
    assert result["merged_tables"][0]["source_pages"] == [2, 3, 4]
    assert result["merged_tables"][0]["fragment_ids"] == [[2, 0], [3, 0], [4, 0]]
    assert len(calls) == 5 and len(FakeRenderer.calls) == 1
    assert all(
        f"Heading {n}" in result["document_markdown"] and f"Footer {n}" in result["document_markdown"]
        for n in (1, 2, 3)
    )
    assert Path(result["artifact_dir"], "result.json").is_file()


async def test_recursive_truncated_structured_stage1_is_partial(pdf, tmp_path):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        if count == 1:
            return response(
                '[{"bbox":[1,2,3,4],"category":"text","content":"preserved"},{"content":"incomplete', "length"
            )
        return response('[{"bbox":[1,2,3,4],"category":"text","content":"second page"}]')

    config = RemoteOCRConfig(crosspage_artifact_dir=tmp_path / "artifacts")
    result = await reconstruct_document_tables(
        str(pdf), [1, 2], 150, "document_structured", backend(config, handle), config, _renderer_factory=FakeRenderer
    )
    assert not result["success"] and result["partial_success"]
    assert "preserved" in result["document_markdown"] and "second page" in result["document_markdown"]
    assert "generation_truncated" in result["stage1_parse_warnings"][0]["codes"]


@pytest.mark.parametrize("kind", ["truncated", "multiple"])
async def test_failed_recursive_merge_keeps_all_source_content(pdf, tmp_path, kind):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        if count <= 2:
            return response("Before\n<table><tr><td>source</td></tr></table>\nAfter")
        if kind == "truncated":
            return response("<table>incomplete", "length")
        table = {"source_pages": [1, 2], "html": "<table><tr><td>merged</td></tr></table>"}
        return response(json.dumps([table, table]))

    config = RemoteOCRConfig(crosspage_artifact_dir=tmp_path / "artifacts")
    result = await reconstruct_document_tables(
        str(pdf), [1, 2], 150, "document_markdown", backend(config, handle), config, _renderer_factory=FakeRenderer
    )
    assert not result["success"] and result["partial_success"] and not result["merged_tables"]
    assert result["document_markdown"].count("source") == 2
    assert result["raw_stages"][-1]["raw_output"]


async def test_crosspage_profile_is_explicit():
    async with Client(create_server(RemoteOCRConfig(tool_profile="crosspage"))) as client:
        assert "reconstruct_document_tables" in {tool.name for tool in await client.list_tools()}
    async with Client(create_server(RemoteOCRConfig(tool_profile="document"))) as client:
        assert "reconstruct_document_tables" not in {tool.name for tool in await client.list_tools()}


def test_nonfinite_structured_json_is_never_exposed_as_valid_data():
    raw = '[{"bbox":[1,2,3,4],"category":"text","content":"visible","extra":NaN}]'
    result = parse_task_result({"success": True, "text": raw}, "document_structured")
    assert not result["success"] and not result["json_validated"] and result["data"] == []
    assert result["raw_output"] == raw
    json.dumps(result, allow_nan=False)


async def test_document_raw_output_budget_returns_retained_partial_pages(pdf, tmp_path):
    config = RemoteOCRConfig(crosspage_artifact_dir=tmp_path / "artifacts", max_output_chars=1000)
    raw = "kept " + "x" * 795
    result = await reconstruct_document_tables(
        str(pdf),
        [1, 2],
        150,
        "document_markdown",
        backend(config, lambda r: response(raw)),
        config,
        _renderer_factory=FakeRenderer,
    )
    assert not result["success"] and result["partial_success"] and result["parsed_pages"] == 1
    assert "kept" in result["document_markdown"]
    assert result["raw_stages"][-1]["error_code"] == "DOCUMENT_OUTPUT_LIMIT"
    assert result["raw_stages"][-1]["omitted_chars"] == len(raw)


async def test_recursive_degraded_render_is_not_reported_complete(pdf, tmp_path):
    class DegradedRenderer(FakeRenderer):
        def render(self, source, owner, paths, directory, name):
            rendered = super().render(source, owner, paths, directory, name)
            (Path(directory) / (name + ".render.json")).write_text(
                json.dumps({"status": "DEGRADED", "formula_fallbacks": 1})
            )
            return rendered

    count = 0

    def handle(request):
        nonlocal count
        count += 1
        return (
            response("<table><tr><td>source</td></tr></table>")
            if count <= 3
            else response('[{"source_pages":[1,2],"html":"<table><tr><td>merged</td></tr></table>"}]')
        )

    config = RemoteOCRConfig(crosspage_artifact_dir=tmp_path / "artifacts")
    result = await reconstruct_document_tables(
        str(pdf),
        [1, 2, 3],
        150,
        "document_markdown",
        backend(config, handle),
        config,
        _renderer_factory=DegradedRenderer,
    )
    assert not result["success"] and result["partial_success"] and result["status"] == "PARTIAL"
    assert result["error"]["type"] == "RenderDegraded"
    assert result["render_warnings"][0]["formula_fallbacks"] == 1


def test_document_worker_does_not_starve_image_encoding_pool(pdf, tmp_path):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    async def run():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        count = 0

        def handle(request):
            nonlocal count
            count += 1
            return (
                response("<table><tr><td>source</td></tr></table>")
                if count <= 2
                else response('[{"source_pages":[1,2],"html":"<table><tr><td>merged</td></tr></table>"}]')
            )

        config = RemoteOCRConfig(crosspage_artifact_dir=tmp_path / "artifacts")
        return await asyncio.wait_for(
            reconstruct_document_tables(
                str(pdf),
                [1, 2],
                150,
                "document_markdown",
                backend(config, handle),
                config,
                _renderer_factory=FakeRenderer,
            ),
            timeout=10,
        )

    assert asyncio.run(run())["success"]


@pytest.mark.parametrize(
    "task,raw,options",
    [
        (
            "reference_grounding",
            '[{"bbox":[1,2,3,4],"reference":"Total"},{"bbox":[1,2,3,4],"reference":"Invoice"}]',
            {"references": ["Invoice", "Total"]},
        ),
        ("word_spotting", '[{"bbox":[1,2,3,4],"text":" padded "}]', {}),
        ("figure_captioning", '[{"bbox":[1,2,3,4],"category":"image","content":"unwrapped caption"}]', {}),
    ],
)
async def test_remaining_prompt_order_and_caption_constraints(pdf, task, raw, options):
    config = RemoteOCRConfig()
    result = await analyze_page(str(pdf), 1, 150, task, backend(config, lambda r: response(raw)), config, **options)
    assert not result["success"] and result["raw_output"] == raw
