#gemini_analyzer.py
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from google import genai
from google.genai import types
from google.genai.errors import ServerError

from src.config import GEMINI_API_KEY
from src.doodle_ai.image_loader import load_image

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


def analyze_image(
    image_path: str,
    prompt: str = DEFAULT_ANALYSIS_PROMPT,
    max_retries: int = 4,
    base_delay: float = 2.0,
) -> Dict[str, Any]:
    """Send an image to Gemini for structured analysis and return parsed JSON.

    Retries transient 503/UNAVAILABLE responses with exponential backoff so the
    pipeline is resilient to temporary model overloads.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to the .env file.")

    image_bytes, metadata = load_image(image_path)
    mime_type = metadata["format"]
    if mime_type == "jpg":
        mime_type = "image/jpeg"
    elif mime_type == "jpeg":
        mime_type = "image/jpeg"
    elif mime_type == "png":
        mime_type = "image/png"
    else:
        mime_type = "image/png"

    client = genai.Client(api_key=GEMINI_API_KEY)
    delay = base_delay

    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            text = response.text.strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Gemini did not return valid JSON: {text}") from exc
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
    except Exception as exc:  # pragma: no cover - helpful CLI reporting
        print(f"Error analyzing image: {exc}")


if __name__ == "__main__":
    test_gemini_analyzer()
