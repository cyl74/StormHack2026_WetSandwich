#gemini_analyzer.py
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict

from google import genai
from google.genai import types
from google.genai.errors import ServerError

from src.config import GEMINI_API_KEY, GEMINI_MODEL
from src.doodle_ai.image_loader import load_image

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", GEMINI_MODEL)
DEFAULT_ANALYSIS_PROMPT = (
    "Analyze this doodle image and return only valid JSON. "
    "Use this exact structure: {\n"
    "  \"description\": \"string\",\n"
    "  \"visual_features\": [\"string\", \"string\"],\n"
    "  \"mood\": \"string\",\n"
    "  \"composition\": \"string\",\n"
    "  \"energy\": \"string\"\n"
    "}. "
    "Do not include markdown fences or any extra commentary."
)


def _normalize_mime_type(format_name: str | None) -> str:
    mapping = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }
    return mapping.get((format_name or "").lower(), "image/png")


def _parse_response_payload(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`\n ")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini did not return valid JSON: {text}") from exc


def analyze_image_from_bytes(
    image_bytes: bytes,
    mime_type: str = "image/png",
    prompt: str = DEFAULT_ANALYSIS_PROMPT,
    model: str | None = None,
    max_retries: int = 4,
    base_delay: float = 2.0,
) -> Dict[str, Any]:
    """Send raw image bytes to Gemini and parse the JSON response."""
    if not image_bytes:
        raise ValueError("Empty image payload: no bytes were provided.")
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to your environment (.env or export).")

    client = genai.Client(api_key=GEMINI_API_KEY)
    model_name = model or DEFAULT_MODEL
    delay = base_delay

    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            text = getattr(response, "text", "").strip()
            if not text:
                raise ValueError("Gemini returned an empty response body.")
            return _parse_response_payload(text)
        except ServerError as exc:
            message = str(exc).lower()
            if "503" in message or "unavailable" in message or "high demand" in message:
                if attempt < max_retries:
                    print(
                        f"Gemini temporarily unavailable (attempt {attempt + 1}/{max_retries + 1}). "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue
            raise
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Gemini request failed: {exc}") from exc


def analyze_image_from_base64(
    encoded: str,
    prompt: str = DEFAULT_ANALYSIS_PROMPT,
    model: str | None = None,
    max_retries: int = 4,
    base_delay: float = 2.0,
) -> Dict[str, Any]:
    """Decode a base64 image string (with or without a data URL prefix) and send it to Gemini."""
    value = encoded.strip()
    prefix = ""
    if value.startswith("data:") and "," in value:
        prefix, value = value.split(",", 1)
    mime_type = "image/png"
    if prefix:
        mime_type = prefix.split(";", 1)[0].split(":", 1)[1]
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            mime_type = "image/png"
    try:
        image_bytes = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError(f"Invalid base64 image payload: {encoded[:80]!r}") from exc
    return analyze_image_from_bytes(
        image_bytes,
        mime_type=mime_type,
        prompt=prompt,
        model=model,
        max_retries=max_retries,
        base_delay=base_delay,
    )


def analyze_image(
    image_path: str,
    prompt: str = DEFAULT_ANALYSIS_PROMPT,
    max_retries: int = 4,
    base_delay: float = 2.0,
) -> Dict[str, Any]:
    """Load an image from disk and send it to Gemini for structured analysis."""
    image_bytes, metadata = load_image(image_path)
    mime_type = _normalize_mime_type(metadata.get("format"))
    return analyze_image_from_bytes(
        image_bytes,
        mime_type=mime_type,
        prompt=prompt,
        max_retries=max_retries,
        base_delay=base_delay,
    )


def test_gemini_analyzer() -> None:
    """Load a sample image and print the Gemini structured analysis JSON."""
    sample_path = input("Enter image path: ").strip() or "/Users/naman/Desktop/doodle.png"
    try:
        result = analyze_image(sample_path)
        output_dir = Path(__file__).resolve().parent
        output_path = output_dir / "gemini_analysis_output.json"
        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print("Gemini analysis:")
        print(json.dumps(result, indent=2))
        print(f"\nSaved output to: {output_path}")
    except FileNotFoundError:
        print(f"Image not found: {sample_path}")
    except Exception as exc:
        print(f"Error analyzing image: {exc}")


if __name__ == "__main__":
    test_gemini_analyzer()
