"""Pinned task contracts, opt-in tool discovery and native two-page reconstruction."""

import json

import httpx
import pymupdf
import pytest
from fastmcp import Client
from PIL import Image

from ocr_mcp.backends.ovisocr3_backend import OvisOCR3Backend
from ocr_mcp.core.http_config import RemoteOCRConfig
from ocr_mcp.document_tasks import analyze_page, reconstruct_table
from ocr_mcp.http_server import create_server
from ocr_mcp.task_prompts import TASK_FORMATS, parse_task_result, task_prompt


@pytest.fixture
def source(tmp_path):
    image = tmp_path / "image.png"
    Image.new("RGB", (60, 60), "white").save(image)
    path = tmp_path / "table.pdf"
    with pymupdf.open() as document:
        for _ in range(3):
            document.new_page(width=100, height=100).insert_text((10, 20), "Digital page")
        document.save(path)
    return path, image


def result(text):
    return httpx.Response(
        200, json={"model": "OvisOCR3", "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    )


def backend_for(handler):
    config = RemoteOCRConfig()
    return OvisOCR3Backend(config, transport=httpx.MockTransport(handler))


async def test_crosspage_native_two_images_and_provenance(source):
    path, _ = source
    requests = []

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert data["temperature"] == 0 and data["max_tokens"] == 16384 and "tools" not in data
        if len(requests) < 3:
            assert len(data["messages"][0]["content"]) == 2
            return result('<table><tr><td>Part</td></tr></table>\n<img src="images/bbox_1_2_3_4.jpg" />')
        content = data["messages"][0]["content"]
        assert len(content) == 3 and all(c["type"] == "image_url" for c in content[:2])
        prompt = content[2]["text"]
        assert '<INPUT_PAGE index="1">' in prompt and '<INPUT_PAGE index="2">' in prompt
        assert "page_1/images/bbox_1_2_3_4.jpg" in prompt and "page_2/images/bbox_1_2_3_4.jpg" in prompt
        return result(json.dumps([{"source_pages": [1, 2], "html": "<table><tr><td>Complete</td></tr></table>"}]))

    backend = backend_for(handler)
    response = await reconstruct_table(str(path), [2, 3], 150, backend, backend.config)
    assert response["success"] and response["continuation"] and len(requests) == 3
    assert response["tables"][0]["source_pages"] == [2, 3]
    assert response["image_reference_pages"] == {"page_1": 2, "page_2": 3}
    assert not response["recursive_multi_page"] and response["scope"] == "adjacent_pair"
    assert response["raw_output"] and len(response["pages"]) == 2


@pytest.mark.parametrize("negative", [True, False])
async def test_table_negative_predictions_and_gate(source, negative):
    path, _ = source
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) <= 2:
            return result("<table><tr><td>x</td></tr></table>" if negative else "No table parsed")
        return result('[{"source_pages":[],"html":"No cross-page table exists between these pages."}]')

    backend = backend_for(handler)
    response = await reconstruct_table(str(path), [1, 2], 150, backend, backend.config)
    assert response["success"] and not response["continuation"] and response["tables"] == []
    assert len(calls) == (3 if negative else 2)


@pytest.mark.parametrize("pages", [[1], [1, 3], [3, 2], [1, 2, 3], [True, 2]])
async def test_reconstruction_requires_one_adjacent_pair(source, pages):
    backend = backend_for(lambda request: pytest.fail("Invalid pages reached upstream"))
    response = await reconstruct_table(str(source[0]), pages, 150, backend, backend.config)
    assert response["error_code"] == "INVALID_PAGES"


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '[{"source_pages":[1],"html":"<table></table>"}]',
        '[{"source_pages":[1,2],"html":"plain text"}]',
        "malformed",
    ],
)
async def test_invalid_reconstruction_is_not_success(source, content):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return result("<table><tr><td>x</td></tr></table>" if count < 3 else content)

    backend = backend_for(handler)
    response = await reconstruct_table(str(source[0]), [1, 2], 150, backend, backend.config)
    assert not response["success"] and not response.get("tables")


