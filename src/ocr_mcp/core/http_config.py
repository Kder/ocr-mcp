"""Configuration for remote OCR; never initializes local models or cache directories."""

import os
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def env(name: str, default: str) -> str:
    return os.getenv(name, default)


class RemoteOCRConfig(BaseModel):
    model_config = ConfigDict(validate_default=True)

    ovisocr3_base_url: str = Field(default_factory=lambda: env("OVISOCR3_BASE_URL", "http://127.0.0.1:8731/v1"))
    ovisocr3_model: str = Field(default_factory=lambda: env("OVISOCR3_MODEL", "OvisOCR3"), min_length=1)
    ovisocr3_api_key: SecretStr = Field(default_factory=lambda: SecretStr(env("OVISOCR3_API_KEY", "")))
    ovisocr3_timeout: float = Field(default_factory=lambda: float(env("OVISOCR3_TIMEOUT", "180")), gt=0, le=3600)
    ovisocr3_prompt: str = Field(
        default_factory=lambda: env("OVISOCR3_PROMPT", "Convert this page to Markdown."), min_length=1
    )
    tool_profile: Literal["basic", "document", "crosspage"] = Field(
        default_factory=lambda: env("OCR_MCP_PROFILE", "basic")
    )
    exposed_tools: str = Field(default_factory=lambda: env("OCR_MCP_TOOLS", ""))
    max_prompt_chars: int = Field(default_factory=lambda: int(env("OCR_MAX_PROMPT_CHARS", "200000")), gt=0)
    ovisocr3_max_tokens: int = Field(default_factory=lambda: int(env("OVISOCR3_MAX_TOKENS", "16384")), gt=0, le=16384)
    max_file_bytes: int = Field(default_factory=lambda: int(env("OCR_MAX_FILE_BYTES", "52428800")), gt=0)
    max_image_pixels: int = Field(default_factory=lambda: int(env("OCR_MAX_IMAGE_PIXELS", "25000000")), gt=0)
    max_pdf_pages: int = Field(default_factory=lambda: int(env("OCR_MAX_PDF_PAGES", "100")), gt=0)
    max_output_chars: int = Field(default_factory=lambda: int(env("OCR_MAX_OUTPUT_CHARS", "1000000")), gt=0)
    crosspage_max_pages: int = Field(default_factory=lambda: int(env("OCR_CROSSPAGE_MAX_PAGES", "20")), gt=0)
    crosspage_artifact_dir: Path = Field(
        default_factory=lambda: Path(
            env("OCR_ARTIFACT_DIR", str(Path(tempfile.gettempdir()) / "ocr-mcp-tables"))
        ).expanduser()
    )
    chromium_executable: str | None = Field(default_factory=lambda: os.getenv("OCR_CHROMIUM_EXECUTABLE") or None)
    artifact_retention_days: float = Field(
        default_factory=lambda: float(env("OCR_ARTIFACT_RETENTION_DAYS", "7")), ge=0, allow_inf_nan=False
    )
    artifact_max_gb: float = Field(
        default_factory=lambda: float(env("OCR_ARTIFACT_MAX_GB", "5")), ge=0, allow_inf_nan=False
    )
    document_root: Path = Field(
        default_factory=lambda: Path(env("OCR_DOCUMENT_ROOT", "").strip() or "documents").expanduser().resolve()
    )

    @field_validator("document_root")
    @classmethod
    def validate_document_root(cls, value: Path) -> Path:
        resolved = value.expanduser().resolve()
        if resolved == Path(resolved.anchor):
            raise ValueError("OCR_DOCUMENT_ROOT must not be a filesystem root")
        return resolved

    @field_validator("ovisocr3_base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        _ = parsed.port  # Reject malformed/non-numeric ports at configuration time.
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OCR base URL must be HTTP(S), without credentials, query or fragment")
        return value.rstrip("/")
