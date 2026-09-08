"""Create auditable daily and equity-session panels from frozen public candles.

No imports from the earlier research code. Raw sources stay in the source cache;
the compact panels and per-artifact hashes are the portable research inputs.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
ASOF = "2026-09-08T15:07:00Z"
START = "2026-07-08T15:07:00Z"


def timestamp(value):
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def number(value):
    try:
        n = float(value)
        return n if pd.notna(n) and abs(n) != float("inf") else None
    except (ValueError, TypeError):
        return None


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def normalized_bar(row, face=1.0, source_tier=None):
    """USD and legacy-cent quotes share a probability scale; nulls stay null."""
    def quote(side):
        obj = row.get(side) or {}
        if obj.get("close_dollars") is not None:
            return number(obj["close_dollars"])
        value = number(obj.get("close"))
        if value is None:
            return None
        return value if row.get("_price_unit") == "dollars" or row.get("_source_tier", source_tier) == "historical" else value / 100
    bid, ask = quote("yes_bid"), quote("yes_ask")
    valid = bool(face and bid is not None and ask is not None and 0 <= bid <= ask <= face)
    return {
        "ts": int(row["end_period_ts"]),
        "bid": bid / face if valid else None,
        "ask": ask / face if valid else None,
        "mid": (bid + ask) / (2 * face) if valid else None,
        "spread": (ask - bid) / face if valid else None,
        "volume": number(row.get("volume_fp", row.get("volume"))),
        "oi": number(row.get("open_interest_fp", row.get("open_interest"))),
        "partial": bool(row.get("_partial_start") or row.get("_partial_end") or row.get("_boundary_only")),
    }


def daily_rows(ticker, bars):
    grouped = defaultdict(list)
    for b in bars:
        grouped[datetime.fromtimestamp(b["ts"] - 1, UTC).date().isoformat()].append(b)
    out = []
    for day, rows in sorted(grouped.items()):
        rows.sort(key=lambda b: b["ts"])
        oi = next((b for b in reversed(rows) if b["oi"] is not None), None)
        q = next((b for b in reversed(rows) if b["mid"] is not None), None)
        vols = [b["volume"] for b in rows if b["volume"] is not None]
        out.append({"ticker": ticker, "utc_date": day, "volume": sum(vols) if vols else None,
                    "oi": oi["oi"] if oi else None, "oi_ts": oi["ts"] if oi else None,
                    **{k: q[k] if q else None for k in ["mid", "bid", "ask", "spread"]},
                    "hours": len(rows), "volume_hours": len(vols),
                    "usable_hours": sum(b["spread"] is not None and b["spread"] <= .10 for b in rows),
                    "first_ts": rows[0]["ts"], "last_ts": rows[-1]["ts"],
                    "partial_boundary_hours": sum(b["partial"] for b in rows)})
    return out


def session_quotes(ticker, bars, dates):
    stamps = [b["ts"] for b in bars]
    result = []
    for day in dates:
        close = datetime.fromisoformat(day + "T16:00:00").replace(tzinfo=ZoneInfo("America/New_York"))
        close_ts = int(close.timestamp())
        idx = bisect_right(stamps, close_ts) - 1
        bar = bars[idx] if idx >= 0 else None
        age = (close_ts - bar["ts"]) / 3600 if bar else None
        status = "missing" if not bar or bar["mid"] is None else "stale" if age > 3 else "valid"
        result.append({"ticker": ticker, "date": day, "session_close_utc": close.astimezone(UTC).isoformat(),
                       "p": bar["mid"] if bar else None,
                       **{k: bar[k] if bar else None for k in ["bid", "ask", "spread", "oi"]},
                       "quote_age_hours": age, "quote_status": status,
                       "bar_timestamp": bar["ts"] if bar else None,
                       "age_limitation": "Bar age; unchanged underlying quote age unavailable"})
    return result


def prepare(repository: Path, inputs: Path = ROOT / "inputs"):
    source = repository / "data/kalshi_full_20260908"
    supplement = repository / "data/midterm_research_20260908/kalshi_supplement"
    prior = repository / "research/midterms_2026_09_08/inputs"
    analysis = repository / "data/midterm_research_20260908/kalshi_analysis"
    inputs.mkdir(parents=True, exist_ok=True)
    run_config = json.loads((source / "run_config.json").read_text(encoding="utf-8"))
    if run_config["asof_utc"] != ASOF or run_config["hourly_start_utc"] != START:
        raise ValueError("Frozen programme requires the reviewed September8/July8 source window")
    copies = {"equity_prices.csv": prior / "equities/equity_prices.csv",
              "company_fundamentals.csv": prior / "equities/company_fundamentals.csv",
              "public_evidence.json": prior / "public_evidence.json",
              "issue_dictionary.json": prior / "issue_dictionary.json",
              "election_snapshot.csv": prior / "kalshi/election_snapshot.csv",
              "policy_snapshot.csv": prior / "kalshi/policy_snapshot.csv",
              "macro_snapshot.csv": prior / "kalshi/macro_snapshot.csv",
              "mention_cohorts.csv": prior / "kalshi/mention_cohorts.csv",
              "mention_observations.csv": prior / "kalshi/mention_observations.csv",
              "prior_equity_manifest.json": prior / "equities/collection_manifest.json"}
    for name, path in copies.items():
        shutil.copy2(path, inputs / name)
    for src, dst in [(analysis / "analysis_market_index.csv", "market_index.csv"),
                     (analysis / "market_index.csv", "broad_market_index.csv")]:
        frame = pd.read_csv(src, low_memory=False)
        for field in ["created_time", "open_time", "close_time", "settlement_ts"]:
            key = {"created_time": "created_epoch", "open_time": "open_epoch", "close_time": "close_epoch", "settlement_ts": "settlement_epoch"}[field]
            frame[key] = frame[field].map(timestamp)
        # Retain definitions in the core cohort; broad issuance needs no repeated full rule text.
        frame = frame.drop(columns=[c for c in ["source_metadata", "classification_text"] + (["rules_primary"] if dst.startswith("broad") else []) if c in frame])
        frame.to_csv(inputs / dst, index=False)
    index = pd.read_csv(inputs / "market_index.csv", low_memory=False)
    scenarios = json.loads((ROOT / "config/scenarios.json").read_text(encoding="utf-8"))
    write_json(inputs / "scenarios.json", scenarios)
    target_tickers = {s["ticker"] for s in scenarios}
    prices = pd.read_csv(inputs / "equity_prices.csv")
    dates = sorted(prices.loc[prices.ticker == "SPY", "date"].unique())
    start, end = timestamp(START), timestamp(ASOF)
    all_daily, all_quotes, provenance, errors = [], [], [], []
    hourly_count = 0
    expected = pd.read_csv(analysis / "coverage.csv").set_index("ticker")
    for n, m in enumerate(index.to_dict("records"), 1):
        ticker = m["ticker"]
        candidates = [supplement / "candles" / (ticker + ".json.gz"), source / "candles" / (ticker + ".json.gz")]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            raise ValueError("Missing frozen source " + ticker)
        payload = path.read_bytes()
        doc = json.loads(gzip.decompress(payload))
        if doc.get("status") != "complete" or doc.get("errors") or doc.get("asof_ts") != end or doc.get("window_label") != "common":
            raise ValueError("Invalid common source " + ticker)
        if doc.get("market_ticker") != ticker or doc.get("requested_start_ts") != start or doc.get("requested_end_ts") != end:
            raise ValueError("Wrong identity or requested bounds " + ticker)
        face = number(m.get("face_usd")) or 1.0
        opened = timestamp(m.get("open_time"))
        settled = timestamp(m.get("settlement_ts"))
        bars_by_time = {}
        for row in doc.get("hourly", []):
            ts = int(row["end_period_ts"])
            if (start <= ts <= end and row.get("_period_interval", 60) == 60
                    and (not opened or ts > opened)
                    and (not settled or ts < settled)):
                if ts in bars_by_time:
                    raise ValueError("Duplicate hour " + ticker)
                bars_by_time[ts] = normalized_bar(row, face, doc.get("source_tier"))
        bars = [bars_by_time[t] for t in sorted(bars_by_time)]
        if len(bars) != int(expected.loc[ticker, "hourly_rows"]):
            errors.append({"ticker": ticker, "observed": len(bars), "prior": int(expected.loc[ticker, "hourly_rows"])})
        hourly_count += len(bars)
        all_daily.extend(daily_rows(ticker, bars))
        if ticker in target_tickers:
            all_quotes.extend(session_quotes(ticker, bars, dates))
        provenance.append({"ticker": ticker, "source": str(path.relative_to(repository)),
                           "sha256": hashlib.sha256(payload).hexdigest(), "hourly_rows": len(bars),
                           "asof": ASOF, "dataset": "common_only"})
        if n % 1000 == 0:
            print(f"prepared {n}/{len(index)} markets; {hourly_count:,} hourly observations", flush=True)
    if errors:
        write_json(inputs / "preparation_errors.json", errors)
        raise ValueError(f"{len(errors)} per-market coverage mismatches")
    (inputs / "preparation_errors.json").unlink(missing_ok=True)
    pd.DataFrame(all_daily).to_csv(inputs / "market_daily.csv", index=False)
    pd.DataFrame(all_quotes).to_csv(inputs / "market_session_quotes.csv", index=False)
    pd.DataFrame(provenance).to_csv(inputs / "source_artifacts.csv", index=False)
    manifest = {"status": "complete", "asof": ASOF, "common_start": START,
                "markets": len(index), "broad_metadata_markets": len(pd.read_csv(inputs / "broad_market_index.csv", usecols=["ticker"])),
                "hourly_rows": hourly_count, "market_daily_rows": len(all_daily),
                "session_quote_rows": len(all_quotes), "scenario_contracts": len(target_tickers),
                "equity_dates": len(dates), "presettlement_included": False, "errors": errors,
                "limitations": ["Imported audited category-derived cohort, not exact historical website membership", "Metadata retrieved after cutoff; only created/opened by cutoff were selected", "Current vendor-adjusted equity history, not a contemporaneous vintage", "Sparse API bars preserve missingness; reported full intervals are not invented trades", "Daily observations at settlement or creation can be partial; attention module applies its own coverage gates"],
                "files": {p.name: sha(p) for p in sorted(inputs.glob("*")) if p.suffix in {".csv", ".json"} and p.name != "preparation_manifest.json"}}
    write_json(inputs / "preparation_manifest.json", manifest)
    print(json.dumps({k:v for k,v in manifest.items() if k not in {"files", "limitations"}}, indent=2), flush=True)
    return manifest
