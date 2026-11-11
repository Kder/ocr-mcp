"""Optional full-install compatibility for the remote OCR bridge."""

import httpx
import pytest
from PIL import Image

from ocr_mcp.backends.ovisocr3_backend import OvisOCR3Backend
from ocr_mcp.core.backend_manager import BackendManager, OCRBackend
from ocr_mcp.core.config import OCRConfig
from ocr_mcp.tools._processor import process_document


def test_remote_registry(config):
    manager = BackendManager(config)
    for name in ("ovisocr3", "openai-ocr"):
        assert manager._is_backend_available(name)
        backend = manager.get_backend(name)
        assert isinstance(backend, OCRBackend)
        assert backend.name == name
        assert backend.is_available()


@pytest.mark.asyncio
async def test_legacy_document_entry_preserves_null_confidence(config, tmp_path):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "# Remote text"}}]})

    config.document_root = tmp_path
    manager = BackendManager(config)
    manager.backends["ovisocr3"] = OvisOCR3Backend(config, transport=httpx.MockTransport(handler))
    path = tmp_path / "image.png"
    Image.new("RGB", (20, 20), "white").save(path)
    result = await process_document(
        str(path), backend="ovisocr3", mode="markdown", backend_manager=manager, config=config
    )
    assert result["success"] and result["confidence"] is None
    assert result["text"] == "# Remote text"
