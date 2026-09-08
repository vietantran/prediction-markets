"""Resume-safe, public-only hourly/daily Kalshi census candle collector.

Consumes completed discovery/{series}.json.gz artifacts and frozen run_config.json.
The approved older-settlement extension is physically separate from common history.
No missing prices/periods are filled and no private account endpoints are called.
"""
from __future__ import annotations

import argparse
import calendar
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import Any
from urllib.parse import quote

import httpx


BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stamp(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def subtract_months(value: int, months: int) -> int:
    dt = datetime.fromtimestamp(value, timezone.utc)
    total = dt.year * 12 + dt.month - 1 - months
    year, zero_month = divmod(total, 12)
    month = zero_month + 1
    return int(dt.replace(year=year, month=month,
                          day=min(dt.day, calendar.monthrange(year, month)[1])).timestamp())


def read_json(path: Path) -> Any:
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    if path.name.endswith(".gz"):
        with gzip.open(temp, "wt", encoding="utf-8", compresslevel=5) as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    else:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows sync/indexing and concurrent readers can briefly deny replacement.
    # Retry only the local commit; retain the downloaded data in the temp file.
    for attempt in range(8):
        try:
            os.replace(temp, path)
            break
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(3.0, 0.1 * (2 ** attempt)))


def safe_ticker(ticker: str) -> str:
    # API ticker spelling is retained in payload; no path traversal is possible.
    if not ticker or not re.fullmatch(r"[A-Za-z0-9_.-]+", ticker) or ticker in {".", ".."}:
        raise ValueError(f"Unsupported ticker filename: {ticker!r}")
    return ticker


