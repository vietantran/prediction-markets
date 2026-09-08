"""Collect a frozen, auditable research subset without changing the full census.

Run issue_research's inventory stage first. The subset uses the same public
hourly/daily collector as the full extraction and preserves its original market
metadata. Existing complete source artifacts are reused, not downloaded again.
"""
from __future__ import annotations

import argparse
import calendar
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def read_json(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
    else:
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def artifact_complete(path, config, label, start, end):
    if not path.exists():
        return False
    try:
        data = read_json(path)
    except (OSError, ValueError, EOFError):
        return False
    return (data.get("schema_version") == 1 and data.get("status") == "complete"
            and data.get("asof_ts") == config["asof_ts"] and data.get("window_label") == label
            and data.get("requested_start_ts") == start and data.get("requested_end_ts") == end
            and not data.get("errors"))


def prepare_subset(source: Path, inventory: Path, out: Path, presettlement=False) -> dict:
    config = read_json(source / "run_config.json")
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    inventory_hash = hashlib.sha256(inventory.read_bytes()).hexdigest()
    prior_config = out / "run_config.json"
    if prior_config.exists() and read_json(prior_config) != config:
        raise ValueError("Frozen run configuration mismatch; use a separate supplement directory")
    prior_manifest = out / "discovery_manifest.json"
    if prior_manifest.exists():
        previous = read_json(prior_manifest)
        if previous.get("inventory_sha256") != inventory_hash:
            raise ValueError("Inventory hash mismatch; use a new supplement for a changed cohort")
        if previous.get("presettlement_enabled", False) != presettlement:
            raise ValueError("Presettlement scope mismatch; use a separate supplement directory")
        # Preparation is immutable. The collector resumes exactly these files.
        return previous
    with inventory.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    requested = {row.get("ticker") or row.get("market_ticker") for row in rows}
    requested.discard(None)
    selected, reused, missing, artifacts = set(), [], [], []
    for path in sorted((source / "discovery").glob("*.json.gz")):
        data = read_json(path)
        if data.get("status") != "complete":
            continue
        subset = []
        for market in data.get("markets", []):
            ticker = market["ticker"]
            if ticker not in requested:
                continue
            selected.add(ticker)
            candidate = source / "candles" / f"{ticker}.json.gz"
            complete = artifact_complete(candidate, config, "common", config["hourly_start_ts"], config["asof_ts"])
            settled = market.get("settlement_ts")
            if isinstance(settled, str):
                settled = int(datetime.fromisoformat(settled.replace("Z", "+00:00")).timestamp())
            if presettlement and settled is not None and config["eligibility_start_ts"] <= settled < config["hourly_start_ts"]:
                dt = datetime.fromtimestamp(settled, timezone.utc)
                month_number = dt.year * 12 + dt.month - 3
                year, month = divmod(month_number, 12)
                month += 1
                start = int(dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1])).timestamp())
                extension = source / "candles_presettlement" / f"{ticker}.json.gz"
                complete = complete and artifact_complete(extension, config, "presettlement", start, settled)
            if complete:
                reused.append(ticker)
            else:
                subset.append(market)
                missing.append(ticker)
        if subset:
            payload = dict(data)
            payload["markets"] = subset
            payload["research_subset"] = True
            payload["parent_discovery_path"] = str(path)
            target = out / "discovery" / path.name
            write_json(target, payload)
            artifacts.append(target.name)
    if requested - selected:
        raise ValueError(f"{len(requested - selected)} inventory tickers absent from completed census")
    write_json(out / "run_config.json", config)
    cutoff = source / "historical_cutoff.json"
    if cutoff.exists():
        write_json(out / cutoff.name, read_json(cutoff))
    manifest = {
        "status": "complete", "prepared_at": datetime.now(timezone.utc).isoformat(),
        "asof_utc": config["asof_utc"], "source_root": str(source),
        "inventory_path": str(inventory),
        "inventory_sha256": inventory_hash, "config_sha256": config_hash,
        "presettlement_enabled": presettlement,
        "requested_markets": len(requested), "reused_complete_markets": len(reused),
        "markets_requiring_collection": len(missing), "discovery_files": artifacts,
        "reused_tickers": reused, "collection_tickers": missing,
        "scope": "Research subset; this is not a claim of complete category historical extraction.",
    }
    write_json(out / "discovery_manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rps", type=float, default=1.5)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--presettlement", action="store_true",
                        help="Also require the separate final-two-month histories for older eligible settlements")
    args = parser.parse_args()
    if args.source_root.resolve() == args.out.resolve():
        parser.error("The supplement must be separate from the source census")
    manifest = prepare_subset(args.source_root, args.inventory, args.out, args.presettlement)
    print(json.dumps({key: value for key, value in manifest.items()
                      if not isinstance(value, list)}, indent=2), flush=True)
    if not args.prepare_only and manifest["markets_requiring_collection"]:
        collector = Path(__file__).with_name("kalshi_full_candles.py")
        command = [sys.executable, str(collector), "--root", str(args.out),
                   "--workers", str(args.workers), "--rps", str(args.rps)]
        if args.presettlement:
            command.append("--presettlement")
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
