"""Normalize venue metadata without guessing units or inventing book prices."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .topics import TopicClassifier


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def probability(value: Any) -> float | None:
    result = number(value)
    return result if result is not None and 0 <= result <= 1 else None


def first_present(record: dict, *keys: str) -> Any:
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def _array(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _context(market: dict, event: dict) -> str:
    headlines = ("question", "title", "subtitle", "sub_title", "yes_sub_title", "groupItemTitle", "series_title", "series_context", "country")
    details = ("description", "rules_primary", "rules_secondary", "category")
    values = [str(record[key]) for fields in (headlines, details) for record in (market, event) for key in fields if record.get(key)]
    for record in (market, event):
        values.extend(str(tag.get("label", "")) for tag in record.get("tags", []) if isinstance(tag, dict))
    return "\n".join(values)


def _book_flags(bid: float | None, ask: float | None) -> tuple[float | None, list[str]]:
    if bid is None and ask is None:
        return None, ["missing_orderbook"]
    if bid is None or ask is None:
        return None, ["one_sided_orderbook"]
    if bid > ask:
        return None, ["crossed_orderbook"]
    return float(Decimal(str(ask)) - Decimal(str(bid))), []


def _base(platform: str, market: dict, event: dict, title: str, observed_at: str | None, classifier: TopicClassifier | None) -> dict:
    labels = (classifier or TopicClassifier()).classify(_context(market, event))
    return {
        "platform": platform, "title": title, "observed_at": observed_at or utc_now(),
        "topics": labels["topics"], "matched_terms": labels["matched_terms"],
        "sectors": labels["sectors"], "relevance_score": labels["relevance_score"],
        "exclude_reason": labels["exclude_reason"],
    }


def normalize_polymarket(market: dict, event: dict | None = None, observed_at: str | None = None, classifier: TopicClassifier | None = None) -> list[dict]:
    """One row per Gamma outcome/token; supports older CLOB token metadata too.

    outcomePrices is a venue-provided displayed estimate, not an executable
    price. Gamma bestBid/bestAsk is used only for the primary Yes outcome of
    the standard [Yes, No] shape. We never copy those quotes onto other tokens.
    Use token-specific CLOB books for executable quotes and depth.
    """
    if event is None:
        events = market.get("events") or []
        event = events[0] if events and isinstance(events[0], dict) else {}
    outcomes, prices, token_ids = (_array(market.get(key)) for key in ("outcomes", "outcomePrices", "clobTokenIds"))
    tokens = market.get("tokens") or []
    if not outcomes and tokens:
        outcomes = [token.get("outcome") for token in tokens]
        prices = [token.get("price") for token in tokens]
        token_ids = [token.get("token_id") for token in tokens]
    count = max(len(outcomes), len(token_ids), len(prices), 1)
    title = str(first_present(market, "question", "title") or event.get("title") or "")
    base = _base("polymarket", market, event, title, observed_at, classifier)
    base.update({
        "market_id": str(first_present(market, "id", "conditionId", "condition_id")) if first_present(market, "id", "conditionId", "condition_id") is not None else "",
        "condition_id": first_present(market, "conditionId", "condition_id"),
        "event_id": str(event["id"]) if event.get("id") is not None else None,
        "series_id": first_present(event, "seriesSlug", "series_id"),
        "volume": number(first_present(market, "volumeNum", "volume")), "volume_unit": "USD_notional",
        "volume_24h": number(market.get("volume24hr")),
        "liquidity": number(first_present(market, "liquidityNum", "liquidity")), "liquidity_unit": "USD",
        "end_date": first_present(market, "endDate", "end_date_iso"),
        "rules": str(market.get("description") or ""), "resolution_source": market.get("resolutionSource"),
        "status": "closed" if market.get("closed") else ("active" if market.get("active") else None),
        "source_updated_at": first_present(market, "updatedAt", "updated_at"),
    })
    rows = []
    binary_yes_first = [str(value).casefold() for value in outcomes] == ["yes", "no"]
    unavailable_flags = []
    for field, flag, blocked_value in (
        ("active", "market_inactive", False),
        ("acceptingOrders", "not_accepting_orders", False),
        ("enableOrderBook", "orderbook_disabled", False),
        ("closed", "market_closed", True),
        ("archived", "market_archived", True),
    ):
        if market.get(field) is blocked_value:
            unavailable_flags.append(flag)
    base["active"] = market.get("active")
    base["accepting_orders"] = market.get("acceptingOrders")
    for index in range(count):
        flags = list(unavailable_flags)
        if len(outcomes) != len(token_ids) or (prices and len(prices) != len(outcomes)):
            flags.append("outcome_array_length_mismatch")
        outcome = outcomes[index] if index < len(outcomes) else None
        token_id = token_ids[index] if index < len(token_ids) else None
        raw_price = prices[index] if index < len(prices) else None
        estimate = probability(raw_price)
        if raw_price is not None and estimate is None:
            flags.append("invalid_outcome_price")
        bid = probability(market.get("bestBid")) if index == 0 and binary_yes_first else None
        ask = probability(market.get("bestAsk")) if index == 0 and binary_yes_first else None
        spread, book_flags = _book_flags(bid, ask)
        flags.extend(book_flags)
        # Gamma can expose 0/1 sentinels on unfunded/inactive placeholders,
        # even with acceptingOrders=true. They do not establish a midpoint.
        boundary_book = bid == 0 or ask == 1
        if boundary_book:
            flags.append("boundary_quotes_unverified")
        source = "gamma_outcome_price" if estimate is not None else None
        if tokens and not market.get("outcomes") and estimate is not None:
            source = "clob_token_price"
        if estimate is None and spread is not None and not unavailable_flags and not boundary_book:
            estimate = float((Decimal(str(bid)) + Decimal(str(ask))) / 2)
            source = "gamma_yes_book_midpoint"
        if outcome is None:
            flags.append("missing_outcome_label")
        if token_id is None:
            flags.append("missing_token_id")
        rows.append({**base, "outcome": str(outcome) if outcome is not None else None,
                     "token_id": str(token_id) if token_id is not None else None,
                     "probability": estimate, "price_source": source,
                     "bid": bid, "ask": ask, "spread": spread, "quality_flags": sorted(set(flags))})
    return rows


def _kalshi_price(market: dict, field: str) -> float | None:
    """Prefer explicit dollars, otherwise legacy cents; never magnitude-guess."""
    dollars = field + "_dollars"
    if dollars in market:
        return probability(market[dollars])
    if field in market:
        value = number(market[field])
        return probability(Decimal(str(value)) / 100) if value is not None else None
    return None


def normalize_kalshi(market: dict, event: dict | None = None, observed_at: str | None = None, classifier: TopicClassifier | None = None) -> list[dict]:
    """One YES row per binary market; scalar contracts are flagged and unpriced.

    Prefers valid two-sided midpoint, otherwise a separately labelled last
    trade. A missing side is not zero; crossed books never form a midpoint.
    Deprecated Kalshi liquidity fields are retained in raw data, not treated
    as a depth measure. Fixed dollar/quantity fields take precedence.
    """
    event = event or {}
    title = str(market.get("title") or event.get("title") or market.get("ticker") or "")
    subtitle = str(market.get("yes_sub_title") or market.get("subtitle") or "")
    if subtitle and subtitle.casefold() not in title.casefold():
        title += " — " + subtitle
    base = _base("kalshi", market, event, title, observed_at, classifier)
    bid, ask = _kalshi_price(market, "yes_bid"), _kalshi_price(market, "yes_ask")
    if number(first_present(market, "yes_bid_size_fp", "yes_bid_size")) == 0:
        bid = None
    if number(first_present(market, "yes_ask_size_fp", "yes_ask_size")) == 0:
        ask = None
    # Some payloads only expose opposite-side bids. Their complement is an ask.
    if "yes_ask_dollars" not in market and "yes_ask" not in market:
        no_bid = _kalshi_price(market, "no_bid")
        no_size = number(first_present(market, "no_bid_size_fp", "no_bid_size"))
        yes_ask_size = number(first_present(market, "yes_ask_size_fp", "yes_ask_size"))
        if no_bid is not None and no_size != 0 and yes_ask_size != 0:
            ask = float(Decimal(1) - Decimal(str(no_bid)))
    spread, flags = _book_flags(bid, ask)
    estimate = None
    source = None
    kind = market.get("market_type", "binary")
    if kind not in ("binary", None, ""):
        flags.append("nonbinary_contract_price_is_not_probability")
    elif market.get("result") in ("yes", "no") and market.get("status") in ("determined", "finalized", "settled"):
        estimate = 1.0 if market["result"] == "yes" else 0.0
        source = "settlement_result"
    elif spread is not None:
        estimate = float((Decimal(str(bid)) + Decimal(str(ask))) / 2)
        source = "yes_book_midpoint"
    else:
        estimate = _kalshi_price(market, "last_price")
        source = "last_trade" if estimate is not None else None
        if source:
            flags.append("last_trade_may_be_stale")
    flags.append("kalshi_liquidity_deprecated")
    base.update({
        "market_id": str(market.get("ticker") or ""), "event_id": first_present(market, "event_ticker") or event.get("event_ticker"),
        "series_id": first_present(market, "series_ticker") or event.get("series_ticker"),
        "outcome": "Yes", "outcome_label": market.get("yes_sub_title"), "token_id": None,
        "probability": estimate, "price_source": source, "bid": bid, "ask": ask, "spread": spread,
        "volume": number(first_present(market, "volume_fp", "volume")), "volume_unit": "contracts",
        "volume_24h": number(first_present(market, "volume_24h_fp", "volume_24h")),
        "open_interest": number(first_present(market, "open_interest_fp", "open_interest")),
        "liquidity": None, "liquidity_unit": None,
        "end_date": first_present(market, "close_time", "expected_expiration_time", "expiration_time", "latest_expiration_time"),
        "rules": "\n\n".join(str(market[key]) for key in ("rules_primary", "rules_secondary") if market.get(key)),
        "status": market.get("status"), "market_type": kind,
        "source_updated_at": market.get("updated_time"), "quality_flags": sorted(set(flags)),
    })
    return [base]
