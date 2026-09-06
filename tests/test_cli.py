import json
from types import SimpleNamespace

from pmresearch.cli import main


def test_unexpected_schema_error_cannot_finalize_as_success(tmp_path, monkeypatch):
    def bad_get(*args, **kwargs):
        raise TypeError("unexpected response shape")
    fake = SimpleNamespace(gamma=SimpleNamespace(get=bad_get), close=lambda: None)
    monkeypatch.setattr("pmresearch.cli.Polymarket", lambda **kwargs: fake)
    code = main(["request", "gamma", "/tags", "--out", str(tmp_path)])
    manifest = json.loads(next(tmp_path.glob("*/manifest.json")).read_text(encoding="utf-8"))
    assert code == 2
    assert manifest["status"] == "failed"
    assert "TypeError" in manifest["errors"][0]["error"]
