"""Attach exact source-page observation times without new API requests."""
import argparse
from pathlib import Path
import time
from urllib.parse import quote

from kalshi_full_census import read_json, write_json, utcnow, parse_ts


def eligibility_class(market, config):
    settled = parse_ts(market.get("settlement_ts"))
    status = str(market.get("status", "")).lower()
    if settled is not None and config["eligibility_start_ts"] <= settled <= config["asof_ts"]:
        return "recently_settled"
    if settled is None and status in {"finalized", "settled"}:
        return "settlement_date_uncertain"
    if settled is not None:
        return "unresolved_candidate"
    observed = parse_ts(market.get("_retrieved_at"))
    opened, closed = parse_ts(market.get("open_time")), parse_ts(market.get("close_time"))
    if status in {"active", "open"} and observed is not None and (opened is None or opened <= observed) and (closed is None or closed > observed):
        return "confirmed_active"
    return "unresolved_candidate"


def enrich(path, output, config):
    payload = read_json(path)
    if payload.get("status") not in {"complete", "error"}:
        return None
    ticker = payload["series_ticker"]
    file_ticker = quote(ticker, safe="-_ .").replace(" ", "_")
    wanted = {(m.get("_source_tier"), m["ticker"]) for m in payload.get("markets", []) if not m.get("_retrieved_at")}
    times = {}
    if wanted:
        for tier in ("live", "historical"):
            for raw_path in sorted((output / "raw" / "discovery" / file_ticker / tier).glob("*.json.gz")):
                envelope = read_json(raw_path)
                for market in envelope.get("response", {}).get("markets") or []:
                    key = (tier, market["ticker"])
                    if key in wanted:
                        times[key] = envelope.get("retrieved_at")
    missing = 0
    for array in ("current", "historical", "markets"):
        for market in payload.get(array, []):
            key = (market.get("_source_tier"), market["ticker"])
            stamp = times.get(key) or market.get("_retrieved_at")
            if stamp:
                market["_retrieved_at"] = stamp
            elif array == "markets":
                missing += 1
            market["_eligibility_class"] = eligibility_class(market, config)
    payload["snapshot_timestamp_method"] = "Matching raw-page retrieval UTC by selected source tier and ticker; last matching page for deduplicated ticker"
    payload["snapshot_timestamps_enriched_at"] = utcnow()
    payload["snapshot_enrichment_version"] = 2
    payload["snapshot_timestamp_missing_markets"] = missing
    payload["eligibility_class_note"] = "confirmed_active uses status and open/close times at actual snapshot observation; frozen asof governs settled-window eligibility. Other unresolved listings are candidates, not confirmed trading-active."
    write_json(path, payload)
    return {"series_ticker": ticker, "market_count": len(payload.get("markets", [])), "missing_timestamps": missing}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/kalshi_full_20260908")
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    config = read_json(output / "run_config.json")
    handled = {}
    while True:
        for path in (output / "discovery").glob("*.json.gz"):
            before = path.stat().st_mtime_ns
            if handled.get(str(path)) == before:
                continue
            payload = read_json(path)
            if payload.get("snapshot_enrichment_version", 0) >= 2:
                handled[str(path)] = before
                continue
            result = enrich(path, output, config)
            if result is not None:
                handled[str(path)] = path.stat().st_mtime_ns
        final = output / "discovery_manifest.json"
        terminal = final.exists() and read_json(final).get("status") in {"complete", "incomplete_errors"}
        state = {"updated_at": utcnow(), "status": "complete" if terminal or not args.follow else "following", "discovery_files_processed": len(handled), "method": "offline exact raw-page retrieval timestamps; no API calls"}
        write_json(output / "snapshot_enrichment_progress.json", state)
        print(state, flush=True)
        if terminal or not args.follow:
            break
        time.sleep(15)


if __name__ == "__main__":
    main()
