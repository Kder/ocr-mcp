"""Remote OCR contract and PDF orchestration tests; no GPU or model downloads."""

import asyncio
import base64
import importlib.util
import io
import json
import sys
from pathlib import Path

import httpx
import pymupdf
import pytest
from fastmcp import Client
from PIL import Image

from ocr_mcp.backends.ovisocr3_backend import OvisOCR3Backend
from ocr_mcp.core.http_config import RemoteOCRConfig
from ocr_mcp.document_pipeline import process_remote_document
from ocr_mcp.http_server import create_server


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (80, 60), "white").save(path)
    return path


def remote(handler, **kwargs):
    return OvisOCR3Backend(RemoteOCRConfig(**kwargs), transport=httpx.MockTransport(handler))


def ok(text="中文 <table><tr><td>42</td></tr></table> $x^2$", **kwargs):
    return httpx.Response(
        200, json={"model": "OvisOCR3", "choices": [{"message": {"content": text}, "finish_reason": "stop"}], **kwargs}
    )


@pytest.mark.parametrize("kind", ["PNG", "JPEG", "WEBP", "TIFF", "BMP"])
async def test_image_protocol(tmp_path, kind):
    path = tmp_path / f"input.{kind.lower()}"
    Image.new("RGB", (80, 60), "white").save(path, kind)

    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-secret"
        data = json.loads(request.content)
        assert data["model"] == "OvisOCR3"
        assert "tools" not in data and data["stream"] is False
        content = data["messages"][0]["content"]
        assert content[1]["text"] == "Convert this page to Markdown."
        encoded = content[0]["image_url"]["url"].split(",")[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as sent:
            assert sent.size == (80, 60) and sent.format == "PNG"
        return ok()

    result = await remote(handler, ovisocr3_api_key="test-secret").process_image(str(path))
    assert result["success"]
    assert result["text"] == "中文 <table><tr><td>42</td></tr></table> $x^2$"
    assert result["confidence"] is None
    assert result["format"] == "markdown"


async def test_crop_text_and_no_auth(image):
    def handler(request):
        assert "authorization" not in request.headers
        content = json.loads(request.content)["messages"][0]["content"]
        assert content[1]["text"] == "Extract all text from this image."
        with Image.open(io.BytesIO(base64.b64decode(content[0]["image_url"]["url"].split(",")[1]))) as sent:
            assert sent.size == (20, 10)
        return ok("text")

    result = await remote(handler, ovisocr3_api_key="").process_image(str(image), "text", region=[0, 0, 20, 10])
    assert result["success"] and result["format"] == "text"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": None}}]},
        [],
        {"choices": [{"message": {"content": ""}}]},
    ],
)
async def test_malformed_responses(image, body):
    result = await remote(lambda r: httpx.Response(200, json=body)).process_image(str(image))
    assert result["error_code"] == "INVALID_RESPONSE"


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
async def test_http_error_does_not_leak_payload(image, status):
    result = await remote(lambda r: httpx.Response(status, text="secret-document-data")).process_image(str(image))
    assert result["error_code"] == "UPSTREAM_HTTP_ERROR"
    assert str(status) in result["error"] and "secret-document-data" not in str(result)


@pytest.mark.parametrize(
    "exc,code", [(httpx.ConnectError, "UPSTREAM_UNAVAILABLE"), (httpx.ReadTimeout, "UPSTREAM_TIMEOUT")]
)
async def test_connection_errors(image, exc, code):
    def handler(request):
        raise exc("secret-document-data", request=request)

    result = await remote(handler).process_image(str(image))
    assert result["error_code"] == code and "secret-document-data" not in str(result)


async def test_model_mismatch_and_truncation(image):
    result = await remote(lambda r: ok(model="other-model")).process_image(str(image))
    assert result["error_code"] == "MODEL_MISMATCH"
    result = await remote(
        lambda r: httpx.Response(
            200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}
        )
    ).process_image(str(image))
    assert result["error_code"] == "TRUNCATED_RESPONSE"


