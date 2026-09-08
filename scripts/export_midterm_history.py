"""Export the frozen research cohort's retained hourly rows and daily summaries.

Read-only input caches; output XLSX shards use numeric cells and naive UTC
datetimes with explicit UTC headers. No prices, intervals or positions are imputed.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time as clock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xlsxwriter

from pmresearch.issue_research import config_hash, load_candles, load_config, number, read_json, timestamp

UTC = timezone.utc
DAY = 86400
HOUR_FIELDS = [
    "ticker",
    "series_ticker",
    "event_ticker",
    "channel",
    "issue_ids",
    "hour_end_UTC",
    "utc_day",
    "yes_bid_USD",
    "yes_ask_USD",
    "midpoint_probability",
    "spread_probability",
    "quote_status",
    "volume_contracts",
    "open_interest_contracts",
    "face_value_USD",
    "OI_face_exposure_USD",
    "boundary_partial_API",
    "incomplete_UTC_day",
    "at_or_after_scheduled_close",
    "source_path",
]
DAY_FIELDS = [
    "ticker",
    "series_ticker",
    "event_ticker",
    "channel",
    "issue_ids",
    "day_UTC",
    "observed_intervals",
    "volume_observed_intervals",
    "daily_volume_contracts",
    "OI_last_observed_contracts",
    "OI_observed_at_UTC",
    "OI_face_exposure_USD",
    "face_value_USD",
    "usable_quote_intervals",
    "last_usable_midpoint_probability",
    "quote_observed_at_UTC",
    "first_hour_end_UTC",
    "last_hour_end_UTC",
    "boundary_partial_intervals",
    "incomplete_UTC_day",
    "missing_or_outside_nominal_24_intervals",
    "all_intervals_in_report_completed_day_aggregate",
    "report_eligible_intervals",
    "report_daily_volume_contracts",
    "report_daily_last_OI_contracts",
]
NOTES = [
    "Completed 6,032-contract research cohort only; the broader full-category extraction is separate and ongoing.",
    "Hourly data are the normalized retained observations used by the report. Market-by-market counts and endpoints reconcile to frozen coverage.csv.",
    "UTC headers are explicit. Excel datetimes have timezone information removed only after conversion to UTC; epoch/date meaning is unchanged.",
    "Prices are YES bid/ask closes in USD, plus midpoint and spread divided by face value. They are not transaction prices, OHLC trade data or executable guarantees.",
    "Midpoint remains present for valid wide-spread quotes; quote_status identifies usability under the frozen report rules. Missing values remain blank.",
    "volume_contracts is a flow. Daily volume sums only observed full API hourly intervals and never imputes missing intervals or subtracts overlap fractions.",
    "OI is a stock: daily OI is the final nonmissing contract observation in that UTC day, never the sum of hourly OI; its timestamp is retained.",
    "OI_face_exposure_USD = OI contracts times stated USD payout face. This is gross payout-face exposure, NOT money invested, trade notional or net directional positioning.",
    "Fractional contract counts are preserved from public API fixed-point fields; they are not rounded to integers.",
    "UTC day uses hour-end timestamp minus one second. A midnight-ending interval belongs to the preceding day.",
    "incomplete_UTC_day flags the frozen intraday cutoff, a dataset/market starting during the day, or a close/settlement during that day; sparse bars are separately counted.",
    "boundary_partial_API flags API partial/boundary annotations. A complete-clock day with missing bars is not assumed to have zero activity.",
    "Daily summaries include all exported hourly rows. Separate report_eligible_intervals/report_daily fields apply the report's narrower completed-day and scheduled-close conditions exactly.",
    "Closed/resolved contracts with no retained history are retained in MARKET_METADATA with the frozen coverage reason. Older pre-settlement extensions are separate.",
    "Issue IDs may overlap; adding across issue groups double-counts some contracts. These data reveal neither complete large positions nor trader portfolios.",
    "Definitions and snapshot status are later-retrieved metadata, not a contemporaneous historical taxonomy. Exact historical website-category membership is unverified.",
    "Source cache paths and SHA256 hashes are in SOURCE_ARTIFACTS. These large Excel files and raw caches are local-only and are not committed as GitHub assets.",
]


def utc_cell(ts):
    return datetime.fromtimestamp(ts, UTC).replace(tzinfo=None) if ts is not None else None


def day_ts(ts):
    return (ts - 1) // DAY * DAY


def incomplete_day(day, market, asof, window_start):
    beginning = max(window_start, timestamp(market.get("open_time")) or window_start)
    endings = [asof] + [
        value
        for value in [timestamp(market.get("close_time")), timestamp(market.get("settlement_ts"))]
        if value is not None
    ]
    return day < beginning < day + DAY or any(day < value < day + DAY for value in endings)


def summarize_day(market, rows, asof, window_start, report_start, report_end):
    """Aggregate observed flows and last stocks; zero appears only if actually reported."""
    rows = sorted(rows, key=lambda r: r["end_ts"])
    day = day_ts(rows[0]["end_ts"])
    volumes = [r["volume_contracts"] for r in rows if r["volume_contracts"] is not None]
    oi = next((r for r in reversed(rows) if r["open_interest_contracts"] is not None), None)
    quote = next((r for r in reversed(rows) if r["usable_quote"]), None)
    face = market["face_usd"]
    stops = [
        v
        for v in [timestamp(market.get("close_time")), timestamp(market.get("settlement_ts"))]
        if v is not None
    ]
    stop = min(stops) if stops else None
    report_rows = [
        r for r in rows if report_start < r["end_ts"] <= report_end and (stop is None or r["end_ts"] < stop)
    ]
    report_volumes = [r["volume_contracts"] for r in report_rows if r["volume_contracts"] is not None]
    report_oi = next(
        (
            r["open_interest_contracts"]
            for r in reversed(report_rows)
            if r["open_interest_contracts"] is not None
        ),
        None,
    )
    return [market[k] for k in ["ticker", "series_ticker", "event_ticker", "channel", "issue_ids"]] + [
        utc_cell(day),
        len(rows),
        len(volumes),
        sum(volumes) if volumes else None,
        oi["open_interest_contracts"] if oi else None,
        utc_cell(oi["end_ts"]) if oi else None,
        oi["open_interest_contracts"] * face if oi and face is not None else None,
        face,
        sum(r["usable_quote"] for r in rows),
        quote["mid_probability"] if quote else None,
        utc_cell(quote["end_ts"]) if quote else None,
        utc_cell(rows[0]["end_ts"]),
        utc_cell(rows[-1]["end_ts"]),
        sum(r["boundary_partial"] for r in rows),
        incomplete_day(day, market, asof, window_start),
        max(0, 24 - len(rows)),
        len(report_rows) == len(rows),
        len(report_rows),
        sum(report_volumes) if report_volumes else None,
        report_oi,
    ]


class Shards:
    def __init__(self, out, stem, fields, max_rows, notes=NOTES):
        self.out, self.stem, self.fields, self.max_rows = out, stem, fields, max_rows
        self.notes, self.files, self.book, self.sheet, self.rows, self.total = notes, [], None, None, 0, 0

    def open(self):
        name = f"{self.stem}_{len(self.files) + 1:03d}.xlsx"
        path = self.out / name
        self.path = path
        self.book = xlsxwriter.Workbook(
            str(path) + ".part",
            {"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False},
        )
        self.date_format = self.book.add_format({"num_format": 'yyyy-mm-dd hh:mm:ss "UTC"'})
        header = self.book.add_format({"bold": True, "font_color": "white", "bg_color": "#172F46"})
        readme = self.book.add_worksheet("README")
        readme.set_column(0, 0, 115)
        for i, note in enumerate(self.notes):
            readme.write_string(i, 0, note)
        self.sheet = self.book.add_worksheet(self.stem)
        self.sheet.write_row(0, 0, self.fields, header)
        self.sheet.freeze_panes(1, 1)
        self.sheet.set_column(0, len(self.fields) - 1, 22)
        self.sheet.set_column(0, 2, 38)
        self.rows = 0

    def append(self, row):
        if self.book is None:
            self.open()
        if self.rows >= self.max_rows:
            self.close()
            self.open()
        self.rows += 1
        self.total += 1
        for col, value in enumerate(row):
            if isinstance(value, datetime):
                self.sheet.write_datetime(self.rows, col, value, self.date_format)
            elif value is not None:
                self.sheet.write(self.rows, col, value)
        return self.path.name

    def close(self):
        if self.book is None:
            return
        self.sheet.autofilter(0, 0, self.rows, len(self.fields) - 1)
        self.book.close()
        Path(str(self.path) + ".part").replace(self.path)
        record = {
            "file": self.path.name,
            "rows": self.rows,
            "bytes": self.path.stat().st_size,
            "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
        }
        self.files.append(record)
        print(json.dumps({"completed_file": record["file"], "rows": record["rows"]}), flush=True)
        self.book = None


def csv_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def export(source_root, supplement_root, analysis_root, out, cfg_path, max_rows=250000):
    started = clock.monotonic()
    source_root, supplement_root, analysis_root, out = map(
        lambda p: Path(p).resolve(), [source_root, supplement_root, analysis_root, out]
    )
    if not 1 <= max_rows <= 250000:
        raise ValueError("Shard size must be 1..250000 rows")
    if any(out == root or root in out.parents for root in [source_root, supplement_root, analysis_root]):
        raise ValueError("Output must be outside read-only source and analysis roots")
    cfg, frozen = load_config(cfg_path), read_json(source_root / "run_config.json")
    analysis = read_json(analysis_root / "analysis_manifest.json")
    if (
        analysis.get("candle_dataset") != "common_only"
        or analysis.get("presettlement_extensions_included") is not False
    ):
        raise ValueError("Excel research export requires a verified common-only analysis")
    if analysis["asof_utc"] != frozen["asof_utc"] or config_hash(cfg) != analysis["config_sha256"]:
        raise ValueError("Analysis/config/cutoff mismatch")
    if read_json(supplement_root / "run_config.json") != frozen:
        raise ValueError("Supplement run config differs")
    markets = csv_rows(analysis_root / "analysis_market_index.csv")
    coverage = {r["ticker"]: r for r in csv_rows(analysis_root / "coverage.csv")}
    if len(markets) != len(coverage) or len({m["ticker"] for m in markets}) != len(markets):
        raise ValueError("Duplicate or mismatching cohort coverage")
    out.mkdir(parents=True, exist_ok=True)
    source_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            analysis_root / "analysis_market_index.csv",
            analysis_root / "coverage.csv",
            analysis_root / "analysis_manifest.json",
            Path(cfg_path),
            source_root / "run_config.json",
        ]
    }
    prior = out / "export_manifest.json"
    if prior.exists() and read_json(prior).get("input_hashes") != source_hashes:
        raise ValueError("Existing export has incompatible input hashes")
    asof, start = int(frozen["asof_ts"]), int(frozen["hourly_start_ts"])
    report_end = asof // DAY * DAY
    report_start = start
    manifest = {
        "status": "running",
        "asof_utc": frozen["asof_utc"],
        "input_hashes": source_hashes,
        "markets": len(markets),
        "max_rows_per_shard": max_rows,
        "notes": NOTES,
        "output_root": str(out),
        "files": [],
    }
    prior.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    hourly, daily = Shards(out, "HOURLY", HOUR_FIELDS, max_rows), Shards(out, "DAILY", DAY_FIELDS, max_rows)
    metadata, artifact_records, mismatches, no_history = [], {}, [], defaultdict(int)
    for i, original in enumerate(markets):
        market = dict(original)
        market["face_usd"] = number(market.get("face_usd"))
        expected = coverage[market["ticker"]]
        rows, artifacts, errors = load_candles(market, [source_root, supplement_root], asof, cfg)
        # The background census may add older extensions after the report. Keep its frozen retained endpoints.
        first, last = (
            timestamp(expected.get("first_observed_utc")),
            timestamp(expected.get("last_observed_utc")),
        )
        rows = [r for r in rows if first is not None and first <= r["end_ts"] <= last]
        if errors or len(rows) != int(expected["hourly_rows"]):
            mismatches.append(
                {
                    "ticker": market["ticker"],
                    "expected_rows": expected["hourly_rows"],
                    "observed_rows": len(rows),
                    "errors": errors,
                }
            )
            break  # Never silently change the report's cohort history.
        shards, day_shards, grouped = set(), set(), defaultdict(list)
        for row in rows:
            t, face, oi = row["end_ts"], market["face_usd"], row["open_interest_contracts"]
            day = day_ts(t)
            close = timestamp(market.get("close_time"))
            values = [
                market[k] for k in ["ticker", "series_ticker", "event_ticker", "channel", "issue_ids"]
            ] + [
                utc_cell(t),
                utc_cell(day),
                row["bid_usd"],
                row["ask_usd"],
                row["mid_probability"],
                row["spread_probability"],
                row["quote_flag"],
                row["volume_contracts"],
                oi,
                face,
                oi * face if oi is not None and face is not None else None,
                row["boundary_partial"],
                incomplete_day(day, market, asof, start),
                bool(close is not None and t >= close),
                row["source_path"],
            ]
            shards.add(hourly.append(values))
            grouped[day].append(row)
        for day_rows in grouped.values():
            day_shards.add(
                daily.append(summarize_day(market, day_rows, asof, start, report_start, report_end))
            )
        reason = ""
        if not rows:
            settled = timestamp(market.get("settlement_ts"))
            reason = (
                "settled_before_common_window"
                if settled and settled < start
                else "no_retained_hourly_rows_see_frozen_coverage"
            )
            no_history[reason] += 1
        metadata.append(
            {
                **original,
                **{
                    key + "_raw": original.get(key)
                    for key in ["created_time", "open_time", "close_time", "settlement_ts"]
                },
                **{f"coverage_{k}": v for k, v in expected.items() if k != "ticker"},
                "exported_hourly_rows": len(rows),
                "exported_daily_rows": len(grouped),
                "no_history_reason": reason,
                "hourly_files": "|".join(sorted(shards)),
                "daily_files": "|".join(sorted(day_shards)),
            }
        )
        for artifact in artifacts:
            path = Path(artifact["path"])
            if str(path) not in artifact_records:
                artifact_records[str(path)] = {
                    "source_path": str(path),
                    "status": artifact["status"],
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
        if (i + 1) % 500 == 0:
            print(
                json.dumps({"markets": i + 1, "hourly_rows": hourly.total, "daily_rows": daily.total}),
                flush=True,
            )
    hourly.close()
    daily.close()
    if mismatches:
        manifest.update(status="failed_frozen_coverage_mismatch", mismatches=mismatches)
        prior.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        raise ValueError(str(mismatches[:3]))
    files = hourly.files + daily.files
    book = xlsxwriter.Workbook(
        str(out / "INDEX.xlsx"), {"strings_to_formulas": False, "strings_to_urls": False}
    )
    heading = book.add_format({"bold": True, "font_color": "white", "bg_color": "#172F46"})
    sheet = book.add_worksheet("README")
    sheet.set_column(0, 0, 125)
    for i, note in enumerate(
        [
            f"Frozen UTC cutoff: {frozen['asof_utc']}",
            f"Markets: {len(markets):,}; hourly rows: {hourly.total:,}; daily rows: {daily.total:,}",
        ]
        + NOTES
    ):
        sheet.write_string(i, 0, note)
    sheet = book.add_worksheet("FILES")
    sheet.write_row(0, 0, ["Workbook", "Data rows", "Bytes", "SHA256"], heading)
    sheet.set_column(0, 0, 32)
    sheet.set_column(1, 2, 20)
    sheet.set_column(3, 3, 70)
    for i, row in enumerate(files, 1):
        sheet.write_url(i, 0, "external:" + row["file"], string=row["file"])
        sheet.write_row(i, 1, [row["rows"], row["bytes"], row["sha256"]])
    for name, records in [
        ("MARKET_METADATA", metadata),
        ("SOURCE_ARTIFACTS", list(artifact_records.values())),
    ]:
        sheet = book.add_worksheet(name)
        keys = list(records[0]) if records else []
        sheet.write_row(0, 0, keys, heading)
        sheet.freeze_panes(1, 1)
        sheet.set_column(0, len(keys) - 1, 24)
        datefmt = book.add_format({"num_format": 'yyyy-mm-dd hh:mm:ss "UTC"'})
        for i, row in enumerate(records, 1):
            for col, key in enumerate(keys):
                value = row.get(key)
                if key in {
                    "created_time",
                    "open_time",
                    "close_time",
                    "settlement_ts",
                    "expected_expiration_time",
                    "snapshot_observed_utc",
                }:
                    ts = timestamp(value)
                    if ts is not None:
                        sheet.write_datetime(i, col, utc_cell(ts), datefmt)
                elif key.startswith("coverage_") and key.removeprefix("coverage_") in {
                    "artifact_count",
                    "artifact_errors",
                    "hourly_rows",
                    "usable_two_sided_hours",
                    "common_window_nominal_hours",
                    "common_window_observed_hours",
                    "common_window_unobserved_nominal_hours",
                    "missing_or_invalid_two_sided_hours",
                }:
                    if number(value) is not None:
                        sheet.write_number(i, col, number(value))
                elif key == "face_usd":
                    if number(value) is not None:
                        sheet.write_number(i, col, number(value))
                elif value is not None:
                    sheet.write(i, col, value)
        sheet.autofilter(0, 0, len(records), max(0, len(keys) - 1))
    book.close()
    manifest.update(
        status="complete",
        hourly_rows=hourly.total,
        daily_rows=daily.total,
        markets_with_history=sum(m["exported_hourly_rows"] > 0 for m in metadata),
        no_history_reasons=dict(no_history),
        files=files,
        source_artifacts=len(artifact_records),
        elapsed_seconds=clock.monotonic() - started,
        index_sha256=hashlib.sha256((out / "INDEX.xlsx").read_bytes()).hexdigest(),
    )
    prior.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "README.txt").write_text("\n\n".join(NOTES), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in manifest.items() if k not in {"notes", "files", "input_hashes"}}, indent=2
        ),
        flush=True,
    )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("data/kalshi_full_20260908"))
    parser.add_argument(
        "--supplement-root", type=Path, default=Path("data/midterm_research_20260908/kalshi_supplement")
    )
    parser.add_argument(
        "--analysis-root", type=Path, default=Path("data/midterm_research_20260908/kalshi_analysis")
    )
    parser.add_argument("--out", type=Path, default=Path("data/midterm_research_20260908/excel_history"))
    parser.add_argument("--config", type=Path, default=Path("config/midterm_issues.json"))
    parser.add_argument("--max-rows", type=int, default=250000)
    args = parser.parse_args()
    export(args.source_root, args.supplement_root, args.analysis_root, args.out, args.config, args.max_rows)


if __name__ == "__main__":
    main()
