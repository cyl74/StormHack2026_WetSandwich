from __future__ import annotations

import base64
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
CODE_DIR = ROOT / "Code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

for env_path in [ROOT / ".env", CODE_DIR / ".env"]:
    if env_path.exists():
        load_dotenv(env_path)

from src.doodle_ai.gemini_analyzer import analyze_image_from_base64
from src.doodle_ai.trait_extractor import extract_traits

STATIC_DIR = ROOT / "doodleBoxWebsite"
HOST = "127.0.0.1"
PORT = 8000


def _json_response(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


class AIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=str(STATIC_DIR), **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            index_path = STATIC_DIR / "index.html"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            body = index_path.read_bytes()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path.startswith("/api/"):
            _json_response(self, 404, {"error": "Not found."})
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/analyze":
            _json_response(self, 404, {"error": "Endpoint not found."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception as exc:
            _json_response(self, 400, {"error": f"Invalid JSON: {exc}"})
            return

        image_value = payload.get("image")
        if not image_value:
            _json_response(self, 400, {"error": "Missing image data in JSON field 'image'."})
            return

        try:
            analysis = analyze_image_from_base64(image_value)
            traits = extract_traits(analysis)
            traits_payload = dict(traits)
            base = traits_payload.get("base")
            if base is not None and hasattr(base, "speed") and hasattr(base, "damage") and hasattr(base, "health"):
                traits_payload["base"] = {
                    "speed": getattr(base, "speed"),
                    "damage": getattr(base, "damage"),
                    "health": getattr(base, "health"),
                }
            _json_response(self, 200, {"status": "ok", "analysis": analysis, "traits": traits_payload})
        except Exception as exc:
            _json_response(self, 500, {"error": f"AI analysis failed: {exc}"})


if __name__ == "__main__":
    print(f"Serving Doodle Arena at http://{HOST}:{PORT}")
    print(f"AI endpoint: http://{HOST}:{PORT}/api/analyze")
    ThreadingHTTPServer((HOST, PORT), AIHandler).serve_forever()
