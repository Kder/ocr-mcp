"""Configuration for remote OCR; never initializes local models or cache directories."""

import os
from pathlib import Path
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
    max_file_bytes: int = Field(default_factory=lambda: int(env("OCR_MAX_FILE_BYTES", "52428800")), gt=0)
    max_image_pixels: int = Field(default_factory=lambda: int(env("OCR_MAX_IMAGE_PIXELS", "25000000")), gt=0)
    max_pdf_pages: int = Field(default_factory=lambda: int(env("OCR_MAX_PDF_PAGES", "100")), gt=0)
    max_output_chars: int = Field(default_factory=lambda: int(env("OCR_MAX_OUTPUT_CHARS", "1000000")), gt=0)
    document_root: Path | None = Field(
        default_factory=lambda: (
            Path(os.environ["OCR_DOCUMENT_ROOT"]).expanduser().resolve() if os.getenv("OCR_DOCUMENT_ROOT") else None
        )
    )

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
