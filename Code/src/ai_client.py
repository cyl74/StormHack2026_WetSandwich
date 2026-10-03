from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from google import genai
from config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)
