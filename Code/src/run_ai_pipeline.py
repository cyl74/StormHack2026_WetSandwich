from __future__ import annotations

import argparse
import json

from src.doodle_ai.gemini_analyzer import analyze_image


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze a doodle image with Gemini and print JSON.")
    ap.add_argument("image_path", help="Path to the doodle image")
    args = ap.parse_args()

    result = analyze_image(args.image_path)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
