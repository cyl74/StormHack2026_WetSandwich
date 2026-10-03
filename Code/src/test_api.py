from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_client import client

response = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="Hello Gemini, test message.",
)
print(response.text)
