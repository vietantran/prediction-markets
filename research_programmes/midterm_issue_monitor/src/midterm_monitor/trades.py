"""Public execution tape: participation and taker direction, never trader positions."""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import requests

from .data import ROOT, timestamp, write_json, sha

API = "https://api.elections.kalshi.com/trade-api/v2"
TRADE_START = "2026-08-11T00:00:00Z"
TRADE_END = "2026-09-08T00:00:00Z"


def request_json(session, endpoint, params):
    for attempt in range(7):
        time.sleep(.8)
        try:
            response = session.get(API + endpoint, params=params, timeout=45)
            if response.status_code == 429 or response.status_code >= 500:
                delay = max(float(response.headers.get("Retry-After", 0)), min(2 ** (attempt + 1), 45))
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == 6:
                raise
            time.sleep(min(2 ** (attempt + 1), 45))
    raise RuntimeError("Retry budget exhausted")


def normalize(trade, expected_ticker, start, end):
    ts = timestamp(trade.get("created_time"))
    if ts is None or not start <= ts < end:
        return None
    if trade.get("ticker") != expected_ticker:
        raise ValueError("Wrong ticker in execution tape")
    qty = float(trade.get("count_fp", trade.get("count")))
    yes = float(trade["yes_price_dollars"]) if "yes_price_dollars" in trade else float(trade["yes_price"]) / 100
    no = float(trade["no_price_dollars"]) if "no_price_dollars" in trade else float(trade["no_price"]) / 100
    if not np.isfinite(qty) or not 0 <= yes <= 1 or not 0 <= no <= 1 or abs(yes + no - 1) > .00011 or qty <= 0:
        raise ValueError("Invalid execution units")
    canonical = trade.get("taker_outcome_side")
    side = canonical or trade.get("taker_side")
    if side not in {"yes", "no"}:
        side = "unknown"
    block = trade.get("is_block_trade")
    return {"trade_id": trade["trade_id"], "ticker": expected_ticker,
            "timestamp": ts, "created_time": trade["created_time"],
            "date": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
            "quantity": qty, "yes_price": yes, "no_price": no,
            "taker_outcome": side, "direction_source": "canonical" if canonical else "legacy",
            "block_status": "block" if block is True else "nonblock" if block is False else "unknown",
            "taker_premium_usd": qty * (yes if side == "yes" else no) if side != "unknown" else None,
            "gross_face_traded_usd": qty, "face_usd": 1.0}


