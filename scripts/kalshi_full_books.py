"""Public current depth snapshots for the full census; no historical-book claim.

Run with .venv/Scripts/python.exe scripts/kalshi_full_books.py --follow.
Each snapshot is explicitly timestamped at retrieval, not at the census cutoff.
"""
from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path
from urllib.parse import quote

from kalshi_full_census import Client, read_json, write_json, utcnow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/kalshi_full_20260908")
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--rate", type=float, default=0.5)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        parser.error("batch-size must be 1..100")
    out = Path(args.output)
    client = Client(args.rate)
    observed_files, targets, processed = set(), {}, set()
    errors = []
    while True:
        for path in (out / "discovery").glob("*.json.gz"):
            if path.name in observed_files:
                continue
            try:
                payload = read_json(path)
            except (OSError, ValueError):
                continue
            if payload.get("status") != "complete":
                continue
            observed_files.add(path.name)
            for market in payload.get("markets", []):
                ticker = market["ticker"]
                if str(market.get("status", "")).lower() in {"active", "open", "paused"}:
                    targets[ticker] = payload["series_ticker"]
        pending = []
        for ticker in targets:
            if ticker in processed:
                continue
            path = out / "books" / (quote(ticker, safe="-_.") + ".json.gz")
            if path.exists() and read_json(path).get("status") == "complete":
                processed.add(ticker)
            else:
                pending.append(ticker)
        for offset in range(0, len(pending), args.batch_size):
            tickers = pending[offset:offset + args.batch_size]
            endpoint = "/markets/orderbooks"
            params = {"tickers": tickers}
            started = utcnow()
            digest = hashlib.sha256("\n".join(tickers).encode()).hexdigest()[:24]
            try:
                response = client.get(endpoint, params)
                stamp = utcnow()
                envelope = {"request": {"endpoint": endpoint, "params": params},
                            "request_started_at": started, "retrieved_at": stamp,
                            "response": response}
                raw_path = out / "raw" / "books" / (digest + ".json.gz")
                write_json(raw_path, envelope)
                received = {}
                for book in response.get("orderbooks", []):
                    ticker = book.get("ticker")
                    if ticker in tickers:
                        received[ticker] = book
                for ticker in tickers:
                    path = out / "books" / (quote(ticker, safe="-_.") + ".json.gz")
                    status = "complete" if ticker in received else "missing_in_batch_response"
                    payload = {"schema_version": 1, "ticker": ticker,
                               "series_ticker": targets[ticker], "status": status,
                               "retrieved_at": stamp, "request_started_at": started,
                               "snapshot_time_basis": "retrieval_time_not_frozen_census_asof",
                               "raw_response_path": str(raw_path),
                               "orderbook": received.get(ticker),
                               "historical_depth_available": False}
                    write_json(path, payload)
                    if status == "complete":
                        processed.add(ticker)
                    else:
                        errors.append({"ticker": ticker, "error": status, "at": stamp})
                        processed.add(ticker)
            except Exception as exc:
                errors.append({"tickers": tickers, "error": str(exc), "at": utcnow()})
                processed.update(tickers)
            progress = {"status": "running", "updated_at": utcnow(),
                        "target_markets_seen": len(targets), "attempted_markets": len(processed),
                        "errors": errors, "requests": client.requests, "retries": client.retries,
                        "scope": "Current active/open/paused market depth; snapshot at retrieval"}
            write_json(out / "books_progress.json", progress)
            print({k: v for k, v in progress.items() if k != "errors"}, flush=True)
        census_path = out / "discovery_manifest.json"
        census_done = census_path.exists() and read_json(census_path).get("status") in {"complete", "incomplete_errors"}
        if census_done and any(path.name not in observed_files and
                               read_json(path).get("status") == "complete"
                               for path in (out / "discovery").glob("*.json.gz")):
            continue
        if not args.follow or census_done:
            status = "complete" if census_done and not errors else "partial"
            write_json(out / "books_manifest.json", {"status": status, "updated_at": utcnow(),
                       "target_markets_seen": len(targets), "attempted_markets": len(processed),
                       "errors": errors, "census_done": census_done,
                       "scope": "Current active/open/paused market depth at individual retrieval timestamps"})
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
