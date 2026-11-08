"""Dependency-light OCR backend contract (also re-exported by backend_manager)."""

from typing import Any

from .http_config import RemoteOCRConfig


class OCRBackend:
    """Base class for OCR backends."""

    def __init__(self, name: str, config: RemoteOCRConfig):
        self.name = name
        self.config = config
        self._available = False

    def is_available(self) -> bool:
        """Check if this backend is available."""
        return self._available

    async def process_image(self, image_path: str, mode: str = "text", **kwargs) -> dict[str, Any]:
        """Process an image with this backend."""
        raise NotImplementedError("Subclasses must implement process_image")

    def get_capabilities(self) -> dict[str, Any]:
        """Get backend capabilities."""
        return {
            "name": self.name,
            "available": self.is_available(),
            "modes": ["text"],  # Default capabilities
            "languages": ["en"],
            "gpu_support": False,
        }
