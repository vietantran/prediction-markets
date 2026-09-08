"""Bounded read-only quality audit of completed census candle artifacts."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import json
from pathlib import Path

ROOT = Path("data/kalshi_full_20260908")
MAX_ARTIFACTS = 1000


def read(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def number(value):
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite numeric field")
    return result


def quantity(row, name):
    return number(row.get(name + "_fp", row.get(name)))


def main():
    started = datetime.now(timezone.utc).isoformat()
    config = json.loads((ROOT / "run_config.json").read_text(encoding="utf-8-sig"))
    errors = Counter()
    examples = defaultdict(list)
    totals = Counter()
    reconciliation = Counter()
    volume_differences = []
    oi_differences = []
    daily_offsets = Counter()
    price_units = Counter()
    source_tiers = Counter()
    observed_ranges = {}
    inconsistent_unit_rows = set()
    empty_trade_price_rows = 0
    sample = []
    master_cache = {}
    checked_master = set()

    def issue(kind, **detail):
        errors[kind] += 1
        if kind == "price_unit_inconsistent":
            inconsistent_unit_rows.add((detail.get("ticker"), detail.get("window"),
                                        detail.get("period"), detail.get("end_period_ts")))
        if len(examples[kind]) < 12:
            examples[kind].append(detail)

    def check_number(value, field, context, face=None):
        try:
            parsed = number(value)
            if parsed is not None:
                totals["numeric_values_checked"] += 1
                if parsed < 0:
                    issue("negative_numeric_value", field=field, value=str(value), **context)
                if face is not None and parsed > face:
                    issue("price_exceeds_face", field=field, value=str(value), face=str(face), **context)
            else:
                totals["null_numeric_values_preserved"] += 1
            return parsed
        except (ValueError, InvalidOperation):
            issue("invalid_numeric_value", field=field, value=str(value), **context)
            return None

    def update_range(name, value):
        if value is None:
            return
        current = observed_ranges.setdefault(name, {"min": value, "max": value})
        current["min"] = min(current["min"], value)
        current["max"] = max(current["max"], value)

    def inspect(path, payload):
        nonlocal empty_trade_price_rows
        ticker = payload["market_ticker"]
        label = payload.get("window_label")
        face = number(payload.get("notional_value_dollars"))
        context = {"ticker": ticker, "window": label}
        if face is None:
            issue("missing_face_value", **context)
        if payload.get("asof_ts") != config["asof_ts"]:
            issue("asof_mismatch", observed=payload.get("asof_ts"), **context)
        windows = {w.get("period_interval"): w for w in payload.get("windows", [])}
        if set(windows) != {60, 1440}:
            issue("missing_period_window", periods=list(windows), **context)
        for period, name in ((60, "hourly"), (1440, "daily")):
            rows = payload.get(name, [])
            totals[name + "_rows"] += len(rows)
            w = windows.get(period, {})
            if w.get("status") not in {"complete", "no_overlap"}:
                issue("complete_artifact_bad_window_status", period=period, status=w.get("status"), **context)
            if w.get("errors"):
                issue("complete_artifact_contains_errors", period=period, errors=w["errors"], **context)
            if w.get("rows") != len(rows):
                issue("row_count_mismatch", period=period, manifest=w.get("rows"), actual=len(rows), **context)
            totals[name + "_missing_interior_periods"] += w.get("missing_interior_periods", 0)
            totals[name + "_trailing_gap_windows"] += w.get("trailing_unobserved_seconds", 0) > 0
            totals[name + "_no_overlap_windows"] += w.get("status") == "no_overlap"
            seen = set()
            previous = None
            for row in rows:
                ts = row.get("end_period_ts")
                rc = {**context, "period": period, "end_period_ts": ts}
                if not isinstance(ts, int):
                    issue("invalid_timestamp", **rc)
                    continue
                if ts in seen:
                    issue("duplicate_timestamp", **rc)
                if previous is not None and ts <= previous:
                    issue("nonascending_timestamp", **rc)
                seen.add(ts)
                previous = ts
                start, stop = w.get("effective_start_ts"), w.get("effective_end_ts")
                begin = ts - period * 60
                if (start is None or stop is None or ts < start or begin >= stop
                        or ts > config["asof_ts"] or ts > w.get("api_query_end_ts", config["asof_ts"])):
                    issue("timestamp_outside_requested_overlap", effective_start=start, effective_end=stop, **rc)
                if start is not None and bool(row.get("_partial_start")) != (begin < start):
                    issue("partial_start_flag_mismatch", **rc)
                if stop is not None and bool(row.get("_partial_end")) != (ts > stop):
                    issue("partial_end_flag_mismatch", **rc)
                if row.get("_window_label") != label:
                    issue("window_label_mismatch", row_label=row.get("_window_label"), **rc)
                if period == 1440:
                    daily_offsets[str(ts % 86400)] += 1
                tier, unit = row.get("_source_tier"), row.get("_price_unit")
                source_tiers[tier] += 1
                price_units[unit] += 1
                empty_trade_price_rows += not row.get("price")
                if (unit not in {"dollars", "cents", "unavailable"}
                        or (tier == "historical" and unit != "dollars")
                        or (tier == "live" and not row.get("price") and unit != "unavailable")):
                    issue("price_unit_inconsistent", source_tier=tier, price_unit=unit, **rc)
                for group in ("price", "yes_bid", "yes_ask"):
                    for field, value in (row.get(group) or {}).items():
                        check = check_number(value, f"{group}.{field}", rc)
                        if check is not None and face is not None:
                            multiplier = Decimal(1) if field.endswith("_dollars") or unit == "dollars" else Decimal("0.01")
                            update_range("candle_price_dollars", check * multiplier)
                            if check * multiplier > face:
                                issue("price_exceeds_face", field=f"{group}.{field}", value=str(value),
                                      dollar_value=str(check * multiplier), face=str(face), **rc)
                            if group == "price" and field.endswith("_dollars") and unit != "dollars":
                                issue("price_unit_inconsistent", field=field, price_unit=unit, **rc)
                for q in ("volume", "open_interest"):
                    value = check_number(row.get(q + "_fp", row.get(q)), q, rc)
                    update_range(name + "_" + q + "_contracts", value)
        hourly = {r["end_period_ts"]: r for r in payload.get("hourly", [])}
        for day in payload.get("daily", []):
            reconciliation["daily_rows_considered"] += 1
            end = day["end_period_ts"]
            if day.get("_partial_start") or day.get("_partial_end") or day.get("_boundary_only"):
                reconciliation["skipped_partial_daily_interval"] += 1
                continue
            hours = [hourly.get(end - i * 3600) for i in reversed(range(24))]
            if any(h is None for h in hours):
                reconciliation["skipped_missing_hourly_bars"] += 1
                continue
            if any(h.get("_partial_start") or h.get("_partial_end") or h.get("_boundary_only") for h in hours):
                reconciliation["skipped_partial_hourly_interval"] += 1
                continue
            reconciliation["comparable_24_hour_intervals"] += 1
            vols = [quantity(h, "volume") for h in hours]
            daily_volume = quantity(day, "volume")
            if daily_volume is None or any(v is None for v in vols):
                reconciliation["volume_null_comparisons_skipped"] += 1
            else:
                summed = sum(vols, Decimal(0))
                delta = summed - daily_volume
                reconciliation["volume_comparisons"] += 1
                if delta:
                    reconciliation["volume_exact_disagreements"] += 1
                    reconciliation["volume_disagreements_gt_0_01"] += abs(delta) > Decimal("0.01")
                    volume_differences.append({**context, "end_period_ts": end,
                        "hourly_sum": str(summed), "daily_api": str(daily_volume), "difference": str(delta),
                        "relative_difference": str(abs(delta) / max(abs(daily_volume), Decimal("0.01"))),
                        "hourly_last_retrieved_at": hours[-1].get("_retrieved_at"),
                        "daily_retrieved_at": day.get("_retrieved_at")})
            last_oi, daily_oi = quantity(hours[-1], "open_interest"), quantity(day, "open_interest")
            if last_oi is None or daily_oi is None:
                reconciliation["oi_null_comparisons_skipped"] += 1
            else:
                reconciliation["oi_comparisons"] += 1
                if last_oi != daily_oi:
                    reconciliation["oi_exact_disagreements"] += 1
                    oi_differences.append({**context, "end_period_ts": end,
                                          "hourly_end_oi": str(last_oi), "daily_end_oi": str(daily_oi),
                                          "difference": str(last_oi - daily_oi)})
        series = payload["series_ticker"]
        if series not in master_cache:
            source = ROOT / "discovery" / f"{series}.json.gz"
            if source.exists():
                master_cache[series] = {m["ticker"]: m for m in read(source).get("markets", [])}
            else:
                master_cache[series] = {}
        if ticker not in checked_master:
            checked_master.add(ticker)
            market = master_cache[series].get(ticker)
            if market is None:
                issue("missing_discovery_master_record", **context)
            else:
                totals["master_records_checked"] += 1
                if market.get("notional_value_dollars") != payload.get("notional_value_dollars"):
                    issue("master_candle_face_mismatch", **context)
                for field in ("yes_bid_dollars", "yes_ask_dollars", "no_bid_dollars", "no_ask_dollars", "last_price_dollars"):
                    check_number(market.get(field), field, {"ticker": ticker, "table": "master"}, face)
                for field in ("volume_fp", "volume_24h_fp", "open_interest_fp"):
                    check_number(market.get(field), field, {"ticker": ticker, "table": "master"})
        sample.append({"path": str(path), "market_ticker": ticker, "window_label": label,
                       "hourly_rows": len(payload.get("hourly", [])), "daily_rows": len(payload.get("daily", []))})

    common_paths = sorted((ROOT / "candles").glob("*.json.gz"))
    extension_paths = sorted((ROOT / "candles_presettlement").glob("*.json.gz"))
    # Reserve up to 200 extension files, then fill the balance with common files.
    for paths, quota in ((extension_paths, 200), (common_paths, MAX_ARTIFACTS)):
        accepted = 0
        for path in paths:
            if accepted >= quota or len(sample) >= MAX_ARTIFACTS:
                break
            try:
                payload = read(path)
            except (OSError, ValueError, EOFError) as exc:
                issue("file_read_error", path=str(path), error=str(exc))
                continue
            if payload.get("status") != "complete":
                totals["noncomplete_files_skipped"] += 1
                continue
            inspect(path, payload)
            accepted += 1
    volume_differences.sort(key=lambda r: abs(Decimal(r["difference"])), reverse=True)
    oi_differences.sort(key=lambda r: abs(Decimal(r["difference"])), reverse=True)
    report = {
        "schema_version": 1, "qa_started_at": started,
        "qa_completed_at": datetime.now(timezone.utc).isoformat(), "run_asof_utc": config["asof_utc"],
        "sampling": "At most 200 lexicographically first complete presettlement artifacts; then first complete common artifacts, maximum 1000 total. Convenience sample, not random; active collection continues.",
        "files_present_at_start": {"common": len(common_paths), "presettlement": len(extension_paths)},
        "sample_artifacts": len(sample), "sample_unique_markets": len(checked_master),
        "sample_by_window": dict(Counter(r["window_label"] for r in sample)),
        "totals": dict(totals), "validation_errors": dict(errors), "validation_examples": dict(examples),
        "source_tier_rows": dict(source_tiers), "price_units": dict(price_units),
        "price_unit_inconsistent_rows": len(inconsistent_unit_rows),
        "empty_trade_price_object_rows": empty_trade_price_rows,
        "observed_ranges": {key: {part: str(value) for part, value in vals.items()}
                            for key, vals in observed_ranges.items()},
        "daily_end_modulo_utc_day_seconds": dict(daily_offsets), "reconciliation": dict(reconciliation),
        "largest_volume_disagreements": volume_differences[:20], "largest_oi_disagreements": oi_differences[:20],
        "sample_manifest": sample,
    }
    (ROOT / "qa_snapshot.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("qa_completed_at", "sample_artifacts", "sample_unique_markets",
                                           "sample_by_window", "totals", "validation_errors", "reconciliation",
                                           "largest_volume_disagreements", "largest_oi_disagreements")}, indent=2))


if __name__ == "__main__":
    main()
