"""Read-only issue analysis of the full Kalshi cache; all writes go to --out.

python -m pmresearch.issue_research --source-root CACHE --out OUTPUT --stage inventory
python -m pmresearch.issue_research --source-root CACHE --out OUTPUT --stage analyze

No forecast, salience, trader-position or causal conclusion is manufactured here.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
DAY = 86400
CONFIG = Path(__file__).resolve().parents[1] / "config" / "midterm_issues.json"
CLASSIFICATION_VERSION = 3
POST_MIDTERM_CONTROL = {"KXBALANCEPOWERCOMBO-27FEB-" + suffix for suffix in ["RR", "RD", "DR", "DD"]}
STATE = "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
ELECTION = re.compile(r"^(?:CONTROL[HS]$|(?:KX)?GOVPARTY(?:" + STATE + r")$|HOUSE(?:" + STATE + r")(?:\d|AL)|SENATE(?:" + STATE + r")S?$|KXHOUSERACE$|KXHOUSEWINSTATE$|KXHOUSEPOPVOTEMARGIN$|KXHOUSETURNOUT$|KXHOUSE(?:[A-Z]{2}\d)|KXSTATELEG$|KXPAHOUSE$|KXPASEN$|KXBALANCEPOWERCOMBO$|KXFOURSTATESEN$|KX(?:CASEN|MDSD|MOSD|PAHD)\d)")
FOREIGN = re.compile(r"\b(?:euro(?:pe|zone| area)?|ecb|bank of england|canada|canadian|australia|australian|india|indian|japan|japanese|uk |united kingdom|british|germany|german|france|french|italy|italian|china|chinese|brazil|brazilian|mexico|mexican)\b", re.I)
US = re.compile(r"\b(?:u\.?s\.?|united states|american|trump|federal reserve|fomc|congress|medicare|medicaid|irs|ercot|pennsylvania|michigan|arizona|california|texas|ohio|wisconsin|new york)\b", re.I)
POLICY = re.compile(r"\b(?:law|ban|pass|enact|moratorium|regulat|reconciliation|government|congress|tax|subsid|shutdown|deport|tariff|court|executive order)", re.I)
KNOWN_US_MACRO = re.compile(r"^(?:KX)?(?:CPI|PCE|GDP|PAYROLL|ECONSTATU3|U3|JOBLESS|CONTCLAIMS|NBER|RECSS|FED|RATECUT|TNOTE|NOTE|10Y|30YMORT|MORTGAGE|AAAGAS|EFFTARIFF|DEFGDP|GOVT|TXERCOT)")
SPORT = re.compile(r"\b(?:nba|nfl|nhl|mlb|ncaa|touchdown|super bowl|stanley cup|premier league|grand prix|formula 1|golf|tennis|ufc)\b", re.I)


def read_json(path):
    path = Path(path)
    with (gzip.open if path.suffix == ".gz" else open)(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def timestamp(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return int(value)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(dt.timestamp()) if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def iso(ts):
    return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=ts)).isoformat().replace("+00:00", "Z") if ts is not None else ""


def number(value):
    try:
        if value is None or value == "" or isinstance(value, bool):
            return None
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def normalize_word(value):
    # Preserve disjunctions and numbers: "AI / artificial intelligence" is not "AI".
    return re.sub(r"\s+", " ", re.sub(r"[^\w /+.-]", "", str(value or "").casefold())).strip()


def load_config(path=CONFIG):
    cfg = read_json(path)
    cfg["_compiled"] = [(i["id"], [re.compile(p, re.I) for p in i["patterns"]]) for i in cfg["issues"]]
    return cfg


def config_hash(cfg):
    payload = {k: v for k, v in cfg.items() if k != "_compiled"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    path = Path(path)
    if not path.exists():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory_fingerprints(source_root, cfg, supplement_root=None):
    return {"selection_config_sha256": config_hash(cfg),
            "source_discovery_manifest_sha256": file_hash(source_root / "discovery_manifest.json"),
            "supplement_discovery_manifest_sha256": file_hash(supplement_root / "discovery_manifest.json") if supplement_root else "not_used"}


def inventory_is_current(selection, source_root, cfg, asof, supplement_root=None):
    return (selection.get("status") == "offline_inventory_complete"
            and selection.get("classification_version") == CLASSIFICATION_VERSION
            and selection.get("asof_ts") == asof
            and selection.get("source_root") == str(source_root.resolve())
            and all(selection.get(k) == v for k, v in inventory_fingerprints(source_root, cfg, supplement_root).items()))


def mention_word(m):
    custom = m.get("custom_strike") or {}
    if isinstance(custom, dict):
        for key, value in custom.items():
            if key.casefold() in {"word", "phrase", "term"} and isinstance(value, str):
                return value, "custom_strike." + key
    value = m.get("yes_sub_title")
    if value and str(value).strip().casefold() not in {"yes", "no", "event does not qualify"}:
        return str(value), "yes_sub_title"
    return "", "missing_target_no_title_fallback"


def mention_subject(m, series):
    # Only the subject clause is extracted; no generic rule text is issue-tagged.
    rule = re.sub(r"\s+", " ", m.get("rules_primary", "")).strip()
    found = re.search(r"(?:what will|will) (.{2,90}?) (?:say|mention)\b", m.get("title", ""), re.I)
    if not found:
        found = re.search(r"^If (.{2,100}?) (?:says|mentions|uses|reports|say)\b", rule, re.I)
    return normalize_word(found.group(1)) if found else "series:" + series


def classify(m, series, cfg):
    ticker = str(m.get("ticker", ""))
    st = str(series.get("ticker", ticker.split("-")[0]))
    category = str(series.get("category", "")).casefold()
    title = " ".join([m.get("title", ""), series.get("title", "")])
    is_mention = category == "mentions" or "MENTION" in st
    target, field = mention_word(m) if is_mention else (title, "market_title+series_title")
    issues = [key for key, patterns in cfg["_compiled"] if any(p.search(target) for p in patterns)]
    if is_mention:
        tags = " ".join(series.get("tags") or []).casefold()
        allowed = any(t in tags for t in ["politician", "earnings", "trump"]) or bool(US.search(title))
        allowed = allowed or bool(re.search(r"earnings|earning call|governor|senator|president|secretary|fomc", title, re.I))
        if SPORT.search(title) and not re.search(r"trump|president|governor|senator", title, re.I):
            allowed = False
        if not allowed:
            return None, "mention_context_not_politician_or_earnings"
        if not issues:
            return None, "mention_target_not_in_issue_dictionary"
        return {"channel": "mention", "issue_ids": "|".join(issues), "classification_text": target,
                "classification_field": field, "word_normalized": normalize_word(target),
                "subject": mention_subject(m, st), "scope": "speech_or_earnings_US_relevance_requires_review"}, None
    if category == "elections" or ELECTION.search(st):
        text = " ".join([ticker, m.get("title", ""), m.get("rules_primary", "")])
        if not ELECTION.search(st):
            return None, "election_not_verified_US_roster_family"
        if re.search(r"\bnominee|\bprimary|\bnomination", text, re.I):
            return None, "primary_not_general_election_roster"
        if st == "KXBALANCEPOWERCOMBO" and ticker in POST_MIDTERM_CONTROL:
            return {"channel": "election", "issue_ids": "", "classification_text": title,
                    "classification_field": "verified_exact_post_midterm_organizational_control_contract",
                    "word_normalized": "", "subject": "",
                    "scope": "US_post_2026_organizational_control_Feb2027_not_election_win"}, None
        if not (re.search(r"\b2026\b|(?:-|[A-Z])26(?:-|$)", text) or "term beginning in 2027" in text):
            return None, "election_not_2026"
        if re.search(r"-28(?:-|$)|\b2028 election", ticker + " " + m.get("rules_primary", "")):
            return None, "election_other_cycle"
        return {"channel": "election", "issue_ids": "", "classification_text": title,
                "classification_field": "verified_US_family+economic_year", "word_normalized": "",
                "subject": "", "scope": "US_2026_general_election"}, None
    if not issues:
        return None, "no_issue_dictionary_match"
    if SPORT.search(title):
        return None, "off_topic_sports"
    if FOREIGN.search(title) and not US.search(title) and not ({"energy_iran", "trade_tariffs"} & set(issues)):
        return None, "foreign_only_macro_or_policy"
    if category not in {"politics", "economics", "financials", "commodities"}:
        return None, "outside_research_categories"
    if category in {"economics", "financials"} and not (US.search(title) or KNOWN_US_MACRO.search(st)):
        return None, "US_macro_context_unverified"
    if re.search(r"\b20(?:29|3\d|4\d)\b", title) and not re.search(r"\b202[678]\b", title):
        return None, "beyond_2028_horizon"
    channel = "policy" if category == "politics" or POLICY.search(title) else "macro"
    return {"channel": channel, "issue_ids": "|".join(issues), "classification_text": title,
            "classification_field": field, "word_normalized": "", "subject": "",
            "scope": "US_or_global_US_relevant_proxy"}, None


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    # Safe for opening in spreadsheet applications. Numeric negatives stay numeric.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_csv(path, rows, fields=None):
    rows = list(rows)
    if fields is None:
        fields = list(dict.fromkeys(k for row in rows for k in row)) or ["no_rows"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_value(row.get(k)) for k in fields})


def candle_paths(root, ticker, include_presettlement=False):
    paths = [root / "candles" / (ticker + ".json.gz")]
    if include_presettlement:
        paths.append(root / "candles_presettlement" / (ticker + ".json.gz"))
    return paths


def inventory(source_root, out, cfg, supplement_root=None):
    roots = [source_root] + ([supplement_root] if supplement_root else [])
    asof = int(read_json(source_root / "run_config.json")["asof_ts"])
    fingerprints = inventory_fingerprints(source_root, cfg, supplement_root)
    selected, seen, anchors, event_starts, counts, errors = {}, set(), {}, {}, Counter(), []
    exclusions = out / "excluded_markets.csv"
    with exclusions.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, ["ticker", "series_ticker", "event_ticker", "title", "reason"])
        writer.writeheader()
        for root in roots:
            for path in sorted((root / "discovery").glob("*.json.gz")):
                try:
                    payload = read_json(path)
                except (OSError, ValueError, EOFError) as exc:
                    errors.append({"path": str(path), "error": str(exc)})
                    continue
                series = payload.get("series") or {"ticker": payload.get("series_ticker", path.name.split(".")[0])}
                st = series.get("ticker", payload.get("series_ticker", ""))
                for m in payload.get("markets", []):
                    ticker, event = m.get("ticker"), m.get("event_ticker", "")
                    if not ticker or ticker in seen:
                        continue
                    seen.add(ticker)
                    counts["scanned_unique_markets"] += 1
                    created = timestamp(m.get("created_time"))
                    opened = timestamp(m.get("open_time"))
                    if created and created > asof or opened and opened > asof:
                        reason = "created_after_frozen_asof" if created and created > asof else "not_open_at_frozen_asof"
                        counts["excluded:" + reason] += 1
                        writer.writerow({"ticker": ticker, "series_ticker": st, "event_ticker": event,
                                         "title": csv_value(m.get("title")), "reason": reason})
                        continue
                    if created:
                        event_starts[event] = min(event_starts.get(event, created), created)
                    if str(series.get("category", "")).lower() == "mentions" or "MENTION" in st:
                        # Earliest close across all outcomes avoids target-specific early resolution.
                        ct = timestamp(m.get("close_time"))
                        if ct:
                            anchors[event] = min(anchors.get(event, ct), ct)
                    tag, reason = classify(m, series, cfg)
                    if tag is None:
                        counts["excluded:" + reason] += 1
                        writer.writerow({k: csv_value(v) for k, v in {"ticker": ticker, "series_ticker": st,
                                         "event_ticker": event, "title": m.get("title"), "reason": reason}.items()})
                        continue
                    face = number(m.get("notional_value_dollars"))
                    if face is None:
                        raw = number(m.get("notional_value"))
                        face = raw / 100 if raw is not None else None
                    selected[ticker] = {"ticker": ticker, "series_ticker": st, "event_ticker": event,
                        "title": m.get("title", ""), "yes_sub_title": m.get("yes_sub_title", ""),
                        "series_title": series.get("title", ""), "category": series.get("category", ""),
                        **tag, "status": m.get("status", ""), "eligibility_class": m.get("_eligibility_class", "unknown"),
                        "created_time": m.get("created_time", ""), "open_time": m.get("open_time", ""),
                        "close_time": m.get("close_time", ""), "settlement_ts": m.get("settlement_ts", ""),
                        "expected_expiration_time": m.get("expected_expiration_time", ""),
                        "snapshot_observed_utc": m.get("_retrieved_at", ""), "face_usd": face,
                        "rules_primary": m.get("rules_primary", ""), "source_metadata": str(path),
                        "source_tier": m.get("_source_tier", "unknown"),
                        "membership_confidence": payload.get("membership_confidence", "unknown")}
                    counts["selected:" + tag["channel"]] += 1
    needed = []
    present = {p.name.removesuffix(".json.gz") for root in roots for folder in ["candles", "candles_presettlement"]
               for p in (root / folder).glob("*.json.gz")}
    for m in selected.values():
        m["event_anchor_ts"] = anchors.get(m["event_ticker"]) if m["channel"] == "mention" else None
        m["event_created_time"] = iso(event_starts.get(m["event_ticker"]))
        m["event_anchor_utc"] = iso(m["event_anchor_ts"])
        m["anchor_basis"] = "earliest_contract_close_retrospective_proxy_not_verified_event_start" if m["channel"] == "mention" else ""
        m["candle_file_present"] = m["ticker"] in present
        # Include present files in the queue: root can check whether they contain the required window.
        needed.append({k: m[k] for k in ["ticker", "series_ticker", "event_ticker", "channel", "issue_ids", "candle_file_present"]} |
                      {"priority": {"mention": 1, "election": 2, "policy": 3, "macro": 4}[m["channel"]],
                       "required_lead_anchor_ts": m["event_anchor_ts"], "lead_hours": cfg["lead_hours"]})
    write_csv(out / "market_index.csv", selected.values())
    write_csv(out / "candles_needed.csv", sorted(needed, key=lambda r: (r["priority"], r["ticker"])))
    if fingerprints != inventory_fingerprints(source_root, cfg, supplement_root):
        errors.append({"error": "discovery_manifest_changed_during_inventory_rerun_required"})
    manifest = {"schema_version": 1, "stage": "inventory", "asof_ts": asof, "source_root": str(source_root.resolve()), **fingerprints,
                "classification_version": CLASSIFICATION_VERSION, "counts": dict(counts), "selected_markets": len(selected),
                "selected_events": len({m["event_ticker"] for m in selected.values()}),
                "candles_absent": sum(not m["candle_file_present"] for m in selected.values()),
                "errors": errors, "status": "partial_metadata_errors" if errors else "offline_inventory_complete",
                "universe_is_full_website_census": False, "topic_ids": [x["id"] for x in cfg["issues"]]}
    write_json(out / "selection_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=True), flush=True)
    return list(selected.values()), manifest


def price(row, side, tier):
    values = row.get(side) or {}
    if values.get("close_dollars") is not None:
        return number(values["close_dollars"])
    value = number(values.get("close"))
    if value is None:
        return None
    # Historical legacy plain OHLC is dollars. Live plain OHLC is cents.
    return value if tier == "historical" or row.get("_price_unit") == "dollars" else value / 100


def quote(row, face, tier, cfg):
    bid, ask = price(row, "yes_bid", tier), price(row, "yes_ask", tier)
    valid = face is not None and face > 0 and bid is not None and ask is not None and 0 <= bid <= ask <= face
    spread = (ask - bid) / face if valid else None
    tight = valid and spread <= cfg["max_spread_probability"]
    return {"bid_usd": bid, "ask_usd": ask, "mid_probability": (bid + ask) / (2 * face) if valid else None,
            "spread_probability": spread, "valid_two_sided": bool(valid), "usable_quote": bool(tight),
            "quote_flag": "usable" if tight else "wide_spread" if valid else "missing_crossed_or_out_of_bounds"}


def load_candles(m, roots, asof, cfg, include_presettlement=False):
    merged, artifacts, errors = {}, [], []
    for root in roots:
        for path in candle_paths(root, m["ticker"], include_presettlement=include_presettlement):
            if not path.exists():
                continue
            try:
                payload = read_json(path)
                artifacts.append({"path": str(path), "status": payload.get("status"), "errors": payload.get("errors", [])})
            except (OSError, ValueError, EOFError) as exc:
                errors.append(str(exc))
                continue
            tier = payload.get("source_tier", m.get("source_tier"))
            for raw in payload.get("hourly", []):
                t = timestamp(raw.get("end_period_ts"))
                if not t or t > asof or raw.get("_period_interval", 60) != 60:
                    continue
                if timestamp(m.get("open_time")) and t <= timestamp(m["open_time"]):
                    continue
                # No post-settlement observations; last close bar can straddle close and is flagged.
                settlement = timestamp(m.get("settlement_ts"))
                if settlement and t >= settlement:
                    continue
                q = quote(raw, m["face_usd"], raw.get("_source_tier", tier), cfg)
                item = {"end_ts": t, **q,
                        "volume_contracts": number(raw.get("volume_fp", raw.get("volume"))),
                        "open_interest_contracts": number(raw.get("open_interest_fp", raw.get("open_interest"))),
                        "boundary_partial": bool(raw.get("_partial_start") or raw.get("_partial_end") or raw.get("_boundary_only")),
                        "source_path": str(path)}
                prev = merged.get(t)
                # Prefer usable quotes; ties prefer supplement (later root), retaining original raw elsewhere.
                if prev is None or item["usable_quote"] or not prev["usable_quote"]:
                    merged[t] = item
    return sorted(merged.values(), key=lambda r: r["end_ts"]), artifacts, errors


def select_quote(rows, target, cfg, cutoff=None):
    candidates = [r for r in rows if r["end_ts"] <= target and (cutoff is None or r["end_ts"] < cutoff)
                  and r["usable_quote"] and r["spread_probability"] <= cfg["max_spread_probability"]]
    if not candidates:
        return {"target_utc": iso(target), "quote_status": "missing_usable_quote"}
    row = candidates[-1]
    age = target - row["end_ts"]
    if age > cfg["max_quote_age_hours"] * 3600:
        return {"target_utc": iso(target), "quote_status": "stale_observation", "quote_observed_utc": iso(row["end_ts"]), "age_hours": age / 3600}
    return {"target_utc": iso(target), "quote_status": "usable", "quote_observed_utc": iso(row["end_ts"]),
            "age_hours": age / 3600, "mid_probability": row["mid_probability"], "bid_usd": row["bid_usd"],
            "ask_usd": row["ask_usd"], "spread_probability": row["spread_probability"],
            "open_interest_contracts": row["open_interest_contracts"],
            "staleness_limit": "bar_age_only_actual_quote_update_time_unavailable"}


def period_label(ts, end):
    if ts is None:
        return "outside"
    if end - 14 * DAY <= ts < end:
        return "latest14d"
    if end - 42 * DAY <= ts < end - 14 * DAY:
        return "prior28d"
    return "outside"


def average(values):
    a = [v for v in values if v is not None]
    return statistics.mean(a) if a else None


def mention_summaries(observations, cfg):
    groups = defaultdict(lambda: defaultdict(dict))
    for r in observations:
        if r["period"] not in {"latest14d", "prior28d"} or r.get("quote_status") != "usable":
            continue
        for issue in r["issue_ids"].split("|"):
            key = (issue, r["series_ticker"], r["subject"], r["word_normalized"])
            # Duplicate event/word contracts are averaged, never treated as independent speeches.
            groups[key][r["period"]].setdefault(r["event_ticker"], []).append(r["mid_probability"])
    cohorts = []
    for key, periods in sorted(groups.items()):
        row = dict(zip(["issue_id", "series_ticker", "subject", "word_normalized"], key))
        for p in ["latest14d", "prior28d"]:
            event_values = [average(v) for v in periods[p].values()]
            row[p + "_events"] = len(event_values)
            row[p + "_mean_probability"] = average(event_values)
        row["matched_supported"] = min(row["latest14d_events"], row["prior28d_events"]) >= cfg["min_events_per_period"]
        row["change_pp"] = 100 * (row["latest14d_mean_probability"] - row["prior28d_mean_probability"]) if row["matched_supported"] else None
        row["interpretation"] = "expected_language_at_fixed_retrospective_lead_not_observed_salience"
        cohorts.append(row)
    summaries = []
    for issue in [x["id"] for x in cfg["issues"]]:
        issue_cohorts = [r for r in cohorts if r["issue_id"] == issue]
        matched = [r for r in issue_cohorts if r["matched_supported"]]
        row = {"issue_id": issue, "supported_matched_cohorts": len(matched), "cohort_count": len(issue_cohorts)}
        raw = {}
        for p in ["latest14d", "prior28d"]:
            events = defaultdict(list)
            for r in observations:
                if issue in r["issue_ids"].split("|") and r["period"] == p and r.get("quote_status") == "usable":
                    events[r["event_ticker"]].append(r["mid_probability"])
            raw[p] = [average(v) for v in events.values()]
            row[p + "_events"] = len(events)
            row[p + "_events_per_day"] = len(events) / (14 if p == "latest14d" else 28)
            row[p + "_raw_mean_probability"] = average(raw[p])
        raw_supported = min(len(raw["latest14d"]), len(raw["prior28d"])) >= cfg["min_events_per_period"]
        row["raw_change_pp"] = 100 * (average(raw["latest14d"]) - average(raw["prior28d"])) if raw_supported else None
        supported = len(matched) >= cfg["min_matched_cohorts"]
        row["matched_change_pp"] = average([r["change_pp"] for r in matched]) if supported else None
        row["matched_status"] = "supported_descriptive_only" if supported else "insufficient_comparable_cohorts"
        row["matching"] = "same_series+subject+literal_normalized_target_equal_cohort_weight"
        summaries.append(row)
    return cohorts, summaries


def analyze(markets, source_root, out, cfg, config, selection, supplement_root=None):
    roots = [source_root] + ([supplement_root] if supplement_root else [])
    asof = int(config["asof_ts"])
    end = asof // DAY * DAY  # completed UTC days only for period comparisons
    start = int(config["hourly_start_ts"])
    observations, snapshots, changes, coverage = [], defaultdict(list), defaultdict(list), []
    sensitivity = {0.05: [], 0.10: []}
    daily = defaultdict(lambda: {"markets": set(), "events": set(), "mids": defaultdict(list), "volumes": [], "ois": [], "hours": 0, "usable": 0, "boundary": 0})
    listing = defaultdict(set)
    needed = []
    for idx, m in enumerate(markets):
        rows, artifacts, errors = load_candles(m, roots, asof, cfg)
        created = timestamp(m.get("event_created_time") or m.get("created_time"))
        for issue in filter(None, m["issue_ids"].split("|")):
            if created:
                listing[(issue, m["channel"], datetime.fromtimestamp(created, UTC).date().isoformat())].add(m["event_ticker"])
        cov = {"ticker": m["ticker"], "channel": m["channel"], "issue_ids": m["issue_ids"],
               "artifact_count": len(artifacts), "artifact_statuses": "|".join(str(a["status"]) for a in artifacts),
               "artifact_errors": sum(len(a["errors"]) for a in artifacts), "read_errors": "|".join(errors),
               "hourly_rows": len(rows), "usable_two_sided_hours": sum(r["usable_quote"] for r in rows),
               "first_observed_utc": iso(rows[0]["end_ts"]) if rows else "", "last_observed_utc": iso(rows[-1]["end_ts"]) if rows else "",
               "coverage_status": "observed_partial_or_complete_see_counts" if rows else "no_hourly_rows"}
        effective_start = max(start, timestamp(m.get("open_time")) or start)
        effective_end = min(asof, timestamp(m.get("close_time")) or asof, timestamp(m.get("settlement_ts")) or asof)
        expected_hours = max(0, (effective_end // 3600) - (effective_start // 3600))
        observed_common = sum(effective_start < r["end_ts"] <= effective_end for r in rows)
        cov["common_window_nominal_hours"] = expected_hours
        cov["common_window_observed_hours"] = observed_common
        cov["common_window_unobserved_nominal_hours"] = max(0, expected_hours - observed_common)
        cov["missing_or_invalid_two_sided_hours"] = sum(not r["usable_quote"] for r in rows)
        coverage.append(cov)
        stop_times = [t for t in [timestamp(m.get("close_time")), timestamp(m.get("settlement_ts"))] if t is not None]
        cutoff = min(stop_times) if stop_times else None
        if m["channel"] == "mention":
            anchor = number(m.get("event_anchor_ts"))
            if anchor:
                target = int(anchor) - cfg["lead_hours"] * 3600
                too_late = (timestamp(m.get("open_time")) or timestamp(m.get("created_time")) or 0) > target
                if too_late:
                    sample = {"quote_status": "lead_unavailable_listed_too_late"}
                else:
                    sample = select_quote(rows, min(target, asof), cfg, cutoff) if target <= asof else {"quote_status": "lead_target_not_yet_observed"}
                obs = {k: m[k] for k in ["ticker", "series_ticker", "event_ticker", "subject", "word_normalized", "issue_ids", "anchor_basis"]}
                obs.update({"event_anchor_utc": iso(int(anchor)), "lead_target_utc": iso(target), "period": period_label(int(anchor), end), **sample})
                observations.append(obs)
                for spread, alternative in sensitivity.items():
                    alt_cfg = dict(cfg, max_spread_probability=spread)
                    alt = sample if too_late else select_quote(rows, target, alt_cfg, cutoff) if target <= asof else {"quote_status": "lead_target_not_yet_observed"}
                    alternative.append({k: v for k, v in obs.items() if k not in sample} | alt)
                if not too_late and obs["quote_status"] != "usable" and end - 42 * DAY <= anchor < end:
                    needed.append({"ticker": m["ticker"], "series_ticker": m["series_ticker"], "event_ticker": m["event_ticker"],
                                   "channel": "mention", "issue_ids": m["issue_ids"], "priority": 1,
                                   "start_ts": target - cfg["max_quote_age_hours"] * 3600, "end_ts": target,
                                   "reason": obs["quote_status"]})
        if m["channel"] in {"policy", "election", "macro"}:
            sample = select_quote(rows, asof, cfg, cutoff) if cutoff is None or cutoff > asof else {"quote_status": "closed_before_frozen_asof"}
            base = {k: m[k] for k in ["ticker", "series_ticker", "event_ticker", "title", "yes_sub_title", "issue_ids", "status", "scope"]}
            snapshots[m["channel"]].append(base | sample)
            prior_missing = False
            for days in [7, 14, 28]:
                prior = select_quote(rows, asof - days * DAY, cfg, cutoff)
                prior_missing = prior_missing or prior.get("quote_status") != "usable"
                good = sample.get("quote_status") == "usable" and prior.get("quote_status") == "usable"
                changes[m["channel"]].append(base | {"lookback_days": days, "current_status": sample.get("quote_status"),
                    "prior_status": prior.get("quote_status"), "current_mid": sample.get("mid_probability"),
                    "prior_mid": prior.get("mid_probability"), "change_pp": 100 * (sample["mid_probability"] - prior["mid_probability"]) if good else None,
                    "current_observed_utc": sample.get("quote_observed_utc"), "prior_observed_utc": prior.get("quote_observed_utc"),
                    "comparison_status": "same_contract_descriptive" if good else "not_comparable"})
            if sample.get("quote_status") != "closed_before_frozen_asof" and (sample.get("quote_status") != "usable" or prior_missing):
                needed.append({"ticker": m["ticker"], "series_ticker": m["series_ticker"], "event_ticker": m["event_ticker"],
                               "channel": m["channel"], "issue_ids": m["issue_ids"], "priority": 2 if m["channel"] == "election" else 3,
                               "start_ts": asof - 30 * DAY, "end_ts": asof, "reason": "missing_current_or_lookback_quote"})
        by_day = defaultdict(list)
        for row in rows:
            if row["end_ts"] <= start or row["end_ts"] > end or cutoff is not None and row["end_ts"] >= cutoff:
                continue
            day = datetime.fromtimestamp(row["end_ts"] - 1, UTC).date().isoformat()
            by_day[day].append(row)
        for day, day_rows in by_day.items():
            valid = [r for r in day_rows if r["usable_quote"]]
            last = valid[-1] if valid else None
            oi_values = [r for r in day_rows if r["open_interest_contracts"] is not None]
            for issue in filter(None, m["issue_ids"].split("|")):
                acc = daily[(issue, m["channel"], day)]
                acc["markets"].add(m["ticker"])
                acc["events"].add(m["event_ticker"])
                if last:
                    acc["mids"][m["event_ticker"]].append(last["mid_probability"])
                acc["volumes"].extend(r["volume_contracts"] for r in day_rows if r["volume_contracts"] is not None)
                if oi_values:
                    acc["ois"].append(oi_values[-1]["open_interest_contracts"])
                acc["hours"] += len(day_rows)
                acc["usable"] += len(valid)
                acc["boundary"] += sum(r["boundary_partial"] for r in day_rows)
        if (idx + 1) % 500 == 0:
            print(json.dumps({"stage": "analyze", "processed_markets": idx + 1, "total": len(markets)}), flush=True)
    cohorts, summaries = mention_summaries(observations, cfg)
    daily_rows = []
    for key in listing:
        day_start = timestamp(key[2] + "T00:00:00Z")
        if day_start is not None and start // DAY * DAY <= day_start < end:
            daily[key]  # Emit listing-only days with missing measures, never fabricated prices/volume.
    for (issue, channel, day), acc in sorted(daily.items()):
        daily_rows.append({"issue_id": issue, "channel": channel, "utc_date": day,
            "observed_markets": len(acc["markets"]), "observed_events": len(acc["events"]),
            "quoted_events": len(acc["mids"]), "observed_hourly_rows": acc["hours"], "usable_quote_hours": acc["usable"],
            "event_equal_weight_mid_diagnostic": average([average(v) for v in acc["mids"].values()]),
            "volume_observed_contracts": sum(acc["volumes"]) if acc["volumes"] else None,
            "oi_sum_latest_observed_per_contract": sum(acc["ois"]) if acc["ois"] else None,
            "oi_contracts_observed": len(acc["ois"]), "partial_boundary_rows": acc["boundary"],
            "newly_listed_events": len(listing.get((issue, channel, day), set())),
            "interpretation": "changing_coverage_not_matched_salience_or_probability_index; OI_not_positions"})
    for summary in summaries:
        issue = summary["issue_id"]
        ms = [m for m in markets if issue in m["issue_ids"].split("|")]
        summary["selected_markets"] = len(ms)
        summary["selected_events"] = len({m["event_ticker"] for m in ms})
        for p in ["latest14d", "prior28d"]:
            listed = {m["event_ticker"] for m in ms if period_label(timestamp(m.get("event_created_time") or m.get("created_time")), end) == p}
            summary[p + "_newly_listed_events"] = len(listed)
            summary[p + "_newly_listed_events_per_day"] = len(listed) / (14 if p == "latest14d" else 28)
        summary["inference"] = "No voter_salience_positioning_causal_or_equity_inference"
    write_csv(out / "mention_observations.csv", observations)
    write_csv(out / "mention_cohorts.csv", cohorts)
    robustness = [dict(r, max_spread_probability=cfg["max_spread_probability"]) for r in summaries]
    for spread, alternative in sensitivity.items():
        _, alt_summary = mention_summaries(alternative, dict(cfg, max_spread_probability=spread))
        robustness.extend(dict(r, max_spread_probability=spread) for r in alt_summary)
    write_csv(out / "mention_robustness.csv", robustness)
    write_csv(out / "issue_summary.csv", summaries)
    for channel in ["election", "policy", "macro"]:
        write_csv(out / (channel + "_snapshot.csv"), snapshots[channel])
        write_csv(out / (channel + "_changes.csv"), changes[channel])
    write_csv(out / "coverage.csv", coverage)
    write_csv(out / "daily_issue_series.csv", daily_rows)
    weekly = defaultdict(list)
    for row in daily_rows:
        dt = datetime.fromisoformat(row["utc_date"])
        monday = (dt - timedelta(days=dt.weekday())).date().isoformat()
        weekly[(row["issue_id"], row["channel"], monday)].append(row)
    weekly_rows = []
    for (issue, channel, week), rs in sorted(weekly.items()):
        last_observed = next((r for r in reversed(rs) if r["observed_hourly_rows"] > 0), {})
        weekly_rows.append({"issue_id": issue, "channel": channel, "week_start_utc": week,
         "observed_days": sum(r["observed_hourly_rows"] > 0 for r in rs),
         "newly_listed_events": sum(r["newly_listed_events"] for r in rs),
         "volume_observed_contracts": sum(r["volume_observed_contracts"] for r in rs if r["volume_observed_contracts"] is not None) if any(r["volume_observed_contracts"] is not None for r in rs) else None,
         "last_observed_day": last_observed.get("utc_date"), "oi_at_last_observed_day": last_observed.get("oi_sum_latest_observed_per_contract"),
         "interpretation": "OI_stock_not_summed_across_days; partial_weeks_and_changing_coverage"})
    write_csv(out / "weekly_issue_series.csv", weekly_rows)
    write_csv(out / "candles_needed.csv", needed, ["ticker", "series_ticker", "event_ticker", "channel", "issue_ids", "priority", "start_ts", "end_ts", "reason"])
    assumptions = [
        "Mention metrics are expected literal language, not realized mentions, voter salience or public opinion.",
        "Fixed lead uses earliest close across all known event contracts, a retrospective proxy; scheduled speech start and contemporaneous schedule vintages are unavailable.",
        "Comparisons are same series+extracted subject+literal normalized word; unmatched cohorts reported separately; min counts configured.",
        "Prices are two-sided midpoints divided by stated face value; wide/crossed/missing quotes excluded; bar age cannot identify unchanged stale underlying quotes.",
        "Frozen asof applies to candle end timestamps. Later metadata snapshot quotes are never substituted into frozen price estimates.",
        "Core analysis reads only common-window candle artifacts. Presettlement extensions remain separate and are excluded from coverage, quotes and trends.",
        "Common-window daily aggregation starts at the exact configured calendar-month timestamp; it is not shortened to 60 days.",
        "Markets created or not yet opened after frozen asof are excluded; later-retrieved definitions remain a metadata-vintage limitation.",
        "Ticker identities are deduplicated; event observations use exact event_ticker. Semantically overlapping contracts across different series are not assumed identical.",
        "Daily rows use UTC (endtimestamp-1 second), no synthetic fills; volume is full observed API intervals and boundary overlap is flagged.",
        "Open interest is latest observed stock per contract per day, summed cross-sectionally only; not trader positioning or conviction.",
        "Issue overlap makes cross-issue totals nonadditive. Changing coverage and listing composition can move raw metrics.",
        "No marginal probabilities are divided to produce conditional policy/control probabilities.",
        "Election roster is a verified-family subset, not a full official 2026 election census; rules and organizational control can differ.",
        "No historical trader positions, orderbook depth, actual transcript mentions, polling or equity evidence is created by this module.",
    ]
    manifest = {"schema_version": 1, "stage": "analyze", "status": "descriptive_partial_coverage",
        "asof_utc": config["asof_utc"], "common_window_start_utc": iso(start), "candle_dataset": "common_only",
        "presettlement_extensions_included": False,
        "completed_utc_days_end": iso(end), "latest_period_start": iso(end - 14 * DAY),
        "prior_period_start": iso(end - 42 * DAY), "selected_markets": len(markets),
        "inventory_selected_markets": selection.get("selected_markets"),
        "analysis_scope": selection.get("analysis_scope", "all_selected_inventory_markets"),
        "markets_with_hourly_rows": sum(r["hourly_rows"] > 0 for r in coverage),
        "markets_without_artifacts": sum(r["artifact_count"] == 0 for r in coverage),
        "markets_with_artifacts_but_no_hourly_rows": sum(r["artifact_count"] > 0 and r["hourly_rows"] == 0 for r in coverage),
        "artifact_error_count": sum(r["artifact_errors"] for r in coverage),
        "artifact_read_error_markets": sum(bool(r["read_errors"]) for r in coverage),
        "mention_observations": len(observations), "usable_mention_observations": sum(r.get("quote_status") == "usable" for r in observations),
        "mention_quote_status_counts": dict(Counter(r.get("quote_status") for r in observations)),
        "supported_matched_cohorts": sum(r["matched_supported"] for r in cohorts), "priority_requests": len(needed),
        "inventory_status": selection["status"], "assumptions": assumptions,
        "source_discovery_manifest_sha256": selection.get("source_discovery_manifest_sha256"),
        "selection_config_sha256": selection.get("selection_config_sha256"),
        "config_sha256": config_hash(cfg)}
    write_json(out / "analysis_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=True), flush=True)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--supplement-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stage", choices=["inventory", "analyze"], default="analyze")
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--market-list", type=Path, help="Analyze only tickers in this CSV (ticker column); full inventory is preserved")
    args = parser.parse_args(argv)
    source, out = args.source_root.resolve(), args.out.resolve()
    roots = [source] + ([args.supplement_root.resolve()] if args.supplement_root else [])
    if any(out == r or r in out.parents for r in roots):
        parser.error("--out must be outside source/supplement caches; these are read-only inputs")
    out.mkdir(parents=True, exist_ok=True)
    cfg, frozen = load_config(args.config), read_json(source / "run_config.json")
    selection_path, index_path = out / "selection_manifest.json", out / "market_index.csv"
    selection = read_json(selection_path) if selection_path.exists() else {}
    if args.stage == "analyze" and index_path.exists() and inventory_is_current(selection, source, cfg, frozen["asof_ts"], args.supplement_root):
        with index_path.open(encoding="utf-8-sig", newline="") as f:
            markets = list(csv.DictReader(f))
        for m in markets:
            m["face_usd"] = number(m.get("face_usd"))
            m["event_anchor_ts"] = number(m.get("event_anchor_ts"))
    else:
        markets, selection = inventory(source, out, cfg, args.supplement_root)
    if args.stage == "analyze":
        if args.market_list:
            with args.market_list.open(encoding="utf-8-sig", newline="") as f:
                requested = {r["ticker"] for r in csv.DictReader(f)}
            known = {m["ticker"] for m in markets}
            unknown = requested - known
            if unknown:
                parser.error(f"--market-list contains {len(unknown)} tickers outside inventory: {sorted(unknown)[:5]}")
            markets = [m for m in markets if m["ticker"] in requested]
            selection = dict(selection, analysis_scope={"market_list": str(args.market_list.resolve()),
                "requested_unique_tickers": len(requested), "scope_is_restricted": True,
                "market_list_sha256": hashlib.sha256(args.market_list.read_bytes()).hexdigest()})
        write_csv(out / "analysis_market_index.csv", markets)
        analyze(markets, source, out, cfg, frozen, selection, args.supplement_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