def collect(inputs: Path = ROOT / "inputs", cache: Path = ROOT / "cache/trades"):
    """Checkpoint every immutable requested page; follow all cursors for 13 contracts."""
    cache.mkdir(parents=True, exist_ok=True)
    scenarios = json.loads((inputs / "scenarios.json").read_text(encoding="utf-8"))
    session = requests.Session()
    session.headers["User-Agent"] = "public-midterm-research/1.0"
    start, end = timestamp(TRADE_START), timestamp(TRADE_END)
    cutoff_path = cache / "cutoff.json"
    if not cutoff_path.exists():
        write_json(cutoff_path, request_json(session, "/historical/cutoff", {}))
    cutoff = json.loads(cutoff_path.read_text())
    split = timestamp(cutoff["trades_created_ts"])
    parts = []
    if start < min(split, end):
        parts.append(("/historical/trades", start, min(split, end)))
    if max(start, split) < end:
        parts.append(("/markets/trades", max(start, split), end))
    data, provenance, summaries = [], [], []
    for spec in scenarios:
        ticker = spec["ticker"]
        unique = {}
        pages = 0
        for endpoint, lo, hi in parts:
            cursor = ""
            seen = set()
            while True:
                # API describes strict after/before filters; overlap one second,
                # then enforce the exact half-open interval client-side.
                params = {"ticker": ticker, "min_ts": lo - 1, "max_ts": hi + 1, "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                signature = hashlib.sha256(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
                path = cache / f"{ticker}_{signature[:20]}.json.gz"
                if path.exists():
                    doc = json.loads(gzip.decompress(path.read_bytes()))
                    if doc["signature"] != signature:
                        raise ValueError("Cache identity mismatch")
                else:
                    result = request_json(session, endpoint, params)
                    if not isinstance(result.get("trades"), list):
                        raise ValueError("Malformed trade page")
                    doc = {"signature": signature, "endpoint": endpoint, "params": params,
                           "retrieved_at": datetime.now(timezone.utc).isoformat(), "response": result}
                    path.write_bytes(gzip.compress(json.dumps(doc, sort_keys=True).encode(), mtime=0))
                result = doc["response"]
                pages += 1
                provenance.append({"ticker": ticker, "endpoint": endpoint, "page": pages,
                                   "signature": signature, "sha256": sha(path), "retrieved_at": doc["retrieved_at"],
                                   "raw_rows": len(result["trades"]), "cache_file": path.name})
                for raw in result["trades"]:
                    row = normalize(raw, ticker, start, end)
                    if row:
                        old = unique.get(row["trade_id"])
                        if old is not None and old != row:
                            raise ValueError("Conflicting duplicate execution")
                        unique[row["trade_id"]] = row
                next_cursor = result.get("cursor", "")
                if not next_cursor:
                    break
                if next_cursor in seen:
                    raise ValueError("Repeated pagination cursor")
                seen.add(next_cursor)
                cursor = next_cursor
                if pages % 25 == 0:
                    print(f"{ticker}: {pages} pages, {len(unique):,} unique trades", flush=True)
        data.extend(unique.values())
        summaries.append({"ticker": ticker, "pages": pages, "trades": len(unique), "cursor_exhausted": True})
        print(f"complete {ticker}: {len(unique):,} trades / {pages} pages", flush=True)
    frame = pd.DataFrame(data).sort_values(["ticker", "timestamp", "trade_id"])
    temporary = inputs / "public_trades.csv.gz.partial"
    frame.to_csv(temporary, index=False, compression={"method": "gzip", "mtime": 0})
    temporary.replace(inputs / "public_trades.csv.gz")
    temporary_pages = inputs / "trade_source_pages.csv.partial"
    pd.DataFrame(provenance).to_csv(temporary_pages, index=False)
    temporary_pages.replace(inputs / "trade_source_pages.csv")
    manifest = {"status": "complete", "window_start_inclusive": TRADE_START, "window_end_exclusive": TRADE_END,
                "contracts": len(scenarios), "unique_trades": len(frame), "pages": len(provenance),
                "partitions": parts, "cutoff_response": cutoff, "coverage": summaries,
                "files": {p.name: sha(p) for p in [inputs / "public_trades.csv.gz", inputs / "trade_source_pages.csv"]},
                "limitations": ["Only specified 13-contract 28-day tape, not exchange-wide executions",
                                "No trader identity or individual positions; large prints can be split/aggregated",
                                "Taker direction describes execution initiation, not net market positioning",
                                "Source revisable after frozen event time; retrieved timestamp retained per page"]}
    write_json(inputs / "trade_manifest.json.partial", manifest)
    (inputs / "trade_manifest.json.partial").replace(inputs / "trade_manifest.json")
    return manifest


def tape_summary(frame):
    total = frame.quantity.sum()
    normal = frame.loc[frame.block_status == "nonblock"]
    directed = normal.loc[normal.taker_outcome.isin(["yes", "no"])]
    directed_qty = directed.quantity.sum()
    yes = directed.loc[directed.taker_outcome == "yes", "quantity"].sum()
    return {"executions": len(frame), "contracts_traded": total,
            "block_volume_share": frame.loc[frame.block_status == "block", "quantity"].sum() / total if total else np.nan,
            "unknown_block_volume_share": frame.loc[frame.block_status == "unknown", "quantity"].sum() / total if total else np.nan,
            "top_five_print_volume_share": frame.quantity.nlargest(5).sum() / total if total else np.nan,
            "largest_print_contracts": frame.quantity.max() if len(frame) else np.nan,
            "median_print_contracts": frame.quantity.median() if len(frame) else np.nan,
            "nonblock_directional_contracts": directed_qty,
            "nonblock_yes_taker_share": yes / directed_qty if directed_qty else np.nan,
            "nonblock_taker_imbalance": (2 * yes - directed_qty) / directed_qty if directed_qty else np.nan,
            "nonblock_unknown_direction_contracts": normal.loc[normal.taker_outcome == "unknown", "quantity"].sum()}


def run(inputs: Path, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((inputs / "trade_manifest.json").read_text())
    if manifest["status"] != "complete":
        raise ValueError("Incomplete execution collection")
    if manifest["window_start_inclusive"] != TRADE_START or manifest["window_end_exclusive"] != TRADE_END:
        raise ValueError("Wrong execution window")
    for name, digest in manifest["files"].items():
        if sha(inputs / name) != digest:
            raise ValueError("Execution input hash mismatch")
    trades = pd.read_csv(inputs / "public_trades.csv.gz")
    specs = json.loads((inputs / "scenarios.json").read_text())
    if {s["ticker"] for s in specs} != {c["ticker"] for c in manifest["coverage"]}:
        raise ValueError("Execution scenario inventory mismatch")
    daily, summary = [], []
    candles = pd.read_csv(inputs / "market_daily.csv")
    for spec in specs:
        frame = trades.loc[trades.ticker == spec["ticker"]]
        for period, lo, hi in [("previous14", "2026-08-11", "2026-08-25"), ("latest14", "2026-08-25", "2026-09-08"),
                               ("all28", "2026-08-11", "2026-09-08")]:
            subset = frame.loc[(frame.date >= lo) & (frame.date < hi)]
            summary.append({"scenario_id": spec["id"], "label": spec["label"], "ticker": spec["ticker"],
                            "period": period, **tape_summary(subset)})
        for day in pd.date_range("2026-08-11", "2026-09-07"):
            label = day.date().isoformat()
            subset = frame.loc[frame.date == label]
            bar = candles.loc[(candles.ticker == spec["ticker"]) & (candles.utc_date == label)]
            measured = float(bar.volume.iloc[0]) if len(bar) and pd.notna(bar.volume.iloc[0]) else np.nan
            hours = int(bar.volume_hours.iloc[0]) if len(bar) else 0
            partial = int(bar.partial_boundary_hours.iloc[0]) if len(bar) else 0
            total = subset.quantity.sum()
            comparable = hours == 24 and partial == 0 and np.isfinite(measured)
            daily.append({"scenario_id": spec["id"], "ticker": spec["ticker"], "date": label,
                          **tape_summary(subset), "candle_volume": measured, "candle_volume_hours": hours,
                          "full_day_comparable": comparable,
                          "volume_difference": total - measured if comparable else np.nan})
    pd.DataFrame(summary).to_csv(out / "trade_summary.csv", index=False)
    days = pd.DataFrame(daily)
    days.to_csv(out / "trade_daily.csv", index=False)
    large = trades.sort_values("quantity", ascending=False).groupby("ticker").head(10)
    large.to_csv(out / "largest_executions.csv", index=False)
    validation = {"status": "complete", "contracts": len(specs), "trades": len(trades),
                  "comparable_days": int(days.full_day_comparable.sum()),
                  "volume_mismatch_days": int((days.volume_difference.abs() > .001).sum()),
                  "max_volume_difference": float(days.volume_difference.abs().max()),
                  "block_flag_counts": trades.block_status.value_counts().to_dict(),
                  "direction_field_counts": trades.direction_source.value_counts().to_dict(),
                  "interpretation": "Observed execution flow; no investor identity, net positions, or inferred smart money"}
    write_json(out / "trade_analysis_manifest.json", validation)
    return validation
