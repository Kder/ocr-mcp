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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pymupdf
from fastmcp import Client
from PIL import Image


async def test_installed_http_server_end_to_end(tmp_path):
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
        OCR_DOCUMENT_ROOT=str(tmp_path),
    )
    image = tmp_path / "image.png"
    Image.new("RGB", (60, 60), "white").save(image)
    pdf = tmp_path / "mixed.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 40), "Embedded text")
        document.new_page(width=180, height=180).insert_image(pymupdf.Rect(0, 0, 180, 180), filename=str(image))
        document.save(pdf)
    with (tmp_path / "server.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "ocr_mcp", "--host", "127.0.0.1", "--port", str(port)],
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient() as client:
                for _ in range(100):
                    assert process.poll() is None, (tmp_path / "server.log").read_text()
                    try:
                        response = await client.get(f"http://127.0.0.1:{port}/mcp", timeout=0.5)
                        if response.status_code in {200, 307, 406}:
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("HTTP MCP did not become ready")
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                assert {t.name for t in await client.list_tools()} == {"process_document", "get_status"}
                status = await client.call_tool("get_status", {"check_upstream": True})
                assert status.data["success"] and status.data["upstream"]["model_present"]
                result = await client.call_tool("process_document", {"source_path": str(image)})
                assert result.data["success"] and "中文" in result.data["text"]
                result = await client.call_tool("process_document", {"source_path": str(pdf)})
                assert result.data["success"] and result.data["processed_pages"] == 2
                assert "Embedded text" in result.data["text"] and "Mock OCR" in result.data["text"]
                assert len(requests) == 2
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=5)
