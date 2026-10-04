from pathlib import Path

from src.doodle_ai import test_pipeline


def test_main_accepts_explicit_image_path(monkeypatch):
    calls = []

    def fake_run_pipeline(path):
        calls.append(path)

    monkeypatch.setattr(test_pipeline, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(Path, "exists", lambda self: True)

    result = test_pipeline.main(["/Users/naman/Desktop/doodle.png"])

    assert result == 0
    assert calls == ["/Users/naman/Desktop/doodle.png"]