async def test_invalid_inputs_make_no_request(image, tmp_path):
    def forbidden(request):
        pytest.fail("Invalid input reached upstream")

    backend = remote(forbidden)
    assert (await backend.process_image(str(tmp_path / "missing.png")))["error_code"] == "FILE_NOT_FOUND"
    bad = tmp_path / "bad.png"
    bad.write_text("not an image")
    assert (await backend.process_image(str(bad)))["error_code"] == "INVALID_IMAGE"
    assert (await backend.process_image(str(image), region=[0, 0, 999, 999]))["error_code"] == "INVALID_IMAGE"
    assert not (await remote(forbidden, max_image_pixels=1).process_image(str(image)))["success"]
    assert not (await remote(forbidden, max_file_bytes=1).process_image(str(image)))["success"]
    assert not (await backend.process_image(str(image), "table"))["success"]


def make_pdf(path, image, types):
    with pymupdf.open() as doc:
        for kind in types:
            page = doc.new_page(width=180, height=180)
            if kind in {"text", "mixed"}:
                page.insert_text((15, 30), "Digital page text")
            if kind in {"scan", "mixed"}:
                page.insert_image(pymupdf.Rect(0, 40, 180, 175), filename=str(image))
        doc.save(path)


async def test_mixed_pdf_order_selection_and_partial_failure(image, tmp_path):
    path = tmp_path / "mixed.pdf"
    make_pdf(path, image, ["text", "scan", "mixed", "text"])
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return ok("Scanned page") if count == 1 else httpx.Response(503)

    backend = remote(handler)
    result = await process_remote_document(str(path), backend, backend.config)
    assert count == 2
    assert result["partial_success"] and not result["success"]
    assert [p["page"] for p in result["pages"]] == [1, 2, 3, 4]
    assert [p["method"] for p in result["pages"]] == ["digital_text", "ocr", "ocr", "digital_text"]
    assert result["processed_pages"] == 3 and result["failed_pages"] == 1
    assert "Digital page text" in result["text"] and "Scanned page" in result["text"] and "OCR failed" in result["text"]
    selected = await process_remote_document(str(path), backend, backend.config, pages=[4, 1, 4])
    assert selected["success"] and [p["page"] for p in selected["pages"]] == [1, 4]
    assert count == 2


async def test_all_scanned_pdf_and_force_ocr(image, tmp_path):
    path = tmp_path / "scan.pdf"
    make_pdf(path, image, ["scan", "scan", "text"])
    count = 0

    def handler(r):
        nonlocal count
        count += 1
        return ok(f"OCR {count}")

    backend = remote(handler)
    result = await process_remote_document(str(path), backend, backend.config, force_ocr=True)
    assert result["success"] and count == 3
    assert "# Page 1" in result["text"] and "# Page 3" in result["text"]
    assert all(p["method"] == "ocr" for p in result["pages"])


async def test_pdf_validation_and_limits(image, tmp_path):
    path = tmp_path / "document.pdf"
    make_pdf(path, image, ["text", "scan"])
    backend = remote(lambda r: ok())
    for pages in [[], [0], [3], [True]]:
        assert (await process_remote_document(str(path), backend, backend.config, pages=pages))[
            "error_code"
        ] == "INVALID_PAGES"
    assert (await process_remote_document(str(path), backend, backend.config, dpi=500))["error_code"] == "INVALID_DPI"
    assert (await process_remote_document(str(image), backend, backend.config, pages=[1]))[
        "error_code"
    ] == "INVALID_PAGES"
    assert (await process_remote_document(str(path), backend, RemoteOCRConfig(max_pdf_pages=1)))[
        "error_code"
    ] == "PAGE_LIMIT"
    assert (await process_remote_document(str(image), backend, RemoteOCRConfig(document_root=tmp_path / "other")))[
        "error_code"
    ] == "PATH_NOT_ALLOWED"
    path.write_bytes(b"corrupt pdf")
    assert (await process_remote_document(str(path), backend, backend.config))["error_code"] == "INVALID_DOCUMENT"


