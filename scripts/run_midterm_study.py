"""Reproduce the midterm research pipeline or rebuild its saved report offline."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def call(*args):
    subprocess.run([sys.executable, *map(str, args)], cwd=REPO, check=True)


def main():
    from scripts.kalshi_full_census import months_before

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("data/kalshi_full_20260908"))
    parser.add_argument("--run-root", type=Path, default=Path("data/midterm_research_reproduction"))
    parser.add_argument("--report-out", type=Path, default=Path("research/midterms_2026_09_08"))
    parser.add_argument("--asof", default="2026-09-08T15:07:00Z")
    parser.add_argument(
        "--collect-census",
        action="store_true",
        help="Download the complete category-union census if missing; can be large",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Rebuild only from the saved report input snapshot; no API calls",
    )
    parser.add_argument("--rps", type=float, default=1.5)
    args = parser.parse_args()
    if args.offline:
        call("scripts/build_midterm_report.py", "--out", args.report_out, "--offline")
        return
    source = args.source_root.resolve()
    run = args.run_root.resolve()
    run.mkdir(parents=True, exist_ok=True)
    frozen = datetime.fromisoformat(args.asof.replace("Z", "+00:00"))
    if frozen.tzinfo is None:
        parser.error("--asof must include a timezone")
    config = {
        "schema_version": 1,
        "asof_utc": args.asof,
        "asof_ts": int(frozen.timestamp()),
        "eligibility_start_utc": months_before(frozen, 3).isoformat().replace("+00:00", "Z"),
        "eligibility_start_ts": int(months_before(frozen, 3).timestamp()),
        "hourly_start_utc": months_before(frozen, 2).isoformat().replace("+00:00", "Z"),
        "hourly_start_ts": int(months_before(frozen, 2).timestamp()),
        "public_api_only": True,
        "pre_settlement_extension": "Separate final-two-calendar-month history for older eligible settlements",
        "day_convention": "UTC",
        "website_membership_exact": False,
    }
    source_config = source / "run_config.json"
    if source_config.exists():
        old = json.loads(source_config.read_text(encoding="utf-8"))
        if any(old.get(key) != config[key] for key in ["asof_ts", "hourly_start_ts", "eligibility_start_ts"]):
            parser.error(
                "Existing source cache uses a different frozen window; choose a separate source directory"
            )
        config = old
    elif args.collect_census:
        source.mkdir(parents=True, exist_ok=True)
        source_config.write_text(json.dumps(config, indent=2), encoding="utf-8")
    else:
        parser.error("No source cache; use --collect-census or supply an existing --source-root")
    census_path = source / "discovery_manifest.json"
    census_complete = (
        census_path.exists()
        and json.loads(census_path.read_text(encoding="utf-8")).get("status") == "complete"
    )
    if not census_complete:
        if not args.collect_census:
            parser.error("The source census is incomplete; use --collect-census to collect/resume it")
        call("scripts/kalshi_full_census.py", "--output", source, "--rate", args.rps)
    analysis = run / "kalshi_analysis"
    call(
        "-m", "pmresearch.issue_research", "--source-root", source, "--out", analysis, "--stage", "inventory"
    )
    cohort = run / "analysis_cohort.csv"
    call(
        "scripts/select_midterm_cohort.py",
        "--index",
        analysis / "market_index.csv",
        "--out",
        cohort,
        "--asof",
        args.asof,
    )
    with cohort.open(encoding="utf-8-sig", newline="") as handle:
        tickers = sorted({row["ticker"] for row in csv.DictReader(handle)})
    # Metadata/cache-presence updates do not change the collection identity.
    collection_inventory = run / "collection_inventory.csv"
    collection_inventory.write_text("ticker\n" + "\n".join(tickers) + "\n", encoding="utf-8")
    cohort_hash = hashlib.sha256(collection_inventory.read_bytes()).hexdigest()[:12]
    supplement = run / f"supplement_{cohort_hash}"
    call(
        "scripts/collect_midterm_research.py",
        "--source-root",
        source,
        "--inventory",
        collection_inventory,
        "--out",
        supplement,
        "--rps",
        args.rps,
    )
    call(
        "-m",
        "pmresearch.issue_research",
        "--source-root",
        source,
        "--supplement-root",
        supplement,
        "--out",
        analysis,
        "--stage",
        "analyze",
        "--market-list",
        cohort,
    )
    call(
        "scripts/export_midterm_history.py",
        "--source-root",
        source,
        "--supplement-root",
        supplement,
        "--analysis-root",
        analysis,
        "--out",
        run / "excel_history",
    )
    call(
        "-m",
        "pmresearch.equity_research",
        "--out",
        run / "equities",
        "--asof",
        args.asof,
        "--start",
        config["hourly_start_utc"][:10],
    )
    (run / "pipeline_manifest.json").write_text(
        json.dumps(
            {
                "asof": args.asof,
                "source_root": str(source),
                "supplement_root": str(supplement),
                "cohort_tickers": len(tickers),
                "report_out": str(args.report_out),
                "scope": "Common-window research cohort. Full-category/pre-settlement extraction is separate.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    call("scripts/build_midterm_report.py", "--run-root", run, "--out", args.report_out)


if __name__ == "__main__":
    main()
