"""Discovery and selected-market extraction, retaining all API responses in RunStore."""
from __future__ import annotations

import json
import re
from itertools import zip_longest
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .http import APIError, utc_now
from .normalize import normalize_kalshi, normalize_polymarket, number
from .storage import write_csv, write_json, write_jsonl


def _attempt(store, operation, fn, default=None):
    try:
        return fn()
    except (APIError, ValueError, RuntimeError) as exc:
        store.error(operation, exc)
        return default


def _series_context(series):
    text = " ".join(str(series.get(k) or "") for k in ("title", "ticker", "category", "tags"))
    official_us_sources = {"bls.gov", "bea.gov", "census.gov", "federalreserve.gov",
                           "congress.gov", "senate.gov", "house.gov", "fec.gov"}
    for source in series.get("settlement_sources") or []:
        host = (urlsplit(source.get("url", "")).hostname or "").removeprefix("www.")
        if any(host == domain or host.endswith("." + domain) for domain in official_us_sources):
            text += " United States official settlement source " + str(source.get("name") or "")
            break
    return text


def _midterm_series_candidate(series, context, classification):
    """Coarse series gate only; the actual event must still prove its 2026 cycle."""
    tags = " ".join(str(t) for t in series.get("tags") or [])
    if re.search(r"international|foreign", tags, re.I):
        return False
    if classification.get("exclude_reason") in {"sports_context", "foreign_context_without_explicit_us_link"}:
        return False
    title = series.get("title") or ""
    election_family = re.search(r"\b(?:house|senate|congressional|governor|gubernatorial|midterms?|redistricting|generic ballot)\b", title, re.I)
    election_context = (str(series.get("category") or "").lower() == "elections" or
                        bool(re.search(r"US Elections|midterms?|\b(?:house|senate)\b.*\b(?:control|winner|election|race)\b", context, re.I)))
    return bool(election_family and election_context)


def _kalshi_series_priority(series, midterm_candidate):
    """Prioritize chamber outcomes and district coverage before related politics.

    Volume only distinguishes used templates from empty legacy templates; it is
    not a measure of race competitiveness. Event books provide that later.
    """
    if not midterm_candidate:
        return 0
    title = str(series.get("title") or "").strip().rstrip("?")
    volume = number(series.get("volume_fp", series.get("volume")))
    if volume == 0:
        return 1
    if re.fullmatch(r"(?:US\s+|U\.S\.\s+)?(?:House(?: of Representatives)?|Senate)\s+(?:winner|control)", title, re.I):
        return 6
    if re.fullmatch(r"(?:House|Senate)\s+Race\s+Winner", title, re.I):
        return 5
    if re.search(r"\b(?:primary|primaries|nominee|nomination|combo|endorse|runoff|resign|expel)\b", title, re.I):
        return 2
    return 4


def _kalshi_event_priority(event):
    """Within a fetched family, put reasonably quoted close races first."""
    distances = []
    volume = 0.0
    for market in event.get("markets") or []:
        volume += number(market.get("volume_24h_fp", market.get("volume_24h"))) or 0
        bid, ask = number(market.get("yes_bid_dollars")), number(market.get("yes_ask_dollars"))
        if bid is None and "yes_bid_dollars" not in market:
            cents = number(market.get("yes_bid"))
            bid = cents / 100 if cents is not None else None
        if ask is None and "yes_ask_dollars" not in market:
            cents = number(market.get("yes_ask"))
            ask = cents / 100 if cents is not None else None
        if (bid is not None and ask is not None and 0 <= bid <= ask <= 1 and ask - bid <= 0.15
                and number(market.get("yes_bid_size_fp", market.get("yes_bid_size"))) != 0
                and number(market.get("yes_ask_size_fp", market.get("yes_ask_size"))) != 0):
            distances.append(abs((bid + ask) / 2 - 0.5))
    return (0, min(distances), -volume) if distances else (1, 1, -volume)


def _kalshi_cycle_evidence(event):
    """Infer the 2026 election only with both ticker and term-rule evidence.

    Current district contracts often name the congressional term beginning in
    2027, rather than the election year. Expiry dates alone prove no cycle.
    """
    ticker = str(event.get("event_ticker") or "")
    if not re.search(r"-(?:26|2026)$", ticker):
        return None
    for market in event.get("markets") or []:
        rule = str(market.get("rules_primary") or "")
        if (re.search(r"\b(?:House member|Senat(?:e|or)|Congress)\b", rule, re.I)
                and re.search(r"\bterm\s+(?:beginning|starting)\s+(?:in\s+)?2027\b", rule, re.I)):
            return {"year": 2026, "event_ticker": ticker, "rule": rule,
                    "basis": "Election ticker suffix and congressional term beginning in 2027"}
    return None


