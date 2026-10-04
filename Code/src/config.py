import os
from pathlib import Path

from dotenv import load_dotenv

for env_path in [
    Path(__file__).resolve().parents[1] / ".env",
    Path(__file__).resolve().parents[2] / ".env",
]:
    if env_path.exists():
        load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