async def test_stage1_truncation_preserves_raw_without_merging(source):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "<table>incomplete"}, "finish_reason": "length"}]}
        )

    backend = backend_for(handler)
    response = await reconstruct_table(str(source[0]), [1, 2], 150, backend, backend.config)
    assert not response["success"] and count == 1
    assert response["pages"][0]["raw_output"] == "<table>incomplete"


async def test_analysis_forces_visual_path_on_digital_pdf(source):
    def handler(request):
        prompt = json.loads(request.content)["messages"][0]["content"][-1]["text"]
        assert "without transcription" not in prompt  # official layout prompt itself defines fields
        assert '"bbox"' in prompt and '"category"' in prompt
        return result('[{"bbox":[10,20,900,200],"category":"text"}]')

    backend = backend_for(handler)
    response = await analyze_page(str(source[0]), 1, 150, "document_layout", backend, backend.config)
    assert response["success"] and response["data"][0]["bbox"] == [10, 20, 900, 200]
    assert response["coordinate_system"] == "normalized_xyxy_0_999"


@pytest.mark.parametrize("task", list(TASK_FORMATS))
def test_official_prompts_are_packaged(task):
    # Parameterized tasks supply the placeholders required by their model-card contracts.
    prompt = task_prompt(
        task,
        question="What is shown?",
        references=["Total"],
        target_language="English",
        candidate_labels=["invoice", "letter"],
        keys=["total"],
        template={"total": ""},
    )
    assert len(prompt) > 20
    assert "{question}" not in prompt and "{keys_json}" not in prompt and "{schema_json}" not in prompt


def test_literal_template_replacement_and_normalized_regions():
    prompt = task_prompt("kie_schema", template={"{schema_json}": "", "nested": {"field": ""}})
    assert '"{schema_json}": ""' in prompt  # never recursively substitute user text
    with pytest.raises(ValueError):
        task_prompt("region_ocr", target_region=[0, 0, 1000, 999])
    with pytest.raises(ValueError):
        task_prompt("text_translation")


@pytest.mark.parametrize(
    "raw", ["not JSON", '[{"bbox":[0,0,1001,10],"category":"text"}]', '[{"bbox":[0,0,10,10]}]', '{"bbox":[0,0,10,10]}']
)
def test_task_json_validation_preserves_raw(raw):
    response = parse_task_result({"success": True, "text": raw}, "document_layout")
    assert not response["success"] and response["raw_output"] == raw


async def test_tool_profiles_and_explicit_allowlist(source, monkeypatch):
    import ocr_mcp.http_server as module

    monkeypatch.setattr(module, "OvisOCR3Backend", lambda config: backend_for(lambda r: result('{"total":"42"}')))
    async with Client(create_server(RemoteOCRConfig(tool_profile="document"))) as client:
        assert {tool.name for tool in await client.list_tools()} == {
            "process_document",
            "get_status",
            "analyze_document",
            "extract_information",
            "reconstruct_table",
        }
        extracted = await client.call_tool("extract_information", {"source_path": str(source[1]), "fields": ["total"]})
        assert extracted.data["success"] and extracted.data["data"]["total"] == "42"
        invalid = await client.call_tool(
            "extract_information", {"source_path": str(source[1]), "fields": ["total"], "json_template": {"total": ""}}
        )
        assert not invalid.data["success"]
        status = await client.call_tool("get_status", {})
        assert len(status.data["exposed_tools"]) == 5
    async with Client(create_server(RemoteOCRConfig(exposed_tools="process_document,reconstruct_table"))) as client:
        assert {tool.name for tool in await client.list_tools()} == {"process_document", "reconstruct_table"}
    with pytest.raises(ValueError):
        create_server(RemoteOCRConfig(exposed_tools="process_document,typo"))


async def test_task_requires_explicit_complete_finish(source):
    backend = backend_for(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "content_filter"}]}
        )
    )
    response = await analyze_page(str(source[1]), 1, 150, "plain_ocr", backend, backend.config)
    assert not response["success"] and response["error_code"] == "INCOMPLETE_TASK_RESPONSE"
    assert response["raw_output"] == "partial"
