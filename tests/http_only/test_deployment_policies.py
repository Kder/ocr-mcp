"""Default path confinement, explicit PDF policy and safe independent cleanup."""

import json
import os
import stat
import time
import uuid

import httpx
import pymupdf
import pytest
from fastmcp import Client
from PIL import Image

from ocr_mcp.artifacts import artifact_job, cleanup_artifacts
from ocr_mcp.backends.ovisocr3_backend import OvisOCR3Backend
from ocr_mcp.core.http_config import RemoteOCRConfig
from ocr_mcp.document_pipeline import process_remote_document
from ocr_mcp.http_server import create_server


@pytest.mark.parametrize("value", [None, "", "  "])
def test_unset_or_blank_root_is_confined(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    if value is None:
        monkeypatch.delenv("OCR_DOCUMENT_ROOT", raising=False)
    else:
        monkeypatch.setenv("OCR_DOCUMENT_ROOT", value)
    assert RemoteOCRConfig().document_root == tmp_path / "documents"
    with pytest.raises(ValueError):
        RemoteOCRConfig(document_root=None)
    with pytest.raises(ValueError):
        RemoteOCRConfig(document_root=tmp_path.anchor)


async def test_all_file_tools_reject_outside_default_root(monkeypatch, tmp_path):
    import ocr_mcp.http_server as module

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OCR_DOCUMENT_ROOT", raising=False)
    root = tmp_path / "documents"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(outside)
    link = root / "link.pdf"
    link.symlink_to(outside)

    def forbidden(request):
        pytest.fail("Outside file reached upstream")

    config = RemoteOCRConfig(tool_profile="crosspage")
    monkeypatch.setattr(
        module, "OvisOCR3Backend", lambda config: OvisOCR3Backend(config, transport=httpx.MockTransport(forbidden))
    )
    async with Client(create_server(config)) as client:
        for path in (outside, root / ".." / "outside.pdf", link):
            for tool, arguments in [
                ("process_document", {}),
                ("analyze_document", {}),
                ("extract_information", {}),
                ("reconstruct_table", {"pages": [1, 2]}),
                ("reconstruct_document_tables", {"pages": [1, 2]}),
            ]:
                result = await client.call_tool(tool, {"source_path": str(path), **arguments})
                assert not result.data["success"], (tool, path)


async def test_pdf_strategies_and_force_override(tmp_path):
    image = tmp_path / "logo.png"
    Image.new("RGB", (20, 20), "white").save(image)
    path = tmp_path / "mixed.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=120, height=120)
        page.insert_text((10, 20), "Exact amount 123.45")
        page.insert_image(pymupdf.Rect(50, 50, 70, 70), filename=str(image))
        pdf.new_page(width=120, height=120).insert_image(pymupdf.Rect(0, 0, 120, 120), filename=str(image))
        pdf.save(path)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200, json={"model": "OvisOCR3", "choices": [{"message": {"content": "OCR"}, "finish_reason": "stop"}]}
        )

    config = RemoteOCRConfig()
    backend = OvisOCR3Backend(config, transport=httpx.MockTransport(handle))
    auto = await process_remote_document(str(path), backend, config)
    assert auto["success"] and len(calls) == 2  # conservative auto behavior is unchanged
    calls.clear()
    text = await process_remote_document(str(path), backend, config, pdf_strategy="text")
    assert not calls and not text["success"] and text["partial_success"]
    assert "123.45" in text["pages"][0]["text"]
    assert text["pages"][1]["error_code"] == "NO_TEXT_LAYER"
    forced = await process_remote_document(str(path), backend, config, pdf_strategy="text", force_ocr=True)
    assert forced["success"] and forced["pdf_strategy"] == "ocr" and len(calls) == 2
    invalid = await process_remote_document(str(path), backend, config, pdf_strategy="guess")
    assert invalid["error_code"] == "INVALID_PDF_STRATEGY"


def make_job(root, age, size=100):
    with artifact_job(root, uuid.uuid4().hex) as job:
        (job / "data.txt").write_bytes(b"x" * size)
    marker = job / ".ocr-mcp-job.json"
    os.utime(marker, (age, age))
    return job


