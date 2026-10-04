from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from PIL import Image


def load_image(path: str) -> Tuple[bytes, Dict[str, Any]]:
    """Load an image from disk and return raw bytes plus basic metadata.

    Args:
        path: File path to the image.

    Returns:
        A tuple of (image_bytes, metadata_dict).
    """
    image_path = Path(path)
    with Image.open(image_path) as img:
        img.load()
        width, height = img.size
        image_format = (img.format or image_path.suffix.lower().lstrip("."))
        metadata = {
            "width": int(width),
            "height": int(height),
            "format": str(image_format).lower(),
            "mode": img.mode,
        }
        image_bytes = image_path.read_bytes()
        return image_bytes, metadata


def test_load_image() -> None:
    """Load a sample image and print basic metadata and first bytes."""
    sample_path = input("Enter image path: ").strip()
    if not sample_path:
        print("No image path provided.")
        return

    try:
        image_bytes, metadata = load_image(sample_path)
        print("Metadata:")
        print(metadata)
        print("First 20 bytes:")
        print(list(image_bytes[:20]))
    except FileNotFoundError:
        print(f"Image not found: {sample_path}")
    except Exception as exc:  # pragma: no cover - helpful CLI error reporting
        print(f"Error loading image: {exc}")
