"""Opt-in real Chromium tests; inference is mocked but rendering is not."""

import base64
import io
import json
from pathlib import Path

import httpx
import pymupdf
from PIL import Image

from ocr_mcp._vendor.ovisocr3 import document_crosspage as reference
from ocr_mcp.backends.ovisocr3_backend import OvisOCR3Backend
from ocr_mcp.core.http_config import RemoteOCRConfig
from ocr_mcp.recursive_tables import BrowserRenderer, reconstruct_document_tables


def test_real_renderer_preserves_table_math_crop_and_removes_active_html(tmp_path):
    image = tmp_path / "original.png"
    Image.new("RGB", (500, 500), "green").save(image)
    config = RemoteOCRConfig()
    renderer = BrowserRenderer(config)
    source = """Heading

<table><tr><td rowspan="2">$$x^2$$</td><td>First</td></tr><tr><td>Second</td></tr></table>

<img src="page_1/images/bbox_100_100_500_500.jpg" onclick="alert('unsafe')" />
<script>document.body.innerHTML='lost';</script>"""
    try:
        rendered = renderer.render(source, 1, [str(image)], tmp_path / "render", "page")
        assert Path(rendered.image_path).is_file()
        diagnostics = json.loads((tmp_path / "render/page.render.json").read_text())
        assert diagnostics["status"] == "COMPLETE"
        assert diagnostics["math"] == 1 and diagnostics["formula_fallbacks"] == 0
        assert diagnostics["tables"] == 1 and diagnostics["cells"] == 3
        snapshot = (tmp_path / "render/page.html").read_text()
        assert "onclick" not in snapshot and "document.body.innerHTML" not in snapshot
        assert 'rowspan="2"' in snapshot
        assert list(rendered.alias_to_original.values()) == ["page_1/images/bbox_100_100_500_500.jpg"]
        assert next(iter(rendered.alias_to_original)) in rendered.rewritten_source
    finally:
        renderer.close()


async def test_actual_recursive_three_page_browser_render_and_crop_provenance(tmp_path):
    pdf = tmp_path / "three.pdf"
    with pymupdf.open() as document:
        for i in range(3):
            page = document.new_page(width=240, height=240)
            page.insert_text((20, 30), f"Original {i + 1}")
            page.draw_rect(pymupdf.Rect(40, 40, 150, 150), color=(0, 0.5, 0), fill=(0, 0.5, 0))
        document.save(pdf)
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) <= 3:
            n = len(calls)
            text = json.dumps(
                [
                    {
                        "bbox": [0, 0, 999, 999],
                        "category": "table",
                        "content": f'<table><tr><td>Part {n}<img src="images/bbox_100_100_500_500.jpg" /></td></tr></table>',
                    }
                ]
            )
        else:
            if len(calls) == 4:
                image_ref = "page_2/images/bbox_100_100_500_500.jpg"
            else:
                content = payload["messages"][0]["content"]
                assert "Rows 2+3" in content[-1]["text"]
                data = content[1]["image_url"]["url"].split(",")[1]
                with Image.open(io.BytesIO(base64.b64decode(data))) as image:
                    # The complete newly rendered page has Chromium's minimum viewport height.
                    assert image.height >= 900
                context = content[-1]["text"].split('<INPUT_PAGE index="2">', 1)[1]
                image_ref = reference.IMAGE_REF.search(context)[0]
                assert image_ref != "page_2/images/bbox_100_100_500_500.jpg"
            label = "Rows 2+3" if len(calls) == 4 else "Rows 1+2+3"
            text = json.dumps(
                [
                    {
                        "source_pages": [1, 2],
                        "html": f'<table><tr><td>{label} $$x^2$$<img src="{image_ref}" /></td></tr></table>',
                    }
                ]
            )
        return httpx.Response(
            200, json={"model": "OvisOCR3", "choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
        )

    config = RemoteOCRConfig(crosspage_artifact_dir=tmp_path / "artifacts")
    remote = OvisOCR3Backend(config, transport=httpx.MockTransport(handler))
    result = await reconstruct_document_tables(str(pdf), [1, 2, 3], 150, "document_structured", remote, config)
    assert result["success"], result
    assert [step["pages"] for step in result["steps"]] == [[2, 3], [1, 2]]
    assert result["steps"][1]["rendered_pages"] == [2]
    assert result["merged_tables"][0]["source_pages"] == [1, 2, 3]
    assert "page_3/images/bbox_100_100_500_500.jpg" in result["merged_tables"][0]["html"]
    crop = Path(result["artifact_dir"]) / "result/page_3/images/bbox_100_100_500_500.jpg"
    assert crop.is_file()
    assert len(calls) == 5
