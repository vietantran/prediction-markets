"""Research triage from explicit snapshots, not an investment recommendation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from .normalize import number, probability


def row_key(row: dict) -> tuple[str, str, str]:
    return str(row.get("platform") or ""), str(row.get("market_id") or ""), str(row.get("outcome") or "")


def _date(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _time_range(rows: list[dict]) -> dict:
    times = sorted(parsed for row in rows if (parsed := _date(row.get("observed_at"))) is not None)
    return {"earliest": times[0].isoformat() if times else None, "latest": times[-1].isoformat() if times else None}


def _identity(row: dict) -> dict:
    return {key: row.get(key) for key in ("platform", "market_id", "event_id", "title", "outcome", "token_id", "topics")}


def analyze(current_rows: Iterable[dict], previous_rows: Iterable[dict] | None = None, *, minimum_move_pp: float = 3.0, wide_spread: float = 0.10, thin_volume: dict[str, float] | None = None) -> dict:
    """Compare snapshots only by venue + market ID + outcome.

    Price changes require increasing timezone-aware observation times, matching
    price source, token, title and rules. Counts summarize coverage of unique
    markets; probabilities across markets are never combined. Thin-volume
    defaults are triage heuristics in each venue's own units, not comparable
    liquidity estimates. Book quality overrides a seemingly precise price.
    """
    current_list = list(current_rows)
    previous_list = list(previous_rows) if previous_rows is not None else []
    current_counts = Counter(row_key(row) for row in current_list)
    previous_counts = Counter(row_key(row) for row in previous_list)
    current = {row_key(row): row for row in current_list}
    previous = {row_key(row): row for row in previous_list}
    thresholds = thin_volume or {"polymarket": 1000.0, "kalshi": 1000.0}
    changes, skipped, watchlist = [], [], []
    market_topics: dict[tuple[str, str], set[str]] = {}
    for key, row in current.items():
        platform, market_id, _ = key
        if not platform or not market_id:
            skipped.append({**_identity(row), "reason": "missing_stable_market_identity"})
            continue
        market_topics.setdefault((platform, market_id), set()).update(row.get("topics") or [])
        flags = set(row.get("quality_flags") or [])
        reasons = []
        estimate = probability(row.get("probability"))
        spread = number(row.get("spread"))
        if spread is not None and spread >= wide_spread:
            flags.add("wide_spread")
        volume = number(row.get("volume"))
        if volume is not None and platform in thresholds and volume < thresholds[platform]:
            flags.add("low_cumulative_volume")
        if estimate is None:
            flags.add("missing_probability")
        if current_counts[key] > 1 or previous_counts[key] > 1:
            flags.add("duplicate_snapshot_identity")
        delta = None
        old = previous.get(key)
        if old is not None:
            now_time, old_time = _date(row.get("observed_at")), _date(old.get("observed_at"))
            previous_estimate = probability(old.get("probability"))
            invalid = None
            if current_counts[key] > 1 or previous_counts[key] > 1:
                invalid = "duplicate_snapshot_identity"
            elif now_time is None or old_time is None or now_time <= old_time:
                invalid = "missing_or_nonincreasing_observation_time"
            elif estimate is None or previous_estimate is None:
                invalid = "missing_valid_price"
            elif not row.get("price_source") or row.get("price_source") != old.get("price_source"):
                invalid = "price_source_changed_or_missing"
            elif any(row.get(field) != old.get(field) for field in ("token_id", "title", "rules")):
                invalid = "contract_definition_changed"
            if invalid:
                skipped.append({**_identity(row), "reason": invalid})
            else:
                delta = float((Decimal(str(estimate)) - Decimal(str(previous_estimate))) * 100)
                changes.append({**_identity(row), "previous_probability": previous_estimate,
                                "probability": estimate, "change_pp": delta,
                                "previous_observed_at": old.get("observed_at"), "observed_at": row.get("observed_at"),
                                "price_source": row.get("price_source"), "quality_flags": sorted(flags)})
                if abs(delta) >= minimum_move_pp:
                    reasons.append("price_move_requires_review")
        elif previous_rows is not None:
            reasons.append("new_in_collected_coverage")
        if "midterms_2026" in (row.get("topics") or []):
            reasons.append("2026_midterm_exposure")
        issue_topics = [topic for topic in row.get("topics") or [] if topic not in {"midterms_2026", "us_politics", "us_macro", "fed_monetary_policy"}]
        if issue_topics:
            reasons.append("investment_issue_keyword_evidence")
        if reasons or row.get("topics"):
            watchlist.append({**_identity(row), "probability": estimate, "change_pp": delta,
                              "bid": row.get("bid"), "ask": row.get("ask"), "spread": spread,
                              "volume": volume, "volume_unit": row.get("volume_unit"),
                              "sectors": row.get("sectors") or [], "matched_terms": row.get("matched_terms") or {},
                              "price_source": row.get("price_source"), "observed_at": row.get("observed_at"),
                              "reasons": reasons or ["target_topic_coverage"], "quality_flags": sorted(flags)})
    watchlist.sort(key=lambda row: ("2026_midterm_exposure" in row["reasons"], abs(row["change_pp"] or 0)), reverse=True)
    changes.sort(key=lambda row: abs(row["change_pp"]), reverse=True)
    counts = Counter(topic for topics in market_topics.values() for topic in topics)
    current_market_ids = set(market_topics)
    previous_market_ids = {(row.get("platform"), row.get("market_id")) for row in previous.values()}
    return {
        "snapshot_times": {"current": _time_range(current_list), "previous": _time_range(previous_list)},
        "comparison_requested": previous_rows is not None,
        "current_outcome_count": len(current), "current_market_count": len(current_market_ids),
        "topic_market_counts": dict(sorted(counts.items())),
        "new_coverage": [_identity(current[key]) for key in sorted(current.keys() - previous.keys())] if previous_rows is not None else [],
        "disappeared_coverage": [_identity(previous[key]) for key in sorted(previous.keys() - current.keys())] if previous_rows is not None else [],
        "new_market_count": len(current_market_ids - previous_market_ids) if previous_rows is not None else None,
        "changes": changes, "skipped_comparisons": skipped, "watchlist": watchlist,
        "thresholds": {"minimum_move_pp": minimum_move_pp, "wide_spread": wide_spread, "thin_cumulative_volume_by_venue": thresholds},
        "interpretation": [
            "Prices are market-implied estimates; watchlist entries are research leads, not forecasts of stock returns.",
            "Keyword and sector links are hypotheses requiring verification against resolution rules, campaign statements and policy documents.",
            "New or disappeared coverage may reflect query limits, listing changes, pagination or collection errors; it does not establish public attention or traction.",
            "Do not add, average or volume-weight heterogeneous market probabilities. Volume units differ between venues.",
            "No cross-venue equivalence or arbitrage is inferred; compare resolution wording, timing, fees and executable depth manually.",
            "A shift in political probability alone does not identify a policy's causal effect on equities. Missing issue markets are coverage gaps, not zero probabilities.",
        ],
    }
