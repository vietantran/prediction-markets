"""Recovery must never promote a failed collector or partial export to completion."""
from __future__ import annotations

import gzip
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def finalizer(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("kalshi_full_finalize")


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle)
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def collection(tmp_path):
    save(tmp_path / "run_config.json", {
        "asof_utc": "2026-09-08T15:07:00Z", "asof_ts": 1788880020,
        "eligibility_start_ts": 1780931220, "hourly_start_ts": 1783523220,
        "public_api_only": True,
    })
    for filename in ("discovery_manifest.json", "candle_manifest.json", "books_manifest.json"):
        save(tmp_path / filename, {"status": "complete", "errors": []})
    save(tmp_path / "discovery" / "S.json.gz", {
        "status": "complete", "markets": [
            {"ticker": "S-A"},
            {"ticker": "S-B", "settlement_ts": "2026-06-20T12:00:00Z"},
        ],
    })
    for folder, ticker in (("candles", "S-A"), ("candles", "S-B"),
                           ("candles_presettlement", "S-B")):
        save(tmp_path / folder / f"{ticker}.json.gz", {
            "status": "complete", "errors": [], "hourly": [], "daily": [],
        })
    return tmp_path


def execute(finalizer, monkeypatch, root, *, failed_script=None, export_state="complete"):
    calls = []
    monkeypatch.setattr(finalizer.sys, "argv", ["finalize", "--root", str(root)])

    def subprocess_run(command, **kwargs):
        script = Path(command[2]).name
        calls.append(script)
        if script == "kalshi_full_export.py" and script != failed_script:
            save(root / "excel" / "final" / "export_manifest.json", {
                "status": ("API EXTRACT COMPLETE; CATEGORY SCOPE APPROXIMATE"
                           if export_state == "complete" else "PARTIAL EXTRACT — SEE COVERAGE"),
                "asof_utc": "2026-09-08T15:07:00Z", "selected_datasets": "all",
                "inputs": {"datasets_complete": export_state == "complete"}, "errors": [],
            })
            (root / "excel" / "final" / "INDEX.xlsx").write_bytes(b"fixture")
        return SimpleNamespace(returncode=9 if script == failed_script else 0)

    monkeypatch.setattr(finalizer.subprocess, "run", subprocess_run)
    return calls


@pytest.mark.parametrize("script", [
    "kalshi_full_census.py", "kalshi_full_candles.py", "kalshi_full_books.py",
])
def test_failed_collector_never_starts_export(finalizer, monkeypatch, collection, script):
    if script == "kalshi_full_census.py":
        save(collection / "discovery_manifest.json", {"status": "incomplete_errors"})
    calls = execute(finalizer, monkeypatch, collection, failed_script=script)
    with pytest.raises(RuntimeError, match="exited with code 9"):
        finalizer.main()
    assert "kalshi_full_export.py" not in calls
    assert json.loads((collection / "finalization_progress.json").read_text())["status"] == "error"


@pytest.mark.parametrize("failure", ["common", "presettlement", "partial", "books"])
def test_incomplete_audit_writes_manifest_and_withholds_export(
        finalizer, monkeypatch, collection, failure):
    if failure in {"common", "presettlement"}:
        folder = "candles" if failure == "common" else "candles_presettlement"
        (collection / folder / "S-B.json.gz").unlink()
    elif failure == "partial":
        save(collection / "candles" / "S-A.json.gz", {"status": "partial", "errors": ["failed"]})
    else:
        save(collection / "books_manifest.json", {"status": "partial", "errors": ["failed"]})
    calls = execute(finalizer, monkeypatch, collection)
    with pytest.raises(RuntimeError, match="Collection audit incomplete"):
        finalizer.main()
    assert "kalshi_full_export.py" not in calls
    assert json.loads((collection / "collection_manifest.json").read_text())["complete"] is False
    progress = json.loads((collection / "finalization_progress.json").read_text())
    assert progress["status"] == "error" and progress["collection_complete"] is False


def test_export_exit_zero_cannot_mask_partial_export(finalizer, monkeypatch, collection):
    execute(finalizer, monkeypatch, collection, export_state="partial")
    with pytest.raises(RuntimeError, match="Excel export is incomplete"):
        finalizer.main()
    assert json.loads((collection / "finalization_progress.json").read_text())["status"] == "error"


def test_complete_collection_and_export_can_finish(finalizer, monkeypatch, collection):
    calls = execute(finalizer, monkeypatch, collection)
    config_before = (collection / "run_config.json").read_bytes()
    finalizer.main()
    assert calls[-1] == "kalshi_full_export.py"
    assert (collection / "run_config.json").read_bytes() == config_before
    progress = json.loads((collection / "finalization_progress.json").read_text())
    assert progress["status"] == "complete" and progress["collection_complete"] is True


def test_failed_export_remains_error(finalizer, monkeypatch, collection):
    execute(finalizer, monkeypatch, collection, failed_script="kalshi_full_export.py")
    with pytest.raises(RuntimeError, match="exited with code 9"):
        finalizer.main()
    assert json.loads((collection / "finalization_progress.json").read_text())["status"] == "error"
