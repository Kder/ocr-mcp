"""OpenAI Chat Completions OCR, with an OvisOCR3 preset and no local ML imports."""

import asyncio
import base64
import io
import warnings
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, UnidentifiedImageError

from ..core.backend import OCRBackend
from ..core.http_config import RemoteOCRConfig


class OpenAICompatibleOCRBackend(OCRBackend):
    def __init__(self, config: RemoteOCRConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__("openai-ocr", config)
        self._available = True  # Static configuration only; health_check is explicitly dynamic.
        self._transport = transport
        self._gate = asyncio.Semaphore(1)  # Serialize NPU requests across all documents.

    def _client(self) -> httpx.AsyncClient:
        key = self.config.ovisocr3_api_key.get_secret_value()
        return httpx.AsyncClient(
            timeout=self.config.ovisocr3_timeout,
            headers={"Authorization": f"Bearer {key}"} if key else {},
            transport=self._transport,
            follow_redirects=False,
        )

    def _error(self, code: str, message: str) -> dict[str, Any]:
        return {"success": False, "backend": self.name, "error_code": code, "error": message}

    def _encode_image(self, image_path: str, region: list[int] | None) -> tuple[str, str]:
        path = Path(image_path)
        if path.stat().st_size > self.config.max_file_bytes:
            raise ValueError("Image exceeds OCR_MAX_FILE_BYTES")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in {"PNG", "JPEG", "WEBP", "TIFF", "BMP"}:
                    raise ValueError("Unsupported image format")
                if image.width * image.height > self.config.max_image_pixels:
                    raise ValueError("Image exceeds OCR_MAX_IMAGE_PIXELS")
                if getattr(image, "n_frames", 1) > 1:
                    raise ValueError("Multi-frame images are unsupported; convert to a PDF or single images")
                image.load()
                if region is not None:
                    if len(region) != 4 or any(type(n) is not int for n in region):
                        raise ValueError("Region must be [x, y, width, height] integers")
                    x, y, w, h = region
                    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > image.width or y + h > image.height:
                        raise ValueError("Region falls outside the image")
                    image = image.crop((x, y, x + w, y + h))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="PNG")
        raw = buffer.getvalue()
        if len(raw) > self.config.max_file_bytes:
            raise ValueError("Encoded image exceeds OCR_MAX_FILE_BYTES")
        return "image/png", base64.b64encode(raw).decode("ascii")

    async def process_image(
        self, image_path: str, mode: str = "markdown", region: list[int] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        if mode not in {"text", "markdown", "format", "auto"}:
            return self._error("INVALID_MODE", "Supported modes: text, markdown, format, auto")
        try:
            mime, encoded = await asyncio.to_thread(self._encode_image, image_path, region)
        except FileNotFoundError:
            return self._error("FILE_NOT_FOUND", "Image does not exist")
        except (
            OSError,
            ValueError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ):
            return self._error("INVALID_IMAGE", "Unreadable, unsupported, oversized image or invalid region")
        prompt = "Extract all text from this image." if mode == "text" else self.config.ovisocr3_prompt
        return await self._complete([(mime, encoded)], prompt, "text" if mode == "text" else "markdown")

    async def run_prompt(
        self, image_paths: list[str], prompt: str, output_format: str, *, allow_truncated: bool = False
    ) -> dict[str, Any]:
        """Run a pinned task prompt on one or two images, without model tool calls."""
        if not 1 <= len(image_paths) <= 2 or len(prompt) > self.config.max_prompt_chars:
            return self._error("INPUT_LIMIT", "Tasks accept one or two images and a bounded prompt")
        try:
            images = [await asyncio.to_thread(self._encode_image, path, None) for path in image_paths]
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            return self._error("INVALID_IMAGE", "Unreadable, unsupported or oversized task image")
        result = await self._complete(images, prompt, output_format)
        if allow_truncated and result.get("error_code") == "TRUNCATED_RESPONSE":
            return {
                **result,
                "success": True,
                "text": result["raw_output"],
                "finish_reason": "length",
                "partial_success": True,
            }
        if result.get("success") and result.get("finish_reason") != "stop":
            return {
                **result,
                "success": False,
                "error_code": "INCOMPLETE_TASK_RESPONSE",
                "error": "Task response must explicitly finish with stop",
                "raw_output": result["text"],
            }
        return result

    async def _complete(self, images: list[tuple[str, str]], prompt: str, output_format: str) -> dict[str, Any]:
        payload = {
            "model": self.config.ovisocr3_model,
            "stream": False,
            "temperature": 0,
            "max_tokens": self.config.ovisocr3_max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
                        for mime, encoded in images
                    ]
                    + [{"type": "text", "text": prompt}],
                }
            ],
        }
        try:
            async with self._gate, self._client() as client:
                response = await client.post(f"{self.config.ovisocr3_base_url}/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
            if data.get("model") and data["model"] != self.config.ovisocr3_model:
                return self._error("MODEL_MISMATCH", "Service returned a different model; no fallback is permitted")
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty or non-text response")
            if len(content) > self.config.max_output_chars:
                return self._error("OUTPUT_LIMIT", "OCR response exceeds OCR_MAX_OUTPUT_CHARS")
            if data["choices"][0].get("finish_reason") == "length":
                return {
                    **self._error("TRUNCATED_RESPONSE", "OCR response reached the model output limit"),
                    "raw_output": content,
                    "finish_reason": "length",
                }
            return {
                "success": True,
                "backend": self.name,
                "model": self.config.ovisocr3_model,
                "text": content,
                "finish_reason": data["choices"][0].get("finish_reason"),
                "format": output_format,
                "confidence": None,
                "confidence_source": "not_provided",
                "metadata": {"provider": "openai-compatible", "remote_api": True},
            }
        except httpx.TimeoutException:
            return self._error("UPSTREAM_TIMEOUT", "OCR service timed out")
        except httpx.HTTPStatusError as exc:
            return self._error(
                "UPSTREAM_HTTP_ERROR",
                f"OCR service returned HTTP {exc.response.status_code}; check model ID, credentials and service logs",
            )
        except httpx.RequestError:
            return self._error("UPSTREAM_UNAVAILABLE", "Cannot connect to OCR service")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return self._error("INVALID_RESPONSE", "OCR service returned an invalid Chat Completions response")

    async def health_check(self) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(f"{self.config.ovisocr3_base_url}/models")
                response.raise_for_status()
                ids = [model["id"] for model in response.json()["data"]]
            return {
                "success": self.config.ovisocr3_model in ids,
                "model": self.config.ovisocr3_model,
                "model_present": self.config.ovisocr3_model in ids,
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return self._error("HEALTH_CHECK_FAILED", "Could not query OCR service models")

    def get_capabilities(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": True,
            "availability": "configured; not a connectivity check",
            "remote_api": True,
            "modes": ["text", "markdown", "format"],
            "local_model": False,
        }


class OvisOCR3Backend(OpenAICompatibleOCRBackend):
    """Halogen/OvisOCR3 default configuration, using the same generic adapter."""

    def __init__(self, config: RemoteOCRConfig, **kwargs: Any):
        super().__init__(config, **kwargs)
        self.name = "ovisocr3"
