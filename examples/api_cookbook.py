"""Runnable examples of direct API extraction with full raw response recording.

Examples (after installing the repository):
  python examples/api_cookbook.py polymarket --event-slug which-party-will-win-the-senate-in-2026
  python examples/api_cookbook.py kalshi --series CONTROLS

The examples resolve identifiers from metadata rather than hardcoding tokens.
For more advanced calls see docs/polymarket.md and docs/kalshi.md.
"""
from __future__ import annotations

import argparse
import warnings

from pmresearch.kalshi import Kalshi
from pmresearch.normalize import normalize_kalshi, normalize_polymarket
from pmresearch.polymarket import Polymarket
from pmresearch.storage import RunStore, write_csv, write_json, write_jsonl


def polymarket_example(api, slug):
    event = api.gamma.event_by_slug(slug)
    markets = event.get("markets") or []
    rows = [row for market in markets for row in normalize_polymarket(market, event)]
    selected = [row for row in rows if row.get("token_id")][:2]
    token_ids = [row["token_id"] for row in selected]
    result = {"event": event, "tags": api.gamma.event_tags(event["id"])}
    if token_ids:
        result["books"] = api.clob.books(token_ids)
        result["spreads"] = api.clob.spreads(token_ids)
        result["history"] = api.clob.batch_price_history(token_ids, interval="1w", fidelity=60)
    if selected and selected[0].get("condition_id"):
        condition_id = selected[0]["condition_id"]
        result["clob_market"] = api.clob.market(condition_id)
        result["trades"] = list(api.data.iter_trades(market=[condition_id], max_pages=2))
        result["holders"] = api.data.holders([condition_id])
        result["open_interest"] = api.data.open_interest([condition_id])
    result["comments"] = api.gamma.comments(parent_entity_type="Event", parent_entity_id=int(event["id"]), limit=100)
    return rows, result


def kalshi_example(api, series_ticker):
    series = api.get_series(series_ticker)
    events = list(api.iter_events(series_ticker=series_ticker, status="open", with_nested_markets=True, max_pages=2))
    rows = []
    for event in events:
        for market in event.get("markets") or []:
            rows.extend(normalize_kalshi(market, event))
    result = {"series": series, "events": events, "historical_cutoff": api.get_historical_cutoff(),
              "exchange_status": api.get_exchange_status()}
    if rows:
        ticker = rows[0]["market_id"]
        result["orderbook"] = api.get_orderbook(ticker)
        result["trades"] = list(api.iter_trades(ticker=ticker, max_pages=2))
        result["event_metadata"] = api.get_event_metadata(rows[0]["event_id"])
    return rows, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=["polymarket", "kalshi"])
    parser.add_argument("--event-slug")
    parser.add_argument("--series")
    parser.add_argument("--out", default="data/cookbook")
    args = parser.parse_args()
    if args.platform == "polymarket" and not args.event_slug:
        parser.error("Polymarket requires --event-slug")
    if args.platform == "kalshi" and not args.series:
        parser.error("Kalshi requires --series")
    store = RunStore(args.out, "cookbook", vars(args))
    failed = False
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        try:
            cls = Polymarket if args.platform == "polymarket" else Kalshi
            with cls(recorder=store.record) as api:
                rows, result = (polymarket_example(api, args.event_slug) if args.platform == "polymarket"
                                else kalshi_example(api, args.series))
            write_json(store.path / "response.json", result)
            write_jsonl(store.path / "markets.jsonl", rows)
            write_csv(store.path / "markets.csv", rows)
        except Exception as exc:
            store.error(args.platform, exc)
            failed = True
            raise
        finally:
            store.warnings.extend(str(notice.message) for notice in notices)
            store.finish(status="failed" if failed else None)
            store.close()
            print(store.path)


if __name__ == "__main__":
    main()