async def test_encrypted_pdf(image, tmp_path):
    path = tmp_path / "secret.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    backend = remote(lambda r: ok())
    assert (await process_remote_document(str(path), backend, backend.config))["error_code"] == "ENCRYPTED_PDF"


async def test_health_check_is_explicit_and_validates_model():
    calls = []

    def handler(r):
        calls.append(r.url.path)
        return httpx.Response(200, json={"data": [{"id": "OvisOCR3"}]})

    backend = remote(handler)
    assert backend.is_available() and calls == []
    assert (await backend.health_check())["success"]
    assert calls == ["/v1/models"]
    assert not (await remote(lambda r: httpx.Response(200, json={"data": []})).health_check())["success"]


async def test_requests_are_serialized(image):
    active = maximum = 0

    async def handler(r):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        await asyncio.sleep(0.01)
        active -= 1
        return ok()

    backend = remote(handler)
    results = await asyncio.gather(*(backend.process_image(str(image)) for _ in range(3)))
    assert all(r["success"] for r in results) and maximum == 1


async def test_mcp_tools_and_document_call(image, monkeypatch):
    import ocr_mcp.http_server as module

    monkeypatch.setattr(module, "OvisOCR3Backend", lambda config: remote(lambda r: ok()))
    async with Client(create_server()) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools} == {"process_document", "get_status"}
        status = await client.call_tool("get_status", {})
        assert status.data["upstream_checked"] is False
        result = await client.call_tool(
            "process_document", {"source_path": str(image), "backend": "ovisocr3", "ocr_mode": "markdown"}
        )
        assert not result.is_error and result.data["success"]
        missing = await client.call_tool("process_document", {"source_path": str(image.parent / "missing.png")})
        assert missing.data["error_code"] == "FILE_NOT_FOUND"


def test_lightweight_imports():
    assert "ocr_mcp.core.backend_manager" not in sys.modules
    for name in ["torch", "transformers", "cv2", "easyocr", "diffusers", "vllm", "playwright", "bs4", "ocr_mcp.server"]:
        assert name not in sys.modules


@pytest.mark.parametrize(
    "url", ["ftp://example.com", "http://user:secret@example.com/v1", "http://example.com?token=secret"]
)
def test_invalid_base_url(url):
    with pytest.raises(ValueError):
        RemoteOCRConfig(ovisocr3_base_url=url)


def test_environment_defaults_are_validated(monkeypatch):
    monkeypatch.setenv("OVISOCR3_TIMEOUT", "-1")
    with pytest.raises(ValueError):
        RemoteOCRConfig()
    monkeypatch.setenv("OVISOCR3_TIMEOUT", "180")
    monkeypatch.setenv("OVISOCR3_BASE_URL", "file:///tmp/service")
    with pytest.raises(ValueError):
        RemoteOCRConfig()


async def test_output_limits_and_render_limits(image, tmp_path):
    backend = remote(lambda r: ok("long OCR output"), max_output_chars=3)
    assert (await backend.process_image(str(image)))["error_code"] == "OUTPUT_LIMIT"
    pdf = tmp_path / "limits.pdf"
    make_pdf(pdf, image, ["text", "scan"])
    result = await process_remote_document(str(pdf), backend, backend.config)
    assert not result["success"] and result["failed_pages"] == 2
    bounded = remote(lambda r: pytest.fail("Oversized page reached upstream"), max_image_pixels=1)
    result = await process_remote_document(str(pdf), bounded, bounded.config)
    assert result["partial_success"] and result["processed_pages"] == 1
    assert result["pages"][1]["error_code"] == "PAGE_PROCESSING_FAILED"


async def test_root_rejects_escaping_symlinks(image, tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    link = root / "outside.png"
    link.symlink_to(image)
    backend = remote(lambda r: pytest.fail("Escaping path reached upstream"))
    result = await process_remote_document(str(link), backend, RemoteOCRConfig(document_root=root))
    assert result["error_code"] == "PATH_NOT_ALLOWED"