def _query_match(text, queries):
    if not queries:
        return True
    terms = {word.lower() for query in queries for word in re.findall(r"[a-zA-Z]+", query)
             if len(word) > 2 and word.lower() not in {"the", "will", "who", "and", "for", "united", "states"}}
    return any(re.search(r"\b" + re.escape(word) + r"\b", text, re.I) for word in terms)


def discover(poly, kalshi, classifier, store, *, platform="both", queries=None, topics=None,
             max_pages=3, max_series=80, include_closed=False, catalog_scan=False, series_tickers=None):
    candidates = {}
    rows = []
    coverage = {"polymarket_events": 0, "kalshi_series_selected": 0, "kalshi_events": 0,
                "candidate_markets": 0, "unclassified_markets": 0, "scope": "bounded topic discovery"}

    def add(market, event, venue):
        key = (venue, str(market.get("id") if venue == "polymarket" else market.get("ticker")))
        if not key[1] or key[1] == "None" or key in candidates:
            return
        normalizer = normalize_polymarket if venue == "polymarket" else normalize_kalshi
        observed_at = utc_now()
        normalized = normalizer(market, event=event, classifier=classifier, observed_at=observed_at)
        # The original API response retains sibling markets. Repeating the entire
        # event for every child would make this candidate table quadratic in size.
        event_context = {key: value for key, value in event.items() if key != "markets"}
        candidates[key] = {"platform": venue, "market": market, "event": event_context, "observed_at": observed_at}
        selected = [row for row in normalized if row.get("topics") and
                    (not topics or set(topics).intersection(row["topics"]))]
        rows.extend(selected)
        if not selected:
            coverage["unclassified_markets"] += 1

    if platform in {"both", "polymarket"}:
        event_ids = set()
        search_queries = queries or classifier.queries_for(topics)
        coverage["polymarket_queries"] = search_queries
        for query in search_queries:
            events = _attempt(store, "polymarket search: " + query,
                lambda q=query: list(poly.gamma.iter_search(q, max_pages=max_pages, limit_per_type=50,
                    **({} if include_closed else {"events_status": "active"}))), [])
            for event in events:
                event_ids.add(event.get("id"))
                for market in event.get("markets") or []:
                    if include_closed or not market.get("closed"):
                        add(market, event, "polymarket")
        if catalog_scan:
            events = _attempt(store, "polymarket catalog scan", lambda: list(poly.gamma.iter_events(
                max_pages=max_pages, **({} if include_closed else {"closed": False}))), [])
            for event in events:
                event_ids.add(event.get("id"))
                for market in event.get("markets") or []:
                    if include_closed or not market.get("closed"):
                        add(market, event, "polymarket")
        coverage["polymarket_events"] = len(event_ids)

    if platform in {"both", "kalshi"}:
        payload = _attempt(store, "kalshi series", lambda: kalshi.get_series_list(include_volume=True), {})
        all_series = payload.get("series") or []
        write_json(store.path / "kalshi_series.json", all_series)
        ranked = []
        for series in all_series:
            context = _series_context(series)
            result = classifier.classify(context)
            midterm_candidate = _midterm_series_candidate(series, context, result)
            explicit = series_tickers and series.get("ticker") in series_tickers
            selected = result["topics"] and (not topics or set(topics).intersection(result["topics"]))
            if midterm_candidate and (not topics or "midterms_2026" in topics):
                selected = True
            if explicit or (not series_tickers and selected and _query_match(context, queries)):
                priority = _kalshi_series_priority(series, midterm_candidate) if not topics or "midterms_2026" in topics else 0
                ranked.append((result["relevance_score"], series, context, priority))
        ranked.sort(key=lambda x: (-x[3], -x[0], -(number(x[1].get("volume_fp")) or 0), x[1].get("ticker", "")))
        coverage["kalshi_series_matching"] = len(ranked)
        if len(ranked) > max_series:
            store.warnings.append(f"Kalshi series limited to {max_series} of {len(ranked)} matches; increase --max-series")
        if series_tickers:
            missing = set(series_tickers) - {s.get("ticker") for s in all_series}
            if missing:
                store.warnings.append("Requested series absent from catalog: " + ", ".join(sorted(missing)))
        coverage["kalshi_series_priority"] = "Chamber outcomes, broad district families, then related elections; event year verified separately"
        coverage["kalshi_selected_series_tickers"] = [item[1]["ticker"] for item in ranked[:max_series]]
        for _, series, context, _ in ranked[:max_series]:
            coverage["kalshi_series_selected"] += 1
            ticker = series["ticker"]
            events = _attempt(store, "kalshi events: " + ticker, lambda t=ticker: list(kalshi.iter_events(
                series_ticker=t, with_nested_markets=True, max_pages=max_pages,
                **({} if include_closed else {"status": "open"}))), [])
            for event in sorted(events, key=_kalshi_event_priority):
                coverage["kalshi_events"] += 1
                event = {**event, "description": str(event.get("description") or "") + " " + context,
                         "series_title": series.get("title"), "series_ticker": ticker}
                cycle = _kalshi_cycle_evidence(event)
                if cycle:
                    event["derived_election_cycle"] = cycle["year"]
                    event["derived_election_cycle_evidence"] = {key: value for key, value in cycle.items() if key != "year"}
                    event["series_context"] = "2026 congressional election cycle inferred from ticker and term rules."
                for market in event.get("markets") or []:
                    if include_closed or market.get("status") in {"active", "open"}:
                        add(market, event, "kalshi")
        if include_closed:
            store.warnings.append("Nested Kalshi events exclude archived markets. Use historical discovery/GET for pre-cutoff markets.")
    coverage["candidate_markets"] = len(candidates)
    coverage["selected_outcome_rows"] = len(rows)
    write_jsonl(store.path / "candidates.jsonl", candidates.values())
    write_jsonl(store.path / "markets.jsonl", rows)
    write_csv(store.path / "markets.csv", rows)
    write_json(store.path / "coverage.json", coverage)
    return rows, coverage


