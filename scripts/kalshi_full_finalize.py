"""Finish the authorized finite collection, audit coverage, and write final Excel.

This is a one-off local pipeline stage, not a recurring scheduler. It waits for
the current collectors, resumes missing/error work, then exports actual data.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

from kalshi_full_candles import atomic_json, candle_price_annotation


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(8):
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(3.0, 0.1 * (2 ** attempt)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/kalshi_full_20260908"))
    parser.add_argument("--export-python", type=Path, default=Path(
        "C:/Users/Viet An/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"))
    args = parser.parse_args()
    root = args.root.resolve()
    workspace = Path(__file__).resolve().parents[1]
    root.mkdir(parents=True, exist_ok=True)
    progress_path = root / "finalization_progress.json"
    state = {"started_at": now(), "status": "running", "stage": "waiting_for_census",
             "finite_pipeline": True}

    def progress(stage, **extra):
        state.update(stage=stage, updated_at=now(), **extra)
        write(progress_path, state)
        print(json.dumps(state), flush=True)

    def await_terminal(filename, statuses):
        while True:
            path = root / filename
            if path.exists():
                doc = read(path)
                if doc.get("status") in statuses:
                    return doc
            time.sleep(30)

    def run(script, argv, python=sys.executable):
        log = root / (Path(script).stem + "_finalize.log")
        with log.open("a", encoding="utf-8") as f:
            result = subprocess.run([str(python), "-u", str(workspace / "scripts" / script), *argv],
                                    cwd=workspace, stdout=f, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode

    try:
        progress("waiting_for_census")
        census = await_terminal("discovery_manifest.json", {"complete", "incomplete_errors"})
        if census.get("status") != "complete":
            progress("retrying_incomplete_census")
            run("kalshi_full_census.py", ["--output", str(root), "--rate", "1", "--workers", "4"])
            census = read(root / "discovery_manifest.json")
        progress("waiting_for_candles")
        await_terminal("candle_manifest.json", {"complete", "complete_with_errors"})
        progress("reconciling_candle_coverage")
        # A fresh finite scan catches late discovery files and retries partial
        # artifacts; completed windows resume from local data without refetching.
        run("kalshi_full_candles.py", ["--root", str(root), "--presettlement", "--rps", "2", "--workers", "4"])
        progress("waiting_for_current_books")
        await_terminal("books_manifest.json", {"complete", "partial"})
        progress("reconciling_current_books")
        run("kalshi_full_books.py", ["--output", str(root), "--rate", "0.5"])
        progress("auditing_saved_artifacts")
        config = read(root / "run_config.json")
        markets, errors = {}, []
        series_files = list((root / "discovery").glob("*.json.gz"))
        for path in series_files:
            doc = read(path)
            if doc.get("status") != "complete" or doc.get("errors"):
                errors.append({"artifact": str(path), "status": doc.get("status"), "errors": doc.get("errors")})
            for market in doc.get("markets", []):
                markets[market["ticker"]] = market
        expected_extension = set()
        for ticker, market in markets.items():
            value = market.get("settlement_ts")
            if value:
                settled = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
                if config["eligibility_start_ts"] <= settled < config["hourly_start_ts"]:
                    expected_extension.add(ticker)
        totals = {"hourly": 0, "daily_api_sessions": 0,
                  "hourly_presettlement": 0, "daily_api_sessions_presettlement": 0}
        missing, partial = [], []
        annotation_repairs = 0
        for directory, required in (("candles", set(markets)), ("candles_presettlement", expected_extension)):
            for ticker in required:
                path = root / directory / (ticker + ".json.gz")
                if not path.exists():
                    missing.append({"market_ticker": ticker, "dataset": directory})
                    continue
                doc = read(path)
                changed = 0
                for row in doc.get("hourly", []) + doc.get("daily", []):
                    unit, basis = candle_price_annotation(row, row.get("_source_tier", doc.get("source_tier", "live")))
                    if row.get("_price_unit") != unit:
                        row["_original_price_unit_annotation"] = row.get("_price_unit")
                        row["_price_unit"], row["_price_unit_basis"] = unit, basis
                        changed += 1
                if changed:
                    doc["annotation_repaired_at"] = now()
                    doc["annotation_repair_note"] = "Synthetic empty-trade-price unit annotations corrected; original API field names and values unchanged."
                    atomic_json(path, doc)
                    annotation_repairs += changed
                if doc.get("status") != "complete" or doc.get("errors"):
                    partial.append({"market_ticker": ticker, "dataset": directory,
                                    "status": doc.get("status"), "errors": doc.get("errors")})
                suffix = "_presettlement" if directory.endswith("presettlement") else ""
                totals["hourly" + suffix] += len(doc.get("hourly", []))
                totals["daily_api_sessions" + suffix] += len(doc.get("daily", []))
        census = read(root / "discovery_manifest.json")
        census_ok = census.get("status") == "complete" and not errors
        candles_ok = not missing and not partial
        errors.extend(missing)
        errors.extend(partial)
        book_manifest = read(root / "books_manifest.json")
        books_ok = book_manifest.get("status") == "complete" and not book_manifest.get("errors")
        complete = census_ok and candles_ok and books_ok
        manifest = {"schema_version": 1, "status": "complete" if complete else "partial",
                    "complete": complete, "completed_at": now(), "asof_utc": config["asof_utc"],
                    "census_complete": census_ok, "candles_complete": candles_ok,
                    "books_complete": books_ok, "category_membership_complete": False,
                    "website_census_claim": False, "errors": errors,
                    "books_errors": book_manifest.get("errors", []),
                    "unique_eligible_markets": len(markets), "discovery_series_files": len(series_files),
                    "eligibility_class_counts": dict(Counter(m.get("_eligibility_class", "unclassified") for m in markets.values())),
                    "expected_presettlement_markets": len(expected_extension), "rows": totals,
                    "synthetic_price_unit_annotations_repaired": annotation_repairs,
                    "scope": "All eligible contracts returned by documented category-derived series queries; website equivalence remains approximate.",
                    "large_positions": "Unavailable for other participants in public API",
                    "tick_trades": "Not included; price/volume/OI candles and face notionals collected",
                    "daily_convention": "Raw API sessions plus separately derived UTC days",
                    "snapshot_time_basis": "Market counters and order books at retrieval; eligibility and candle windows use frozen asof"}
        write(root / "collection_manifest.json", manifest)
        progress("exporting_excel", audited_market_count=len(markets), audit_complete=complete)
        code = run("kalshi_full_export.py", ["--root", str(root), "--out", str(root / "excel" / "final")],
                   python=args.export_python)
        progress("finished" if code == 0 else "export_failed", status="complete" if code == 0 else "error",
                 collection_complete=complete, export_exit_code=code,
                 final_index=str(root / "excel" / "final" / "INDEX.xlsx"))
    except Exception as exc:
        progress("failed", status="error", error=f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    main()
