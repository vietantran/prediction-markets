"""Resumable public Kalshi census, preserving raw pages and category uncertainty.

Run with .venv/Scripts/python.exe scripts/kalshi_full_census.py.
The historical API ignores undocumented date filters: every returned archive
page is retained and settlement eligibility is evaluated locally.
"""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import gzip
import json
import os
from pathlib import Path
import random
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

BASE = "https://external-api.kalshi.com/trade-api/v2"
CATEGORIES = ["Politics", "Commodities", "Economics", "Mentions", "Financials", "Elections"]
TAG_PATHS = {
    "Politics": {"Congress": "congress"},
    "Economics": {"Econ Daily": "econ-daily", "Econ Weekly": "econ-weekly", "Fed": "fed", "GDP": "gdp", "Global Central Banks": "global-central-banks", "Growth": "growth", "Housing": "housing", "Inflation": "inflation", "Jobs & Economy": "jobs-economy", "Oil and energy": "oil-and-energy"},
    "Mentions": {"Earnings": "earnings", "Politicians": "politicians", "Trump": "trump"},
    "Financials": {"Indices": "indices", "Markets": "markets", "Foreign Exchange": "foreign-exchange", "Interest Rates": "interest-rates", "Match Ups": "match-ups"},
    "Elections": {"US Elections": "us-elections", "House": "house", "Senate": "senate", "Governor": "governor", "Referendums": "referendums"},
}


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def months_before(value, months):
    month_number = value.year * 12 + value.month - 1 - months
    year, month = divmod(month_number, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def read_json(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{threading.get_ident()}")
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(temp, "wt", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
    for retry, delay in enumerate((0, 0.1, 0.25, 0.5, 1, 2)):
        if delay:
            time.sleep(delay)
        try:
            os.replace(temp, path)
            break
        except PermissionError:
            if retry == 5:
                # Preserve the completed temporary file for recovery.
                raise


class Client:
    def __init__(self, rate):
        self.interval = 1 / rate
        self.minimum_interval = self.interval
        self.lock = threading.Lock()
        self.next_slot = 0.0
        self.local = threading.local()
        self.requests = 0
        self.retries = 0
        self.rate_limited = 0
        self.success_streak = 0

    def get(self, endpoint, params=None):
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers["User-Agent"] = "KalshiPublicResearchCensus/1.0"
        for attempt in range(9):
            with self.lock:
                now = time.monotonic()
                slot = max(now, self.next_slot)
                self.next_slot = slot + self.interval
                self.requests += 1
            time.sleep(max(0, slot - time.monotonic()))
            try:
                response = self.local.session.get(BASE + endpoint, params=params, timeout=(15, 90))
                if response.status_code == 429:
                    with self.lock:
                        self.rate_limited += 1
                        self.success_streak = 0
                        self.interval = min(2.0, self.interval * 1.3)
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON object")
                with self.lock:
                    self.success_streak += 1
                    if self.success_streak >= 100:
                        self.interval = max(self.minimum_interval, self.interval * 0.95)
                        self.success_streak = 0
                return result
            except (requests.RequestException, ValueError) as exc:
                permanent = isinstance(exc, requests.HTTPError) and exc.response is not None and 400 <= exc.response.status_code < 500 and exc.response.status_code != 429
                if permanent or attempt == 8:
                    raise
                with self.lock:
                    self.retries += 1
                time.sleep(min(30, 2 ** attempt) + random.random())


def eligible(market, config):
    created = parse_ts(market.get("created_time"))
    if created is not None and created > config["asof_ts"]:
        return False, "created_after_frozen_asof"
    settled = parse_ts(market.get("settlement_ts"))
    if settled is not None:
        if config["eligibility_start_ts"] <= settled <= config["asof_ts"]:
            return True, "settled_in_last_three_calendar_months"
        if settled > config["asof_ts"]:
            return True, "unresolved_at_frozen_asof_later_settlement_observed"
        return False, "settled_before_eligibility_window"
    status = str(market.get("status", "")).lower()
    if status in {"finalized", "settled"}:
        # Preserve uncertainty instead of asserting an unknown settlement date.
        return True, "settled_timestamp_missing_eligibility_uncertain"
    return True, "unresolved_including_paused_unopened_or_awaiting_resolution"


def memberships(series, categories):
    tags = set(series.get("tags") or [])
    paths, evidence = set(), []
    for category in categories:
        if category != "Elections":
            paths.add("https://kalshi.com/category/" + category.lower())
            evidence.append({"basis": "api_category_query", "category": category, "website_equivalence": "unverified"})
        for tag, slug in TAG_PATHS.get(category, {}).items():
            if tag in tags:
                paths.add(f"https://kalshi.com/category/{category.lower()}/{slug}")
                evidence.append({"basis": "series_tag", "category": category, "tag": tag, "website_equivalence": "inferred"})
    title = (series.get("title") or "").lower()
    midterm = any(word in title for word in ("midterm", "house winner", "senate winner", "balance of power"))
    if "Elections" in categories and midterm:
        paths.add("https://kalshi.com/category/elections/midterms")
        evidence.append({"basis": "title_midterm_candidate", "event_cycle_requires_verification": True})
        for word in ("house", "governor", "balance of power"):
            if word in title:
                paths.add("https://kalshi.com/category/elections/midterms/" + word.replace(" ", "-"))
    only_elections = categories == ["Elections"]
    us_tags = {"US Elections", "Other US Elections", "House", "Senate", "Governor", "Referendums", "Election Combos", "House Combos", "Senate Combos", "Governor Combos"}
    election_relevance = bool(tags & us_tags) or midterm
    clearly_outside = only_elections and bool(tags & {"International elections", "Brazil"}) and not election_relevance
    return {
        "category_queries": categories, "category_paths": sorted(paths), "membership_evidence": evidence,
        "membership_confidence": "out_of_requested_elections_tags" if clearly_outside else ("candidate_membership_unresolved" if only_elections and not election_relevance else "api_category_or_tag_inferred_website_unverified"),
        "included_in_census": not clearly_outside,
    }


def discover_series(client, output, config):
    combined = {}
    for category in CATEGORIES:
        path = output / "raw" / "catalog" / f"series_{category.lower()}.json.gz"
        if path.exists():
            response = read_json(path)["response"]
        else:
            response = client.get("/series", {"category": category, "include_product_metadata": "true"})
            write_json(path, {"request": {"endpoint": "/series", "params": {"category": category}}, "retrieved_at": utcnow(), "response": response})
        for series in response.get("series") or []:
            ticker = series["ticker"]
            item = combined.setdefault(ticker, {"series_ticker": ticker, "series": series, "category_queries": []})
            item["category_queries"].append(category)
    result = []
    for item in combined.values():
        item.update(memberships(item["series"], item["category_queries"]))
        result.append(item)
    priority_words = ("data center", "datacenter", "electric", "gas price", "ai ", "artificial intelligence", "fed ", "governor", "senate", "house", "cpi", "inflation", "health", "housing")
    result.sort(key=lambda item: (not any(word in str(item["series"].get("title", "")).lower() for word in priority_words), item["series_ticker"]))
    write_json(output / "universe_series.json", result)
    return [item for item in result if item["included_in_census"]]


def scan_one(item, client, output, config):
    ticker = item["series_ticker"]
    file_ticker = quote(ticker, safe="-_ .").replace(" ", "_")
    target = output / "discovery" / f"{file_ticker}.json.gz"
    if target.exists():
        saved = read_json(target)
        if saved.get("status") == "complete":
            return {"series_ticker": ticker, "status": "complete", "eligible_count": len(saved["markets"]), "cached": True}
    payload = {"schema_version": 1, **item, "started_at": utcnow(), "status": "running", "current": [], "historical": [], "markets": [], "excluded_count": 0, "errors": [], "pages": {}, "scanned_counts": {}, "mve_excluded": False}
    for tier, endpoint, key in (("live", "/markets", "current"), ("historical", "/historical/markets", "historical")):
        cursor, seen, page_number, scanned = None, set(), 0, 0
        try:
            while True:
                page_number += 1
                params = {"series_ticker": ticker, "limit": 1000}
                if cursor:
                    params["cursor"] = cursor
                page_path = output / "raw" / "discovery" / file_ticker / tier / f"{page_number:06d}.json.gz"
                if page_path.exists():
                    envelope = read_json(page_path)
                    if envelope.get("request", {}).get("params") != params:
                        raise RuntimeError("Saved-page request differs from resumed cursor")
                    response = envelope["response"]
                else:
                    response = client.get(endpoint, params)
                    envelope = {"request": {"endpoint": endpoint, "params": params}, "retrieved_at": utcnow(), "response": response}
                    write_json(page_path, envelope)
                if "markets" not in response:
                    raise ValueError("Missing markets field")
                for market in response.get("markets") or []:
                    scanned += 1
                    include, reason = eligible(market, config)
                    if include:
                        payload[key].append({**market, "_source_tier": tier, "_eligibility_reason": reason, "_retrieved_at": envelope["retrieved_at"]})
                    else:
                        payload["excluded_count"] += 1
                next_cursor = response.get("cursor") or response.get("next_cursor")
                if not next_cursor:
                    break
                if next_cursor in seen:
                    raise RuntimeError("Repeated pagination cursor")
                seen.add(next_cursor)
                cursor = next_cursor
        except Exception as exc:
            payload["errors"].append({"tier": tier, "page": page_number, "cursor": cursor, "error": str(exc)[:1500]})
        payload["pages"][tier] = page_number
        payload["scanned_counts"][tier] = scanned
    by_ticker = {}
    for market in payload["historical"] + payload["current"]:
        by_ticker[market["ticker"]] = market
    payload["markets"] = list(by_ticker.values())
    payload["status"] = "error" if payload["errors"] else "complete"
    payload["completed_at"] = utcnow()
    write_json(target, payload)
    return {"series_ticker": ticker, "status": payload["status"], "eligible_count": len(payload["markets"]), "scanned_counts": payload["scanned_counts"], "errors": payload["errors"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/kalshi_full_20260908")
    parser.add_argument("--rate", type=float, default=3.0)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    client = Client(args.rate)
    config_path = output / "run_config.json"
    if config_path.exists():
        config = read_json(config_path)
    else:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        start, hourly = months_before(now, 3), months_before(now, 2)
        config = {"schema_version": 1, "asof_utc": now.isoformat().replace("+00:00", "Z"), "asof_ts": int(now.timestamp()), "eligibility_start_utc": start.isoformat().replace("+00:00", "Z"), "eligibility_start_ts": int(start.timestamp()), "hourly_start_utc": hourly.isoformat().replace("+00:00", "Z"), "hourly_start_ts": int(hourly.timestamp()), "public_api_only": True, "pre_settlement_extension": "Two calendar months before settlement for eligible markets settled before hourly_start_ts", "day_convention": "UTC", "mve_excluded": False, "website_membership_exact": False}
        write_json(config_path, config)
    cutoff = client.get("/historical/cutoff")
    write_json(output / "historical_cutoff.json", {"retrieved_at": utcnow(), **cutoff})
    items = discover_series(client, output, config)
    print(json.dumps({"event": "started", "series_count": len(items), "config": config}), flush=True)
    summaries = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scan_one, item, client, output, config): item for item in items}
        for future in concurrent.futures.as_completed(futures):
            try:
                summary = future.result()
            except Exception as exc:
                summary = {"series_ticker": futures[future]["series_ticker"], "status": "error", "eligible_count": 0, "errors": [{"error": str(exc)[:1500]}]}
            summaries.append(summary)
            progress = {"updated_at": utcnow(), "status": "running", "total_series": len(items), "completed_series": len(summaries), "error_series": sum(x["status"] == "error" for x in summaries), "eligible_contract_records": sum(x["eligible_count"] for x in summaries), "requests": client.requests, "retries": client.retries, "rate_limited": client.rate_limited, "effective_rps": round(1 / client.interval, 3), "latest_series": summary["series_ticker"]}
            if len(summaries) % 10 == 0 or summary["status"] == "error":
                write_json(output / "census_progress.json", progress)
                print(json.dumps(progress), flush=True)
    status = "complete" if all(item["status"] == "complete" for item in summaries) else "incomplete_errors"
    manifest = {**progress, "status": status, "finished_at": utcnow(), "series": summaries, "coverage_notes": ["API category-query membership is not proven identical to website navigation membership.", "Ambiguous Elections-only series were collected and flagged; clearly international-only tag records outside other requested broad queries were excluded in universe_series.json.", "No MVE exclusion filter was applied. Per-series census includes returned multivariate markets; complete discoverability of uncategorized custom-combination series is not guaranteed.", "No arbitrary page limit. Every available archive page scanned; undocumented historical date filters intentionally not used.", "Unopened/paused/closed/determined/disputed unresolved contracts retained with actual status.", "Provisional zero-activity listings removed before the census cannot be reconstructed from current APIs."]}
    write_json(output / "discovery_manifest.json", manifest)
    write_json(output / "census_progress.json", {key: value for key, value in manifest.items() if key != "series"})
    print(json.dumps({"event": "finished", "status": status, "series_count": len(items), "errors": progress["error_series"]}), flush=True)


if __name__ == "__main__":
    main()
