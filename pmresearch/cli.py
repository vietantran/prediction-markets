"""Command-line entry points; run python -m pmresearch --help."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import warnings
from pathlib import Path

from .catalog import SPECS, build_catalog
from .insights import analyze
from .kalshi import DEMO_BASE_URL, Kalshi, KalshiSigner
from .pipeline import collect, discover
from .polymarket import Polymarket
from .storage import RunStore, read_jsonl, write_json
from .streams import (KALSHI_DEMO_WS, KALSHI_WS, POLYMARKET_RTDS, POLYMARKET_WS,
                      capture, kalshi_subscription, polymarket_subscription, rtds_subscription)
from .topics import TopicClassifier


def positive(value):
    n = int(value)
    if n <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return n


def parser():
    p = argparse.ArgumentParser(description="Read-only US election, politics and macro prediction-market research")
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", default="data", help="Parent directory for a new timestamped run")
    common.add_argument("--demo", action="store_true", help="Use Kalshi demo (Polymarket stays public production)")
    common.add_argument("--timeout", type=positive, default=30)
    common.add_argument("--max-pages", type=positive, default=3, help="Per-query endpoint pagination limit; caps are reported")
    d = sub.add_parser("discover", parents=[common], help="Find relevant events and normalize their markets")
    d.add_argument("--platform", choices=["both", "polymarket", "kalshi"], default="both")
    d.add_argument("--query", action="append", help="Repeatable Polymarket search and Kalshi series keyword filter")
    d.add_argument("--topic", action="append", help="Repeatable topic ID; see topics command")
    d.add_argument("--topics-config", help="Custom taxonomy JSON")
    d.add_argument("--max-series", type=positive, default=80)
    d.add_argument("--kalshi-series", action="append", help="Explicit series ticker; repeat for multiple series")
    d.add_argument("--include-closed", action="store_true")
    d.add_argument("--catalog-scan", action="store_true", help="Also scan Polymarket keyset events for new vocabulary")
    c = sub.add_parser("collect", parents=[common], help="Download books, history, trades and analytics for a saved universe")
    c.add_argument("--universe", required=True, help="Path to discover run's markets.jsonl")
    c.add_argument("--datasets", nargs="+", default=["books", "history", "trades"],
                   choices=["books", "history", "trades", "holders", "open_interest", "comments"])
    c.add_argument("--max-markets", type=positive, default=20)
    c.add_argument("--start", help="UTC ISO timestamp or epoch seconds (default 7 days ago)")
    c.add_argument("--end", help="UTC ISO timestamp or epoch seconds (default now)")
    c.add_argument("--period", type=int, choices=[1, 60, 1440], default=60, help="History sampling / candle minutes")
    a = sub.add_parser("analyze", parents=[common], help="Rank research leads and compare two normalized snapshots")
    a.add_argument("--current", required=True)
    a.add_argument("--previous")
    r = sub.add_parser("request", parents=[common], help="Save one documented GET response; preserves platform schema")
    r.add_argument("api", choices=["kalshi", "gamma", "clob", "data", "bridge", "relayer", "combos", "perps"])
    r.add_argument("path", help="API-relative path, e.g. /historical/cutoff")
    r.add_argument("--params", default="{}", help='JSON object, e.g. {"limit":100}')
    r.add_argument("--authenticated", action="store_true", help="Kalshi private GET using configured RSA credentials")
    s = sub.add_parser("stream", parents=[common], help="Capture finite raw WebSocket sessions")
    s.add_argument("platform", choices=["polymarket", "kalshi", "rtds"])
    s.add_argument("--id", action="append", help="Outcome token ID / Kalshi ticker, repeatable")
    s.add_argument("--seconds", type=positive, default=60)
    s.add_argument("--channel", action="append", help="Kalshi channel; default ticker, trade, orderbook_delta")
    s.add_argument("--event-id", help="Polymarket numeric event ID for RTDS comments")
    s.add_argument("--equity", help="RTDS equity symbol")
    s.add_argument("--crypto", help="RTDS comma-separated symbols, e.g. btcusdt,ethusdt")
    s.add_argument("--max-reconnects", type=int, default=5)
    cat = sub.add_parser("catalog", parents=[common], help="Refresh complete official OpenAPI operation inventory")
    cat.add_argument("--api", action="append", choices=list(SPECS))
    sub.add_parser("topics", help="Print topic IDs and search seeds")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "topics":
        print(json.dumps(TopicClassifier().config, indent=2))
        return 0
    # Do not put environment values / authentication material into manifests.
    settings = {k: v for k, v in vars(args).items() if k not in {"out", "params"}}
    store = RunStore(args.out, args.command, settings)
    poly = kalshi = None
    summary = {}
    fatal = False
    print(f"Saving run to {store.path}", flush=True)
    with warnings.catch_warnings(record=True) as notices:
        warnings.simplefilter("always")
        try:
            if args.command in {"discover", "collect", "request"}:
                poly = Polymarket(timeout=args.timeout, recorder=store.record)
                kalshi = Kalshi(**({"base_url": DEMO_BASE_URL} if args.demo else {}),
                                timeout=args.timeout, recorder=store.record,
                                authenticated=getattr(args, "authenticated", False))
            if args.command == "discover":
                classifier = TopicClassifier(args.topics_config)
                unknown = set(args.topic or []) - set(classifier.config["topics"])
                if unknown:
                    raise ValueError("Unknown topics: " + ", ".join(sorted(unknown)))
                rows, coverage = discover(poly, kalshi, classifier, store, platform=args.platform,
                    queries=args.query, topics=args.topic, max_pages=args.max_pages, max_series=args.max_series,
                    include_closed=args.include_closed, catalog_scan=args.catalog_scan, series_tickers=args.kalshi_series)
                write_json(store.path / "research_report.json", analyze(rows))
                summary = {"outcome_rows": len(rows), "coverage": coverage}
            elif args.command == "collect":
                rows = read_jsonl(args.universe)
                outputs = collect(poly, kalshi, store, rows, datasets=args.datasets, max_markets=args.max_markets,
                                  max_pages=args.max_pages, start=args.start, end=args.end, period=args.period)
                summary = {"contracts_collected": len(outputs)}
            elif args.command == "analyze":
                current = read_jsonl(args.current)
                previous = read_jsonl(args.previous) if args.previous else None
                report = analyze(current, previous)
                # Preserve source coverage: differing/partial discovery can mimic topic emergence.
                manifests = {}
                for name in ("current", "previous"):
                    source = getattr(args, name)
                    manifest_path = Path(source).parent / "manifest.json" if source else None
                    if manifest_path and manifest_path.exists():
                        manifests[name] = json.loads(manifest_path.read_text(encoding="utf-8"))
                report["source_manifests"] = manifests
                write_json(store.path / "research_report.json", report)
                summary = {"current_rows": len(current), "previous_rows": len(previous or [])}
            elif args.command == "request":
                params = json.loads(args.params)
                if not isinstance(params, dict):
                    raise ValueError("--params must be a JSON object")
                if args.api == "kalshi":
                    data = kalshi.get(args.path, params=params, authenticated=args.authenticated)
                else:
                    if args.authenticated:
                        raise ValueError("For Polymarket authenticated reads, inject the documented signer from Python")
                    data = getattr(poly, args.api).get(args.path, **params)
                write_json(store.path / "response.json", data)
            elif args.command == "catalog":
                data = build_catalog(args.api)
                write_json(store.path / "api_inventory.json", data)
                for error in data["errors"]:
                    store.error(error["api"], error["error"])
                summary = {"operations": len(data["operations"])}
            elif args.command == "stream":
                signer = None
                heartbeat = None
                if args.max_reconnects < 0:
                    raise ValueError("--max-reconnects must be nonnegative")
                if args.platform == "polymarket":
                    url, subscription = POLYMARKET_WS, polymarket_subscription(args.id)
                    heartbeat = 10
                elif args.platform == "kalshi":
                    url = KALSHI_DEMO_WS if args.demo else KALSHI_WS
                    subscription = kalshi_subscription(args.id, args.channel)
                    signer = KalshiSigner.from_env()
                else:
                    url, subscription = POLYMARKET_RTDS, rtds_subscription(event_id=args.event_id,
                                                                           equity=args.equity, crypto=args.crypto)
                    heartbeat = 5
                gaps = []
                def record(frame):
                    store.record(frame)
                    if frame.get("kind") == "gap":
                        gaps.append(frame)
                summary = asyncio.run(capture(url, subscription, record, seconds=args.seconds, signer=signer,
                                              heartbeat_seconds=heartbeat, max_reconnects=args.max_reconnects))
                if gaps:
                    store.warnings.append(f"{len(gaps)} connection/sequence gaps; do not reconstruct across gaps")
                if not summary.get("data_messages"):
                    store.warnings.append("No data messages captured; subscription success/market activity is unverified")
        except KeyboardInterrupt:
            store.error(args.command, "Interrupted; raw responses retained")
            fatal = True
        except Exception as exc:
            # The command boundary must also mark unexpected schema/programming
            # failures as failed rather than finalizing a misleading success.
            detail = f"{type(exc).__name__}: {exc}"
            store.error(args.command, detail)
            print(f"Error: {detail}", file=sys.stderr)
            fatal = True
        finally:
            store.warnings.extend(str(w.message) for w in notices)
            store.warnings = list(dict.fromkeys(store.warnings))
            manifest = store.finish(status="failed" if fatal else None, summary=summary)
            store.close()
            if poly:
                poly.close()
            if kalshi:
                kalshi.close()
    print(json.dumps({"directory": str(store.path), "status": manifest["status"],
                      "errors": len(store.errors), "warnings": len(store.warnings), **summary}, indent=2))
    return 2 if fatal or store.errors else 0
