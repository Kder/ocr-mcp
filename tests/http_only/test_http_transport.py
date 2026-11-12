"""Exercise the installed entry point over TCP against a mock Halogen HTTP server."""

import asyncio
import base64
import io
import json
import os
import socket
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pymupdf
import pytest
from fastmcp import Client
from PIL import Image


@asynccontextmanager
async def running_http_server(tmp_path, environment, port, *, stateful=False):
    """Start the real entry point, or the old stateful transport for upgrade tests."""
    command = [sys.executable, "-m", "ocr_mcp", "--host", "127.0.0.1", "--port", str(port)]
    if stateful:
        command = [
            sys.executable,
            "-c",
            "import sys; from ocr_mcp.http_server import create_server; "
            "create_server().run(transport='http', host='127.0.0.1', "
            "port=int(sys.argv[1]), path='/mcp', stateless_http=False)",
            str(port),
        ]
    url = f"http://127.0.0.1:{port}/mcp"
    with (tmp_path / "server.log").open("a") as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=log)
        try:
            async with httpx.AsyncClient() as client:
                for _ in range(100):
                    assert process.poll() is None, (tmp_path / "server.log").read_text()
                    try:
                        response = await client.get(url, timeout=0.5)
                        # Stateless Streamable HTTP intentionally has no GET stream.
                        if response.status_code in {200, 307, 405, 406}:
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("HTTP MCP did not become ready")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


async def rpc(client, url, method, params=None):
    response = await client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        messages = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
        body = next(message for message in messages if message.get("id") == 1)
    else:
        body = response.json()
    assert "error" not in body, body
    return response, body["result"]


async def test_stale_session_header_does_not_block_upgrade_or_restart(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = os.environ.copy()
    environment.update(OCR_MCP_PROFILE="basic", OCR_MCP_TOOLS="", OCR_DOCUMENT_ROOT=str(tmp_path))
    initialize = {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "session-regression", "version": "1"},
    }
    pdf = tmp_path / "text.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 40), "Text after restart")
        document.save(pdf)

    async with httpx.AsyncClient(headers={"Accept": "application/json, text/event-stream"}) as client:
        async with running_http_server(tmp_path, environment, port, stateful=True) as url:
            response, result = await rpc(client, url, "initialize", initialize)
            session = response.headers["mcp-session-id"]
            client.headers.update({"Mcp-Session-Id": session, "MCP-Protocol-Version": result["protocolVersion"]})
            response = await client.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            assert response.status_code == 202
            _, result = await rpc(client, url, "tools/call", {"name": "get_status"})
            assert result["structuredContent"]["success"]

        # Keep the real old session header across an upgrade and another restart.
        for _ in range(2):
            async with running_http_server(tmp_path, environment, port) as url:
                response, result = await rpc(client, url, "tools/list")
                assert "mcp-session-id" not in response.headers
                assert {tool["name"] for tool in result["tools"]} == {"process_document", "get_status"}
                _, result = await rpc(client, url, "tools/call", {"name": "get_status"})
                assert result["structuredContent"]["success"]
                _, result = await rpc(
                    client,
                    url,
                    "tools/call",
                    {"name": "process_document", "arguments": {"source_path": str(pdf)}},
                )
                assert result["structuredContent"]["success"]
                assert "Text after restart" in result["structuredContent"]["text"]
                # Reinitialization without an old ID also remains valid.
                session = client.headers.pop("Mcp-Session-Id")
                response, _ = await rpc(client, url, "initialize", initialize)
                assert "mcp-session-id" not in response.headers
                client.headers["Mcp-Session-Id"] = session


@pytest.mark.parametrize("profile", ["basic", "document", "crosspage"])
async def test_installed_http_server_end_to_end(tmp_path, profile):
    requests = []

    class Halogen(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/v1/models"
            self.respond({"data": [{"id": "OvisOCR3"}]})

        def do_POST(self):
            assert self.path == "/v1/chat/completions"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            assert payload["model"] == "OvisOCR3" and "tools" not in payload
            data = payload["messages"][0]["content"][0]["image_url"]["url"]
            with Image.open(io.BytesIO(base64.b64decode(data.split(",")[1]))) as image:
                image.verify()
            self.respond(
                {
                    "model": "OvisOCR3",
                    "choices": [{"message": {"content": "# Mock OCR\n\n中文 $E=mc^2$"}, "finish_reason": "stop"}],
                }
            )

        def respond(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Halogen)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = os.environ.copy()
    environment.update(
        OVISOCR3_BASE_URL=f"http://127.0.0.1:{upstream.server_port}/v1",
        OVISOCR3_MODEL="OvisOCR3",
        OVISOCR3_API_KEY="",
        OCR_MCP_PROFILE=profile,
        OCR_MCP_TOOLS="",
        OCR_DOCUMENT_ROOT=str(tmp_path),
    )
    image = tmp_path / "image.png"
    Image.new("RGB", (60, 60), "white").save(image)
    pdf = tmp_path / "mixed.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 40), "Embedded text")
        document.new_page(width=180, height=180).insert_image(pymupdf.Rect(0, 0, 180, 180), filename=str(image))
        document.save(pdf)
    try:
        async with running_http_server(tmp_path, environment, port) as url:
            async with Client(url) as client:
                expected = {"process_document", "get_status"}
                if profile in {"document", "crosspage"}:
                    expected |= {"analyze_document", "extract_information", "reconstruct_table"}
                if profile == "crosspage":
                    expected.add("reconstruct_document_tables")
                assert {t.name for t in await client.list_tools()} == expected
                status = await client.call_tool("get_status", {"check_upstream": True})
                assert status.data["success"] and status.data["upstream"]["model_present"]
                result = await client.call_tool("process_document", {"source_path": str(image)})
                assert result.data["success"] and "中文" in result.data["text"]
                result = await client.call_tool("process_document", {"source_path": str(pdf)})
                assert result.data["success"] and result.data["processed_pages"] == 2
                assert "Embedded text" in result.data["text"] and "Mock OCR" in result.data["text"]
                assert len(requests) == 2
                text_only = await client.call_tool(
                    "process_document", {"source_path": str(pdf), "pdf_strategy": "text"}
                )
                assert not text_only.data["success"] and text_only.data["partial_success"]
                assert text_only.data["pages"][1]["error_code"] == "NO_TEXT_LAYER"
                assert len(requests) == 2
                outside = await client.call_tool(
                    "process_document", {"source_path": str(tmp_path.parent / "outside.pdf")}
                )
                assert outside.data["error_code"] == "PATH_NOT_ALLOWED"
                assert len(requests) == 2
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)