def epoch(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    value = str(value)
    if value.isdigit():
        return int(value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a timezone, e.g. 2026-09-01T00:00:00Z")
    return int(parsed.timestamp())


def time_windows(start, end, step):
    if step <= 0:
        raise ValueError("step must be positive")
    if end <= start:
        raise ValueError("end must be after start")
    while start < end:
        stop = min(end, start + step)
        yield start, stop
        start = stop


def collect(poly, kalshi, store, rows, *, datasets=("books", "history", "trades"), max_markets=20,
            max_pages=3, start=None, end=None, period=60):
    """Collect each selected contract; raw responses preserve platform-native schemas.

    A market limit counts contracts, not individual Polymarket outcome tokens.
    Historical/live trades are merged by ID, candles by end_period_ts, per contract.
    """
    end = epoch(end) if end is not None else int(datetime.now(timezone.utc).timestamp())
    start = epoch(start) if start is not None else end - 7 * 86400
    if end <= start:
        raise ValueError("end must be after start")
    selected = {}
    for row in rows:
        key = (row["platform"], row["market_id"])
        selected.setdefault(key, []).append(row)
    if len(selected) > max_markets:
        store.warnings.append(f"Collection limited to {max_markets} of {len(selected)} contracts")
    # Preserve discovery order within each venue, interleaving venues so a small
    # limit does not accidentally spend its entire budget on the first platform.
    by_venue = {}
    for key, outcomes in selected.items():
        by_venue.setdefault(key[0], []).append((key, outcomes))
    selected_items = [item for group in zip_longest(*by_venue.values()) for item in group if item is not None][:max_markets]
    outputs = []
    cutoff = _attempt(store, "kalshi historical cutoff", lambda: kalshi.get_historical_cutoff()) if any(
        k[0] == "kalshi" for k, _ in selected_items) else None
    for (venue, market_id), outcomes in selected_items:
        first = outcomes[0]
        label = f"{venue}:{market_id}"
        result = {"platform": venue, "market_id": market_id, "observed_at": utc_now()}
        if venue == "polymarket":
            token_results = []
            for row in outcomes:
                token_id = row.get("token_id")
                if not token_id:
                    store.error(label, "Missing outcome token ID")
                    continue
                token = {"token_id": token_id, "outcome": row.get("outcome")}
                if "books" in datasets:
                    token["book"] = _attempt(store, label + ":book", lambda: poly.clob.book(token_id))
                if "history" in datasets:
                    points = {}
                    for left, right in time_windows(start, end, 7 * 86400):
                        history = _attempt(store, label + ":history", lambda: poly.clob.price_history(
                            token_id, startTs=left, endTs=right, fidelity=period), {})
                        for point in history.get("history") or []:
                            points[point["t"]] = point
                    token["history"] = [points[t] for t in sorted(points)]
                token_results.append(token)
            result["outcomes"] = token_results
            # Normalized ID may be Gamma ID; condition_id is the Data API join key.
            condition = first.get("condition_id")
            if not condition and str(market_id).startswith("0x"):
                condition = market_id
            if condition:
                if "trades" in datasets:
                    result["trades"] = _attempt(store, label + ":trades", lambda: list(poly.data.iter_trades(
                        market=[condition], start=start, end=end, max_pages=max_pages)), [])
                if "holders" in datasets:
                    result["holders"] = _attempt(store, label + ":holders", lambda: poly.data.holders(market=[condition]))
                if "open_interest" in datasets:
                    result["open_interest"] = _attempt(store, label + ":open_interest", lambda: poly.data.open_interest(market=[condition]))
            elif set(datasets).intersection({"trades", "holders", "open_interest"}):
                store.error(label, "Missing condition_id for Data API; rediscover with current normalizer")
            if "comments" in datasets and first.get("event_id"):
                result["comments"] = _attempt(store, label + ":comments", lambda: list(poly.gamma.iter_comments(
                    parent_entity_type="Event", parent_entity_id=int(first["event_id"]), max_pages=max_pages)), [])
        elif venue == "kalshi":
            unsupported = set(datasets).intersection({"holders", "comments"})
            if unsupported:
                store.warnings.append("Kalshi public API does not expose " + ", ".join(sorted(unsupported)) +
                                      "; those datasets were skipped for Kalshi contracts")
            if "books" in datasets:
                result["book"] = _attempt(store, label + ":book", lambda: kalshi.get_orderbook(market_id))
            if "trades" in datasets:
                trades = _attempt(store, label + ":trades", lambda: list(kalshi.iter_trades(
                    ticker=market_id, min_ts=start, max_ts=end, max_pages=max_pages)), [])
                if cutoff and start < epoch(cutoff.get("trades_created_ts", 0)):
                    trades += _attempt(store, label + ":historical trades", lambda: list(kalshi.iter_historical_trades(
                        ticker=market_id, min_ts=start, max_ts=end, max_pages=max_pages)), [])
                unique = {str(t.get("trade_id") or json.dumps(t, sort_keys=True)): t for t in trades}
                result["trades"] = list(unique.values())
            if "history" in datasets:
                series = first.get("series_id") or first.get("series_ticker")
                if not series:
                    store.error(label + ":candles", "Missing series_id; preserve discovery event metadata")
                else:
                    candles = {}
                    historical = False
                    for left, right in time_windows(start, end, period * 60 * 4000):
                        try:
                            if historical:
                                data = kalshi.get_historical_candlesticks(market_id, left, right, period_interval=period)
                            else:
                                data = kalshi.get_candlesticks(series, market_id, left, right, period_interval=period)
                                if not data.get("candlesticks"):
                                    # Archival is by settlement time, not candle date. Check archived market existence.
                                    try:
                                        kalshi.get_historical_market(market_id)
                                        historical = True
                                        data = kalshi.get_historical_candlesticks(market_id, left, right, period_interval=period)
                                    except APIError as probe_error:
                                        if probe_error.status_code != 404:
                                            raise
                        except APIError as exc:
                            if exc.status_code == 404 and not historical:
                                historical = True
                                data = _attempt(store, label + ":historical candles", lambda: kalshi.get_historical_candlesticks(
                                    market_id, left, right, period_interval=period), {})
                            else:
                                store.error(label + ":candles", exc)
                                data = {}
                        for candle in data.get("candlesticks") or []:
                            candles[candle["end_period_ts"]] = candle
                    result["candlesticks"] = [candles[t] for t in sorted(candles)]
            if "open_interest" in datasets:
                try:
                    result["market_details"] = kalshi.get_market(market_id)
                except APIError as exc:
                    if exc.status_code == 404:
                        result["market_details"] = _attempt(store, label + ":historical details",
                            lambda: kalshi.get_historical_market(market_id))
                    else:
                        store.error(label + ":details", exc)
                        result["market_details"] = None
        outputs.append(result)
    write_jsonl(store.path / "extracted.jsonl", outputs)
    write_json(store.path / "history_window.json", {"start_ts": start, "end_ts": end, "period_minutes": period,
        "kalshi_cutoff": cutoff, "note": "Historical book snapshots require prior WebSocket/REST capture. Empty arrays are not zero activity."})
    return outputs
