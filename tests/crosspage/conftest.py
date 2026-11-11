"""Keep browser fixtures inside an explicit document root."""

import pytest


@pytest.fixture(autouse=True)
def document_root(tmp_path, monkeypatch):
    monkeypatch.setenv("OCR_DOCUMENT_ROOT", str(tmp_path))
