"""Select the research cohort from the full classified Kalshi inventory."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path


def select(index: Path, scope_path: Path, output: Path, asof: str | None = None):
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    with index.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    cutoff = datetime.fromisoformat(asof.replace("Z", "+00:00")) if asof else None
    def available(row):
        return not cutoff or all(not row.get(key) or datetime.fromisoformat(row[key].replace("Z", "+00:00")) <= cutoff
                                 for key in ["created_time", "open_time"])
    cohort = [row for row in rows if available(row) and (row["channel"] in scope["include_channels"]
              or row["channel"] == "macro" and row["series_ticker"] in scope["macro_series"])]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(cohort)
    counts = {channel: sum(row["channel"] == channel for row in cohort)
              for channel in ["mention", "election", "policy", "macro"]}
    manifest = {"classified_inventory_markets": len(rows), "research_cohort_markets": len(cohort), "asof_utc": asof,
                "channel_counts": counts, "scope_sha256": hashlib.sha256(scope_path.read_bytes()).hexdigest(),
                "inventory_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
                "scope": scope, "macro_series_absent": sorted(set(scope["macro_series"]) -
                {row["series_ticker"] for row in cohort})}
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--scope", default=Path("config/midterm_analysis_scope.json"), type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--asof", help="Optional frozen cutoff; excludes markets not yet created/open")
    args = parser.parse_args()
    print(json.dumps(select(args.index, args.scope, args.out, args.asof), indent=2))


if __name__ == "__main__":
    main()