class RateLimiter:
    """One aggregate request budget shared by every worker in this process."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next_at = 0.0

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            reserved = max(now, self.next_at)
            self.next_at = reserved + self.interval
        delay = reserved - now
        if delay > 0:
            time.sleep(delay)

    def slow_down(self) -> None:
        with self.lock:
            self.interval = min(5.0, self.interval / 0.8)
            self.next_at = max(self.next_at, time.monotonic() + 3)


class PublicAPI:
    def __init__(self, rps: float, retries: int):
        self.limiter = RateLimiter(rps)
        self.retries = retries
        self.client = httpx.Client(base_url=BASE_URL, timeout=httpx.Timeout(70, connect=20),
                                   headers={"User-Agent": "pmresearch-public-census/1.0"},
                                   limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))
        self.lock = threading.Lock()
        self.request_count = 0
        self.retry_count = 0

    def close(self) -> None:
        self.client.close()

    def get(self, path: str, params: dict[str, Any]) -> tuple[dict | None, list[dict]]:
        attempts: list[dict] = []
        for attempt in range(self.retries + 1):
            self.limiter.acquire()
            record = {"endpoint": path, "params": params, "attempt": attempt + 1,
                      "requested_at": utc_now()}
            retry_delay = min(60.0, 1.5 * (2 ** attempt)) + random.uniform(0, 0.5)
            try:
                response = self.client.get(path, params=params)
                record.update(http_status=response.status_code, retrieved_at=utc_now())
                with self.lock:
                    self.request_count += 1
                if response.status_code == 200:
                    data = response.json()
                    key = "markets" if path == "/markets/candlesticks" else "candlesticks"
                    if not isinstance(data, dict) or not isinstance(data.get(key), list):
                        raise ValueError(f"Expected object with {key} list")
                    record["rows_returned"] = (sum(len(m.get("candlesticks", [])) for m in data[key])
                                               if key == "markets" else len(data[key]))
                    attempts.append(record)
                    return data, attempts
                record["error"] = response.text[:600]
                attempts.append(record)
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    return None, attempts
                if response.status_code == 429:
                    self.limiter.slow_down()
                if response.headers.get("Retry-After"):
                    try:
                        retry_delay = min(120, max(retry_delay, float(response.headers["Retry-After"])))
                    except ValueError:
                        pass
            except (httpx.HTTPError, ValueError) as exc:
                record.update(retrieved_at=utc_now(), error=f"{type(exc).__name__}: {exc}")
                attempts.append(record)
            if attempt < self.retries:
                with self.lock:
                    self.retry_count += 1
                time.sleep(retry_delay)
        return None, attempts


def route_path(series: str, ticker: str, tier: str) -> str:
    ticker_path = quote(ticker, safe="")
    if tier == "historical":
        return f"/historical/markets/{ticker_path}/candlesticks"
    return f"/series/{quote(series, safe='')}/markets/{ticker_path}/candlesticks"


def preferred_tier(market: dict, cutoff: int | None) -> str:
    if market.get("_source_tier") in {"historical", "live"}:
        return market["_source_tier"]
    settled = stamp(market.get("settlement_ts"))
    return "historical" if settled and cutoff and settled < cutoff else "live"


def fetch_chunk(api: PublicAPI, series: str, ticker: str, tier: str,
                start: int, end: int, period: int) -> tuple[list[dict], str, list[dict], list[dict]]:
    params = {"start_ts": start, "end_ts": end, "period_interval": period}
    payload, attempts = api.get(route_path(series, ticker, tier), params)
    if payload and payload["candlesticks"]:
        return payload["candlesticks"], tier, attempts, []
    if payload and payload.get("_verified_batch_market"):
        return [], tier, attempts, []
    # A market may have migrated since discovery; also verify empty tier responses.
    last_status = attempts[-1].get("http_status") if attempts else None
    if payload is None and last_status not in {404, 410}:
        return [], tier, attempts, [{"reason": "request_failed", "request": attempts[-1]}]
    alternate = "live" if tier == "historical" else "historical"
    other, other_attempts = api.get(route_path(series, ticker, alternate), params)
    attempts.extend(other_attempts)
    if other and other["candlesticks"]:
        return other["candlesticks"], alternate, attempts, []
    if payload is not None:
        # Valid preferred response with no activity; a missing alternate is expected.
        return [], tier, attempts, []
    if other is not None:
        return [], alternate, attempts, []
    return [], tier, attempts, [{"reason": "both_tiers_unavailable", "request": attempts[-1]}]


def candle_price_annotation(raw: dict, tier: str) -> tuple[str, str]:
    """Describe trade-price fields; explicit quote suffixes retain their own units."""
    if tier == "historical":
        # Historical plain bid/ask and trade-price fields are all dollar strings,
        # including records whose trade-price object is empty.
        return "dollars", "historical_candle_schema"
    price = raw.get("price") or {}
    if not price:
        return "unavailable", "no_trade_price_fields"
    if any(key.endswith("_dollars") for key in price):
        return "dollars", "explicit_trade_price_dollars_fields"
    if any(key in price for key in ("open", "high", "low", "close", "mean", "previous")):
        return "cents", "legacy_live_trade_price_fields"
    return "unavailable", "unrecognized_trade_price_fields"


def fetch_window(api: PublicAPI, series: str, market: dict, tier: str, label: str,
                 requested_start: int, requested_end: int, config: dict,
                 period: int, bars_per_chunk: int) -> tuple[list[dict], dict]:
    birth_times = [stamp(market.get(key)) for key in ("created_time", "open_time")]
    birth_times = [x for x in birth_times if x is not None]
    start = max([requested_start] + birth_times)
    settled = stamp(market.get("settlement_ts"))
    end = min(requested_end, settled) if settled else requested_end
    step = period * 60
    coverage: dict[str, Any] = {
        "label": label, "period_interval": period,
        "requested_start_ts": requested_start, "requested_end_ts": requested_end,
        "effective_start_ts": start, "effective_end_ts": end,
        "status": "pending", "requests": [], "errors": [], "rows": 0,
        "asof_ts": config["asof_ts"], "missing_period_policy": "preserve_sparse_no_fill",
        "price_policy": "preserve_null_and_raw_values",
        "daily_boundary_policy": "API-defined interval; not assumed UTC calendar days",
    }
    if end <= start:
        coverage.update(status="no_overlap", no_overlap_reason=(
            "settled_before_common_window" if settled and settled < requested_start
            else "not_open_in_window"))
        return [], coverage
    # For settled markets include the final interval straddling settlement. Its
    # endpoint may follow settlement although trading inside it was earlier.
    # For live markets never request a candle ending after frozen asof.
    query_end = min(config["asof_ts"], end + step) if settled and settled <= end else end
    coverage["api_query_end_ts"] = query_end
    rows_by_ts: dict[int, dict] = {}
    cursor = start
    while cursor <= query_end:
        chunk_end = min(query_end, cursor + bars_per_chunk * step - 1)
        rows, actual_tier, attempts, errors = fetch_chunk(
            api, series, market["ticker"], tier, cursor, chunk_end, period)
        coverage["requests"].extend(attempts)
        coverage["errors"].extend(errors)
        # Requested intervals remain well below the documented 10k batch cap.
        if len(rows) > bars_per_chunk + 2:
            coverage["errors"].append({"reason": "unexpected_response_size", "rows": len(rows)})
        for raw in rows:
            if not isinstance(raw, dict) or not isinstance(raw.get("end_period_ts"), int):
                coverage["errors"].append({"reason": "malformed_candle", "row": raw})
                continue
            ts = raw["end_period_ts"]
            interval_start = ts - step
            if ts < start or ts > query_end or interval_start >= end:
                continue
            row = dict(raw)
            price_unit, price_unit_basis = candle_price_annotation(raw, actual_tier)
            row.update({
                "_window_label": label, "_source_tier": actual_tier,
                "_retrieved_at": attempts[-1]["retrieved_at"],
                "_period_interval": period, "_period_start_ts": interval_start,
                "_partial_start": interval_start < start,
                "_partial_end": ts > end,
                "_boundary_only": ts == start,
                "_window_overlap_start_ts": max(interval_start, start),
                "_window_overlap_end_ts": min(ts, end),
                "_price_unit": price_unit, "_price_unit_basis": price_unit_basis,
            })
            # Retain original full-period volume; boundary flags prohibit treating
            # it as exact in-window volume without a trade-level recalculation.
            row["_volume_is_full_api_interval"] = True
            if ts in rows_by_ts and any(rows_by_ts[ts].get(k) != raw.get(k) for k in raw):
                coverage.setdefault("conflicting_duplicate_timestamps", []).append(ts)
            rows_by_ts[ts] = row
        if rows:
            tier = actual_tier
        cursor = chunk_end + 1
    rows = [rows_by_ts[ts] for ts in sorted(rows_by_ts)]
    stamps = [row["end_period_ts"] for row in rows]
    coverage.update(
        status="error" if coverage["errors"] and not rows else "partial" if coverage["errors"] else "complete",
        rows=len(rows), first_end_period_ts=stamps[0] if stamps else None,
        last_end_period_ts=stamps[-1] if stamps else None,
        no_rows_returned=not rows, nominal_period_count=(end - start + step - 1) // step,
        missing_interior_periods=sum(max(0, (b - a) // step - 1) for a, b in zip(stamps, stamps[1:])),
        irregular_spacing_count=sum((b - a) % step != 0 for a, b in zip(stamps, stamps[1:])),
        leading_unobserved_seconds=max(0, stamps[0] - step - start) if stamps else end - start,
        trailing_unobserved_seconds=max(0, end - stamps[-1]) if stamps else end - start,
        partial_start_rows=sum(row["_partial_start"] for row in rows),
        partial_end_rows=sum(row["_partial_end"] for row in rows),
        api_tiers=sorted({row["_source_tier"] for row in rows}),
    )
    return rows, coverage


def completed_output(path: Path, config: dict, label: str) -> bool:
    if not path.exists():
        return False
    try:
        data = read_json(path)
        return (data.get("schema_version") == SCHEMA_VERSION and data.get("status") == "complete"
                and data.get("asof_ts") == config["asof_ts"] and data.get("window_label") == label)
    except (OSError, ValueError, EOFError):
        return False


def collect_dataset(api: PublicAPI, root: Path, series: str, market: dict, config: dict,
                    cutoff: int | None, label: str, requested_start: int, requested_end: int,
                    bars_per_chunk: int) -> dict:
    folder = root / ("candles" if label == "common" else "candles_presettlement")
    path = folder / f"{safe_ticker(market['ticker'])}.json.gz"
    if completed_output(path, config, label):
        return {"path": str(path), "market_ticker": market["ticker"], "label": label,
                "status": "resumed", "requests": 0}
    tier = preferred_tier(market, cutoff)
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "market_ticker": market["ticker"],
        "series_ticker": series, "event_ticker": market.get("event_ticker"),
        "source_tier": tier, "asof_utc": config["asof_utc"], "asof_ts": config["asof_ts"],
        "window_label": label, "requested_start_ts": requested_start,
        "requested_end_ts": requested_end, "notional_value_dollars": market.get("notional_value_dollars"),
        "created_time": market.get("created_time"), "open_time": market.get("open_time"),
        "close_time": market.get("close_time"), "settlement_ts": market.get("settlement_ts"),
        "market_status": market.get("status"), "eligibility_reason": market.get("_eligibility_reason"),
        "started_at": utc_now(), "status": "partial", "hourly": [], "daily": [], "windows": [],
        "errors": [], "public_api_only": True,
    }
    # Partial checkpoint resumes only fully successful periods for identical bounds.
    prior = None
    if path.exists():
        try:
            prior = read_json(path)
            if any(prior.get(key) != data.get(key) for key in (
                    "schema_version", "asof_ts", "requested_start_ts", "requested_end_ts", "window_label")):
                prior = None
        except (OSError, ValueError, EOFError):
            pass
    for period, name in ((60, "hourly"), (1440, "daily")):
        reusable = next((w for w in (prior or {}).get("windows", [])
                         if w.get("period_interval") == period and w.get("status") in {"complete", "no_overlap"}), None)
        if reusable:
            rows, coverage = prior[name], reusable
        else:
            rows, coverage = fetch_window(api, series, market, tier, label, requested_start,
                                           requested_end, config, period, bars_per_chunk)
        data[name] = rows
        data["windows"].append(coverage)
        data["errors"].extend(coverage["errors"])
        data["updated_at"] = utc_now()
        atomic_json(path, data)
    data["status"] = "complete" if not data["errors"] else (
        "partial" if data["hourly"] or data["daily"] else "error")
    data["completed_at"] = utc_now()
    atomic_json(path, data)
    return {"path": str(path), "market_ticker": market["ticker"], "label": label,
            "status": data["status"], "hourly_rows": len(data["hourly"]), "daily_rows": len(data["daily"]),
            "errors": data["errors"], "requests": sum(len(w["requests"]) for w in data["windows"])}


def collect_market(api: PublicAPI, root: Path, series: str, market: dict, config: dict,
                   cutoff: int | None, presettlement: bool, bars_per_chunk: int) -> list[dict]:
    results = [collect_dataset(api, root, series, market, config, cutoff, "common",
                               config["hourly_start_ts"], config["asof_ts"], bars_per_chunk)]
    settled = stamp(market.get("settlement_ts"))
    if (presettlement and settled is not None and config["eligibility_start_ts"] <= settled
            < config["hourly_start_ts"]):
        results.append(collect_dataset(api, root, series, market, config, cutoff, "presettlement",
                                        subtract_months(settled, 2), settled, bars_per_chunk))
    return results


def needs_presettlement(market: dict, config: dict) -> bool:
    settled = stamp(market.get("settlement_ts"))
    return (settled is not None and config["eligibility_start_ts"] <= settled
            < config["hourly_start_ts"])


def enqueue_discovered(items: list[tuple[str, dict]], selected: set[str],
                       seen_markets: set[str], expected_extensions: set[str],
                       pending: deque[tuple[str, dict]], config: dict,
                       presettlement: bool) -> None:
    for series, market in items:
        ticker = market["ticker"]
        if ticker not in seen_markets and (not selected or ticker in selected):
            seen_markets.add(ticker)
            if presettlement and needs_presettlement(market, config):
                expected_extensions.add(ticker)
            pending.append((series, market))


def common_bounds(market: dict, config: dict, period: int) -> tuple[int, int]:
    births = [stamp(market.get(k)) for k in ("created_time", "open_time")]
    start = max([config["hourly_start_ts"]] + [x for x in births if x is not None])
    settled = stamp(market.get("settlement_ts"))
    end = min(config["asof_ts"], settled) if settled else config["asof_ts"]
    query_end = min(config["asof_ts"], end + period * 60) if settled and end <= settled else end
    return start, query_end


class BatchCacheAPI:
    """Serve per-market collectors verified batch payloads without changing artifacts."""

    def __init__(self, api: PublicAPI, cache: dict):
        self.api = api
        self.cache = cache

    def get(self, path: str, params: dict) -> tuple[dict | None, list[dict]]:
        if path.startswith("/series/") and path.endswith("/candlesticks"):
            ticker = path.split("/")[-2]
            key = (ticker, params["period_interval"])
            if key in self.cache:
                entry = self.cache[key]
                if entry["start"] <= params["start_ts"] and entry["end"] >= params["end_ts"]:
                    rows = [r for r in entry["rows"] if params["start_ts"] <= r["end_period_ts"] <= params["end_ts"]]
                    return {"ticker": ticker, "candlesticks": rows, "_verified_batch_market": True}, list(entry["attempts"])
        return self.api.get(path, params)


def prefetch(api: PublicAPI, group: list[tuple[str, dict]], config: dict, period: int,
             cache: dict) -> None:
    if not group:
        return
    bounds = [common_bounds(m, config, period) for _, m in group]
    start = min(a for a, _ in bounds)
    end = max(b for _, b in bounds)
    worst_rows = ((end - start) // (period * 60) + 3) * len(group)
    if len(group) > 100 or worst_rows > 9900:
        middle = max(1, len(group) // 2)
        if len(group) == 1:
            return  # The individual collector splits exceptionally long intervals.
        prefetch(api, group[:middle], config, period, cache)
        prefetch(api, group[middle:], config, period, cache)
        return
    params = {"market_tickers": ",".join(m["ticker"] for _, m in group),
              "start_ts": start, "end_ts": end, "period_interval": period}
    payload, attempts = api.get("/markets/candlesticks", params)
    if payload is None:
        return  # Recover through individual endpoints with explicit error metadata.
    if sum(len(item.get("candlesticks", [])) for item in payload["markets"]) >= 10000:
        return  # Never trust a response that reaches the service truncation cap.
    wanted = {m["ticker"] for _, m in group}
    for item in payload["markets"]:
        ticker = item.get("market_ticker") or item.get("ticker")
        if ticker in wanted and isinstance(item.get("candlesticks"), list):
            cache[(ticker, period)] = {"rows": item["candlesticks"], "attempts": attempts,
                                      "start": start, "end": end}


def collect_group(api: PublicAPI, root: Path, group: list[tuple[str, dict]], config: dict,
                  cutoff: int | None, presettlement: bool, bars_per_chunk: int) -> list[dict]:
    """Batch 100 daily series, then bound each hourly subgroup by interval and count."""
    eligible_live = []
    individual = []
    results = []
    for series, market in group:
        path = root / "candles" / f"{safe_ticker(market['ticker'])}.json.gz"
        settled = stamp(market.get("settlement_ts"))
        has_overlap = settled is None or settled > config["hourly_start_ts"]
        start, stop = common_bounds(market, config, 60)
        if (preferred_tier(market, cutoff) == "live" and has_overlap
                and start < stop
                and not completed_output(path, config, "common")):
            eligible_live.append((series, market))
        else:
            individual.append((series, market))
    daily_cache: dict = {}
    prefetch(api, eligible_live, config, 1440, daily_cache)
    while eligible_live:
        subgroup = [eligible_live.pop(0)]
        while eligible_live and len(subgroup) < 100:
            candidate = subgroup + [eligible_live[0]]
            bounds = [common_bounds(m, config, 60) for _, m in candidate]
            worst_rows = ((max(b for _, b in bounds) - min(a for a, _ in bounds)) // 3600 + 3) * len(candidate)
            if worst_rows > 9900:
                break
            subgroup.append(eligible_live.pop(0))
        cache = dict(daily_cache)
        prefetch(api, subgroup, config, 60, cache)
        proxy = BatchCacheAPI(api, cache)
        for series, market in subgroup:
            try:
                results.extend(collect_market(proxy, root, series, market, config, cutoff,
                                              presettlement, bars_per_chunk))
            except Exception as exc:
                results.append({"market_ticker": market["ticker"], "status": "error",
                                "error": f"{type(exc).__name__}: {exc}"})
    for series, market in individual:
        try:
            results.extend(collect_market(api, root, series, market, config, cutoff,
                                          presettlement, bars_per_chunk))
        except Exception as exc:
            results.append({"market_ticker": market["ticker"], "status": "error",
                            "error": f"{type(exc).__name__}: {exc}"})
    return results


def discover_complete(root: Path, seen_files: set[str], errors: list[dict]) -> list[tuple[str, dict]]:
    markets = []
    for path in sorted((root / "discovery").glob("*.json.gz")):
        if path.name in seen_files:
            continue
        try:
            data = read_json(path)
            if data.get("status") != "complete":
                continue
            series = data["series_ticker"]
            for market in data["markets"]:
                markets.append((series, market))
            seen_files.add(path.name)
        except (OSError, ValueError, KeyError, EOFError) as exc:
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    return markets


def census_finished(root: Path) -> bool:
    for name in ("discovery_manifest.json", "discovery_progress.json", "census_manifest.json"):
        path = root / name
        if path.exists():
            try:
                if read_json(path).get("status") in {"complete", "completed", "complete_with_errors", "incomplete_errors"}:
                    return True
            except (OSError, ValueError):
                pass
    return (root / "DISCOVERY_COMPLETE").exists()


def load_cutoff(root: Path) -> int | None:
    for path in (root / "historical_cutoff.json", root / "raw" / "historical_cutoff.json",
                 Path("research/kalshi_2026_09_08/historical_cutoff.json")):
        if path.exists():
            data = read_json(path)
            return stamp(data.get("market_settled_ts"))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/kalshi_full_20260908"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--rps", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=7)
    parser.add_argument("--bars-per-chunk", type=int, default=4000)
    parser.add_argument("--presettlement", action="store_true",
                        help="Also collect approved separate final-2-month histories for early settlements")
    parser.add_argument("--follow", action="store_true", help="Watch completed discoveries until census completion")
    parser.add_argument("--poll-seconds", type=float, default=15)
    parser.add_argument("--tickers", nargs="*", help="Explicit smoke-test filter; omitted means every eligible market")
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or not 0 < args.rps <= 4 or not 1 <= args.bars_per_chunk <= 4000:
        parser.error("workers 1..4, rps >0..4, and bars-per-chunk 1..4000 required")
    config = read_json(args.root / "run_config.json")
    for key in ("asof_utc", "asof_ts", "hourly_start_ts", "eligibility_start_ts"):
        if key not in config:
            parser.error(f"Missing run config key {key}")
    if config.get("public_api_only") is not True:
        parser.error("run_config must explicitly declare public_api_only:true")
    selected = set(args.tickers or [])
    manifest_path = args.root / ("candle_smoke_manifest.json" if selected else "candle_manifest.json")
    log_path = args.root / ("candle_smoke_results.jsonl" if selected else "candle_results.jsonl")
    seen_files: set[str] = set()
    seen_markets: set[str] = set()
    expected_extensions: set[str] = set()
    discovery_errors: list[dict] = []
    pending: deque[tuple[str, dict]] = deque()
    counts = {"complete": 0, "resumed": 0, "partial": 0, "error": 0,
              "hourly_rows_new": 0, "daily_rows_new": 0}
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "started_at": utc_now(), "pid": os.getpid(),
        "status": "running", "asof_utc": config["asof_utc"], "asof_ts": config["asof_ts"],
        "workers": args.workers, "rps": args.rps, "presettlement_enabled": args.presettlement,
        "scope_tickers": sorted(selected) if selected else None, "public_api_only": True,
        "discovery_errors": discovery_errors,
    }
    api = PublicAPI(args.rps, args.retries)
    cutoff = load_cutoff(args.root)
    last_progress = 0.0
    last_discovery_scan = 0.0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures: dict[Any, list[str]] = {}
            while True:
                new_markets = []
                if time.monotonic() - last_discovery_scan >= 15 or not (pending or futures):
                    new_markets = discover_complete(args.root, seen_files, discovery_errors)
                    last_discovery_scan = time.monotonic()
                enqueue_discovered(new_markets, selected, seen_markets, expected_extensions,
                                   pending, config, args.presettlement)
                # Bounded in-flight queue avoids storing per-market futures for a huge census.
                while pending and len(futures) < args.workers * 2:
                    group = [pending.popleft() for _ in range(min(100, len(pending)))]
                    future = executor.submit(collect_group, api, args.root, group, config,
                                             cutoff, args.presettlement, args.bars_per_chunk)
                    futures[future] = [market["ticker"] for _, market in group]
                if futures:
                    done, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
                    for future in done:
                        tickers = futures.pop(future)
                        try:
                            results = future.result()
                        except Exception as exc:
                            results = [{"market_ticker": ticker, "status": "error",
                                        "error": f"{type(exc).__name__}: {exc}"} for ticker in tickers]
                        with log_path.open("a", encoding="utf-8") as log:
                            for result in results:
                                result["logged_at"] = utc_now()
                                log.write(json.dumps(result, ensure_ascii=False) + "\n")
                                counts[result["status"]] += 1
                                counts["hourly_rows_new"] += result.get("hourly_rows", 0)
                                counts["daily_rows_new"] += result.get("daily_rows", 0)
                manifest.update(updated_at=utc_now(), counts=counts, discovered_series=len(seen_files),
                                discovered_markets=len(seen_markets), pending_markets=len(pending),
                                in_flight=len(futures), request_count=api.request_count,
                                retry_count=api.retry_count, effective_rps=1 / api.limiter.interval,
                                census_finished=census_finished(args.root),
                                expected_common_artifacts=len(seen_markets),
                                expected_presettlement_artifacts=len(expected_extensions),
                                expected_period_windows=2 * (len(seen_markets) + len(expected_extensions)))
                if time.monotonic() - last_progress >= 15:
                    atomic_json(manifest_path, manifest)
                    print(json.dumps({"at": manifest["updated_at"], "series": len(seen_files),
                                      "markets": len(seen_markets), "pending": len(pending),
                                      "requests": api.request_count, **counts}), flush=True)
                    last_progress = time.monotonic()
                if not futures and not pending:
                    if not args.follow or census_finished(args.root) or (selected and selected <= seen_markets):
                        # The last future can finish after the periodic scan while
                        # discovery publishes its final files and terminal marker.
                        # Observe termination first, then rescan completed artifacts
                        # before deciding there is no further work to enqueue.
                        final_markets = discover_complete(args.root, seen_files, discovery_errors)
                        enqueue_discovered(final_markets, selected, seen_markets, expected_extensions,
                                           pending, config, args.presettlement)
                        if pending:
                            last_discovery_scan = time.monotonic()
                            continue
                        break
                    time.sleep(min(args.poll_seconds, 45))
        manifest.update(status="complete_with_errors" if counts["partial"] or counts["error"] or discovery_errors
                        else "complete", completed_at=utc_now(), counts=counts,
                        expected_common_artifacts=len(seen_markets),
                        expected_presettlement_artifacts=len(expected_extensions),
                        expected_period_windows=2 * (len(seen_markets) + len(expected_extensions)),
                        final_discovery_rescan=True)
        if selected - seen_markets:
            manifest["missing_selected_tickers"] = sorted(selected - seen_markets)
            manifest["status"] = "complete_with_errors"
        atomic_json(manifest_path, manifest)
        print(json.dumps({"status": manifest["status"], "counts": counts, "manifest": str(manifest_path)}), flush=True)
    finally:
        api.close()


if __name__ == "__main__":
    main()