def test_cleanup_retention_dry_run_and_private_permissions(tmp_path):
    root = tmp_path / "artifacts"
    now = time.time()
    old = make_job(root, now - 8 * 86400)
    recent = make_job(root, now)
    if os.name == "posix":
        assert stat.S_IMODE(old.stat().st_mode) == 0o700
    planned = cleanup_artifacts(root, 7, 5, now=now)
    assert old.exists() and recent.exists() and planned["jobs"][0]["id"] == old.name
    actual = cleanup_artifacts(root, 7, 5, apply=True, now=now)
    assert not old.exists() and recent.exists() and actual["jobs"][0]["reason"] == "expired"


def test_cleanup_oldest_first_quota_and_active_job_lock(tmp_path):
    root = tmp_path / "artifacts"
    now = time.time()
    old = make_job(root, now - 50, 2048)
    recent = make_job(root, now - 10, 2048)
    with artifact_job(root, uuid.uuid4().hex) as active:
        (active / "data.txt").write_bytes(b"x" * 2048)
        report = cleanup_artifacts(root, 0, 0.000001, apply=True, now=now)
        assert active.exists() and active.name in report["active_skipped"]
        assert [job["id"] for job in report["jobs"]] == [old.name, recent.name]
        assert not report["quota_satisfied"]
    report = cleanup_artifacts(root, 0, 0.000001, apply=True, now=now)
    assert not active.exists() and report["quota_satisfied"]


def test_cleanup_does_not_follow_links_or_delete_unmanaged_content(tmp_path):
    root = tmp_path / "artifacts"
    now = time.time()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "keep.txt"
    secret.write_text("keep")
    job = make_job(root, now - 10 * 86400)
    (job / "linked-directory").symlink_to(outside, target_is_directory=True)
    (root / uuid.uuid4().hex).symlink_to(outside, target_is_directory=True)
    unmanaged = root / uuid.uuid4().hex
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("not an OCR job")
    report = cleanup_artifacts(root, 7, 5, apply=True, now=now)
    assert len(report["jobs"]) == 1 and not job.exists()
    assert secret.read_text() == "keep" and unmanaged.exists()


def test_failed_job_is_marked_and_eligible_for_later_cleanup(tmp_path):
    root = tmp_path / "artifacts"
    with pytest.raises(RuntimeError), artifact_job(root, uuid.uuid4().hex) as job:
        raise RuntimeError("simulated failure")
    assert json.loads((job / ".ocr-mcp-job.json").read_text())["format"] == "ocr-mcp-artifact-v1"
    future = time.time() + 8 * 86400
    cleanup_artifacts(root, 7, 0, apply=True, now=future)
    assert not job.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission policy")
def test_insecure_artifact_root_is_rejected(tmp_path):
    root = tmp_path / "shared"
    root.mkdir(mode=0o777)
    root.chmod(0o777)
    with pytest.raises(ValueError), artifact_job(root, uuid.uuid4().hex):
        pass
    with pytest.raises(ValueError):
        cleanup_artifacts(root, 7, 5, apply=True)


@pytest.mark.parametrize("settings_source", ["env", "dotenv"])
def test_installed_cleanup_command_previews_then_applies(tmp_path, settings_source):
    import subprocess
    import sys
    from pathlib import Path

    root = tmp_path / "artifacts"
    job = make_job(root, time.time() - 8 * 86400)
    environment = os.environ.copy()
    settings = {"OCR_ARTIFACT_DIR": str(root), "OCR_ARTIFACT_RETENTION_DAYS": "7", "OCR_ARTIFACT_MAX_GB": "5"}
    if settings_source == "env":
        environment.update(settings)
    else:
        for key in settings:
            environment.pop(key, None)
        (tmp_path / ".env").write_text("\n".join(f"{key}='{value}'" for key, value in settings.items()))
    command = str(
        Path(sys.executable).parent / ("ocr-mcp-clean-artifacts.exe" if os.name == "nt" else "ocr-mcp-clean-artifacts")
    )
    planned = subprocess.run([command], env=environment, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert json.loads(planned.stdout)["dry_run"] and job.exists()
    applied = subprocess.run(
        [command, "--apply"], env=environment, cwd=tmp_path, check=True, capture_output=True, text=True
    )
    assert not json.loads(applied.stdout)["dry_run"] and not job.exists()
