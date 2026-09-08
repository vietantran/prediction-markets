"""Check the portable report's actual charts, primary sources and data payload."""
import json
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from midterm_monitor.reporting import build


ROOT = Path(__file__).resolve().parents[1]


def test_report_is_self_contained_and_complete():
    doc = BeautifulSoup((ROOT / "report/index.html").read_text(encoding="utf-8"), "html.parser")
    assert len(doc.select("figure img")) == 10
    assert all(image["src"].startswith("data:image/png;base64,") and image.get("alt") for image in doc.select("figure img"))
    assert not doc.select("script[src],link[rel=stylesheet]")
    assert not [a for a in doc.select("a") if not a.get("href")]
    assert not [a for a in doc.select("a") if a["href"].startswith("#") and not doc.find(id=a["href"][1:])]
    assert "{{" not in doc.get_text()
    models = json.loads(doc.select_one("#research-data").string)["models"]
    assert len(models) == 230
    assert len(doc.select("details a[href^='https://']")) == 29


def test_no_windows_default_encoding_corruption():
    report = (ROOT / "report/index.html").read_text(encoding="utf-8")
    assert "Â·" not in report
    assert "â€”" not in report
    assert "−9.2%" in report


def test_changed_inputs_cannot_publish_stale_conclusions(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "example.csv").write_text("changed-data", encoding="utf-8")
    lock = {"content_sha256": {"example.csv": "not-the-content-hash"}}
    (tmp_path / "config/report_input_lock.json").write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(ValueError, match="Reviewed report input changed"):
        build(tmp_path)
