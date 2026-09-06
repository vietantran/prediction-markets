"""Read-only Polymarket API adapters, verified against the September 2026 specs.

Raw responses are intentional: preserve identifiers, rule text and new fields.
Gamma IDs, condition IDs and outcome token IDs are different namespaces.
All POST methods here perform documented reads; no trading routes are exposed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import warnings
from collections.abc import Iterator, Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlsplit

from .http import HttpClient, PaginationError


class PaginationWarning(UserWarning):
    """A requested collection is incomplete because a configured cap was reached."""


def _id(value: str | int) -> str:
    return quote(str(value), safe="")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _warn(message: str) -> None:
    warnings.warn(message, PaginationWarning, stacklevel=3)


def _bounds(page_size: int, max_pages: int, maximum: int | None = None) -> None:
    if page_size < 1 or max_pages < 1:
        raise ValueError("page_size and max_pages must be positive")
    if maximum is not None and page_size > maximum:
        raise ValueError(f"page_size must be <= {maximum} for this endpoint")


def _rows(payload: Any, key: str | None = None) -> list[dict]:
    if key is not None and not isinstance(payload, Mapping):
        raise PaginationError(f"Expected an object containing {key}")
    rows = payload if key is None else payload.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PaginationError(f"Expected a list of records at {key or 'response root'}")
    return rows


def _cursor_pages(fetch, *, key: str, cursor_param="next_cursor", cursor_key="next_cursor",
                  max_pages=100, nested=False, **params) -> Iterator[dict]:
    if max_pages < 1:
        raise ValueError("max_pages must be positive")
    cursor = params.pop(cursor_param, None)
    seen_cursors = {cursor} if cursor else set()
    seen_pages: set[str] = set()
    for _ in range(max_pages):
        payload = fetch(**params, **({cursor_param: cursor} if cursor else {}))
        rows = _rows(payload, key)
        next_cursor = (payload.get("pagination") or {}).get(cursor_key) if nested else payload.get(cursor_key)
        if rows:
            fingerprint = _fingerprint(rows)
            if fingerprint in seen_pages:
                raise PaginationError("API repeated a page; refusing a potentially incomplete collection")
            seen_pages.add(fingerprint)
            yield from rows
        if not next_cursor or next_cursor == "LTE=":
            return
        if next_cursor in seen_cursors:
            raise PaginationError("API repeated a pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    _warn(f"Stopped after max_pages={max_pages}; continuation cursor {cursor!r} remains")


def _offset_pages(fetch, *, page_size=100, max_pages=100, offset=0,
                  max_offset: int | None = None, maximum: int | None = None, **params):
    _bounds(page_size, max_pages, maximum)
    if offset < 0 or (max_offset is not None and offset > max_offset):
        raise ValueError("offset outside endpoint limits")
    seen_pages: set[str] = set()
    for _ in range(max_pages):
        rows = _rows(fetch(limit=page_size, offset=offset, **params))
        if not rows:
            return
        fingerprint = _fingerprint(rows)
        if fingerprint in seen_pages:
            raise PaginationError("API repeated an offset page")
        seen_pages.add(fingerprint)
        yield from rows
        if len(rows) < page_size:
            return
        offset += len(rows)
        if max_offset is not None and offset > max_offset:
            _warn(f"Endpoint offset cap {max_offset} reached; narrow the filters or use time windows")
            return
    _warn(f"Stopped after max_pages={max_pages}; next offset is {offset}")


class ReadAPI:
    BASE = ""
    GET_PATHS: tuple[str, ...] = ()
    AUTH_PATHS: tuple[str, ...] = ()

    def __init__(self, client=None, *, base_url=None, **http_options):
        self.client = client if client is not None else HttpClient(base_url or self.BASE, **http_options)

    def get(self, path: str, **params):
        """Read a documented route; parameters retain the official API spelling."""
        if not any(re.fullmatch(pattern, path) for pattern in self.GET_PATHS):
            raise ValueError(f"Route is not in this adapter's documented read-only allowlist: {path}")
        authenticated = any(re.fullmatch(pattern, path) for pattern in self.AUTH_PATHS)
        return self.client.get(path, params=params, authenticated=authenticated)

    def close(self):
        self.client.close()


class GammaAPI(ReadAPI):
    BASE = "https://gamma-api.polymarket.com"
    GET_PATHS = (
        r"/status", r"/teams", r"/teams/[^/]+", r"/tags", r"/tags/[^/]+",
        r"/tags/slug/[^/]+", r"/tags/(?:slug/)?[^/]+/related-tags(?:/tags)?",
        r"/events", r"/events/(?:pagination|results|keyset|creators)",
        r"/events/creators/[^/]+", r"/events/[^/]+", r"/events/slug/[^/]+",
        r"/events/[^/]+/(?:tweet-count|comments/count|tags)",
        r"/markets", r"/markets/keyset", r"/markets/[^/]+", r"/markets/slug/[^/]+",
        r"/markets/[^/]+/(?:description|tags)", r"/series", r"/series/[^/]+",
        r"/series/[^/]+/comments/count", r"/series-summary/(?:slug/)?[^/]+",
        r"/comments", r"/comments/[^/]+", r"/comments/user_address/[^/]+",
        r"/public-profile", r"/profiles/user_address/[^/]+", r"/sports",
        r"/sports/market-types", r"/public-search",
    )

    def events(self, **params):
        return self.get("/events", **params)

    def markets(self, **params):
        return self.get("/markets", **params)

    def events_keyset(self, **params):
        return self.get("/events/keyset", **params)

    def markets_keyset(self, **params):
        return self.get("/markets/keyset", **params)

    def iter_events(self, *, page_size=100, max_pages=100, pagination="keyset", **params):
        if pagination == "offset":
            yield from _offset_pages(self.events, page_size=page_size, max_pages=max_pages, **params)
        elif pagination == "keyset":
            _bounds(page_size, max_pages, 500)
            if "offset" in params:
                raise ValueError("Gamma keyset endpoints reject offset; use after_cursor")
            yield from _cursor_pages(self.events_keyset, key="events", cursor_param="after_cursor",
                                     limit=page_size, max_pages=max_pages, **params)
        else:
            raise ValueError("pagination must be keyset or offset")

    def iter_markets(self, *, page_size=100, max_pages=100, pagination="keyset", **params):
        if pagination == "offset":
            yield from _offset_pages(self.markets, page_size=page_size, max_pages=max_pages, **params)
        elif pagination == "keyset":
            _bounds(page_size, max_pages, 100)
            if "offset" in params:
                raise ValueError("Gamma keyset endpoints reject offset; use after_cursor")
            yield from _cursor_pages(self.markets_keyset, key="markets", cursor_param="after_cursor",
                                     limit=page_size, max_pages=max_pages, **params)
        else:
            raise ValueError("pagination must be keyset or offset")

    def event(self, event_id, **params):
        return self.get(f"/events/{_id(event_id)}", **params)

    def event_by_slug(self, slug, **params):
        return self.get(f"/events/slug/{_id(slug)}", **params)

    def market(self, market_id, **params):
        return self.get(f"/markets/{_id(market_id)}", **params)

    def market_by_slug(self, slug, **params):
        return self.get(f"/markets/slug/{_id(slug)}", **params)

    def market_description(self, market_id):
        return self.get(f"/markets/{_id(market_id)}/description")

    def market_information(self, filters: Mapping, *, abridged=False):
        return self.client.post("/markets/abridged" if abridged else "/markets/information", json=dict(filters))

    def tags(self, **params):
        return self.get("/tags", **params)

    def tag(self, tag_id, **params):
        return self.get(f"/tags/{_id(tag_id)}", **params)

    def tag_by_slug(self, slug, **params):
        return self.get(f"/tags/slug/{_id(slug)}", **params)

    def related_tags(self, tag_id=None, *, slug=None, full=False, **params):
        if (tag_id is None) == (slug is None):
            raise ValueError("Provide exactly one of tag_id or slug")
        target = f"slug/{_id(slug)}" if slug is not None else _id(tag_id)
        return self.get(f"/tags/{target}/related-tags" + ("/tags" if full else ""), **params)

    def event_tags(self, event_id):
        return self.get(f"/events/{_id(event_id)}/tags")

    def market_tags(self, market_id):
        return self.get(f"/markets/{_id(market_id)}/tags")

    def iter_tags(self, *, page_size=100, max_pages=100, **params):
        yield from _offset_pages(self.tags, page_size=page_size, max_pages=max_pages, **params)

    def series(self, **params):
        return self.get("/series", **params)

    def series_detail(self, series_id, **params):
        return self.get(f"/series/{_id(series_id)}", **params)

    def iter_series(self, *, page_size=100, max_pages=100, **params):
        yield from _offset_pages(self.series, page_size=page_size, max_pages=max_pages, **params)

    def comments(self, **params):
        return self.get("/comments", **params)

    def iter_comments(self, *, page_size=100, max_pages=100, **params):
        yield from _offset_pages(self.comments, page_size=page_size, max_pages=max_pages, **params)

    def comments_by_user(self, address, **params):
        return self.get(f"/comments/user_address/{_id(address)}", **params)

    def public_profile(self, address):
        return self.get("/public-profile", address=address)

    def search(self, query: str, **params):
        return self.get("/public-search", q=query, **params)

    def iter_search(self, query: str, *, max_pages=10, page=1, **params):
        """Yield matching events. Use search() for raw tags/profiles/pagination too."""
        if max_pages < 1 or page < 1:
            raise ValueError("max_pages and page must be positive")
        seen_pages: set[str] = set()
        for _ in range(max_pages):
            payload = self.search(query, page=page, **params)
            rows = _rows(payload, "events") if payload.get("events") is not None else []
            fingerprint = _fingerprint(payload.get("events"))
            if rows and fingerprint in seen_pages:
                raise PaginationError("Search repeated an event page")
            seen_pages.add(fingerprint)
            yield from rows
            if not (payload.get("pagination") or {}).get("hasMore", False):
                return
            page += 1
        _warn(f"Search {query!r} stopped after max_pages={max_pages}; next page is {page}")


class ClobAPI(ReadAPI):
    BASE = "https://clob.polymarket.com"
    GET_PATHS = (
        r"/time", r"/(?:midpoint|midpoints|spread|last-trade-price|last-trades-prices)",
        r"/(?:price|prices|book|books|prices-history)",
        r"/(?:fee-rate|tick-size|neg-risk)(?:/[^/]+)?",
        r"/(?:simplified-markets|sampling-markets|sampling-simplified-markets)",
        r"/clob-markets/[^/]+", r"/markets-by-token/[^/]+", r"/markets/live-activity/[^/]+",
        r"/rewards/markets/(?:current|multi|[^/]+)", r"/rebates/current", r"/builder/trades",
        r"/data/(?:orders|trades)", r"/data/order/[^/]+", r"/balance-allowance",
        r"/notifications", r"/rewards/user(?:/(?:total|percentages|markets))?",
        r"/(?:order-scoring|orders-scoring)", r"/auth/ban-status/closed-only",
    )
    AUTH_PATHS = (
        r"/data/.*", r"/balance-allowance", r"/notifications", r"/rewards/user.*",
        r"/(?:order-scoring|orders-scoring)", r"/auth/ban-status/closed-only",
    )

    @staticmethod
    def _tokens(token_ids: Sequence[str], maximum=500) -> list[str]:
        if isinstance(token_ids, str) or not 1 <= len(token_ids) <= maximum:
            raise ValueError(f"Provide 1..{maximum} token IDs in a list")
        return [str(token_id) for token_id in token_ids]

    def market(self, condition_id):
        return self.get(f"/clob-markets/{_id(condition_id)}")

    def market_by_token(self, token_id):
        return self.get(f"/markets-by-token/{_id(token_id)}")

    def book(self, token_id):
        return self.get("/book", token_id=token_id)

    def _batch(self, path, token_ids):
        return self.client.post(path, json=[{"token_id": token} for token in self._tokens(token_ids)])

    def books(self, token_ids):
        return self._batch("/books", token_ids)

    def midpoint(self, token_id):
        return self.get("/midpoint", token_id=token_id)

    def midpoints(self, token_ids):
        return self._batch("/midpoints", token_ids)

    def spread(self, token_id):
        return self.get("/spread", token_id=token_id)

    def spreads(self, token_ids):
        return self._batch("/spreads", token_ids)

    def last_trade_price(self, token_id):
        return self.get("/last-trade-price", token_id=token_id)

    def last_trade_prices(self, token_ids):
        return self._batch("/last-trades-prices", token_ids)

    def price(self, token_id, side="BUY"):
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        return self.get("/price", token_id=token_id, side=side)

    def prices(self, token_ids, *, side="BUY"):
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        return self.client.post("/prices", json=[{"token_id": t, "side": side} for t in self._tokens(token_ids)])

    def fee_rate(self, token_id):
        return self.get("/fee-rate", token_id=token_id)

    def tick_size(self, token_id):
        return self.get("/tick-size", token_id=token_id)

    def negative_risk(self, token_id):
        return self.get("/neg-risk", token_id=token_id)

    def price_history(self, token_id, **params):
        """market is an outcome TOKEN ID; startTs/endTs seconds, fidelity minutes."""
        if "start_ts" in params:
            params["startTs"] = params.pop("start_ts")
        if "end_ts" in params:
            params["endTs"] = params.pop("end_ts")
        if not any(k in params for k in ("interval", "startTs", "endTs")):
            params["interval"] = "max"
        return self.get("/prices-history", market=token_id, **params)

    def batch_price_history(self, token_ids, **params):
        """Batch endpoint uses snake_case start_ts/end_ts, maximum 20 tokens."""
        if "startTs" in params:
            params["start_ts"] = params.pop("startTs")
        if "endTs" in params:
            params["end_ts"] = params.pop("endTs")
        if not any(k in params for k in ("interval", "start_ts", "end_ts")):
            params["interval"] = "max"
        return self.client.post("/batch-prices-history", json={"markets": self._tokens(token_ids, 20), **params})

    def live_activity(self, condition_id):
        return self.get(f"/markets/live-activity/{_id(condition_id)}")

    def batch_live_activity(self, condition_ids):
        return self.client.post("/markets/live-activity", json=list(condition_ids))

    def iter_markets(self, *, kind="simplified-markets", max_pages=100, **params):
        if kind not in {"simplified-markets", "sampling-markets", "sampling-simplified-markets"}:
            raise ValueError("Use one of the documented CLOB market catalog routes")
        yield from _cursor_pages(lambda **p: self.get("/" + kind, **p), key="data", max_pages=max_pages, **params)

    def rewards(self, condition_id=None, **params):
        return self.get("/rewards/markets/" + (_id(condition_id) if condition_id else "current"), **params)

    def iter_rewards(self, *, condition_id=None, max_pages=100, **params):
        yield from _cursor_pages(lambda **p: self.rewards(condition_id, **p), key="data", max_pages=max_pages, **params)

    def account_trades(self, maker_address, **params):
        """Authenticated participant history; public market prints are data.trades()."""
        return self.get("/data/trades", maker_address=maker_address, **params)

    def iter_account_trades(self, maker_address, *, max_pages=100, **params):
        yield from _cursor_pages(lambda **p: self.account_trades(maker_address, **p), key="data", max_pages=max_pages, **params)

    def builder_trades(self, builder_code, **params):
        return self.get("/builder/trades", builder_code=builder_code, **params)


class DataAPI(ReadAPI):
    BASE = "https://data-api.polymarket.com"
    GET_PATHS = (
        r"/", r"/v1/approvals", r"/(?:positions|trades|activity|holders|traded|revisions|value|oi|live-volume|closed-positions|other)",
        r"/v1/(?:activity|positions)/combos", r"/v1/market-positions",
        r"/v1/builders/(?:leaderboard|volume)", r"/v1/leaderboard",
    )

    def get(self, path, **params):
        if params.get("market") is not None and params.get("eventId") is not None:
            raise ValueError("Data API market and eventId filters are mutually exclusive")
        # Unlike Gamma's repeated query parameters, Data API arrays use CSV.
        params = {k: ",".join(map(str, v)) if isinstance(v, (list, tuple)) else v for k, v in params.items()}
        return super().get(path, **params)

    def trades(self, **params):
        return self.get("/trades", **params)

    def iter_trades(self, *, page_size=1000, max_pages=100, **params):
        yield from _offset_pages(self.trades, page_size=page_size, max_pages=max_pages,
                                 max_offset=10000, maximum=10000, **params)

    def activity(self, user, **params):
        return self.get("/activity", user=user, **params)

    def iter_activity(self, user, *, page_size=500, max_pages=100, **params):
        yield from _offset_pages(lambda **p: self.activity(user, **p), page_size=page_size,
                                 max_pages=max_pages, max_offset=5000, maximum=500, **params)

    def positions(self, user, **params):
        return self.get("/positions", user=user, **params)

    def iter_positions(self, user, *, page_size=500, max_pages=100, **params):
        yield from _offset_pages(lambda **p: self.positions(user, **p), page_size=page_size,
                                 max_pages=max_pages, max_offset=10000, maximum=500, **params)

    def closed_positions(self, user, **params):
        return self.get("/closed-positions", user=user, **params)

    def iter_closed_positions(self, user, *, page_size=50, max_pages=100, **params):
        yield from _offset_pages(lambda **p: self.closed_positions(user, **p), page_size=page_size,
                                 max_pages=max_pages, max_offset=100000, maximum=50, **params)

    def holders(self, market, **params):
        """Top 20 per outcome maximum; this endpoint is not the full holder census."""
        return self.get("/holders", market=market, **params)

    def market_positions(self, market, **params):
        """Grouped per outcome; offset/limit apply separately to each outcome."""
        return self.get("/v1/market-positions", market=market, **params)

    def open_interest(self, market=None):
        return self.get("/oi", market=market)

    def live_volume(self, event_id):
        return self.get("/live-volume", id=event_id)

    def position_value(self, user, **params):
        return self.get("/value", user=user, **params)

    def traded(self, user):
        return self.get("/traded", user=user)

    def revisions(self, question_id, **params):
        return self.get("/revisions", questionID=question_id, **params)

    def leaderboard(self, **params):
        return self.get("/v1/leaderboard", **params)

    def iter_leaderboard(self, *, page_size=50, max_pages=25, **params):
        yield from _offset_pages(self.leaderboard, page_size=page_size, max_pages=max_pages,
                                 max_offset=1000, maximum=50, **params)

    def builder_leaderboard(self, **params):
        return self.get("/v1/builders/leaderboard", **params)

    def builder_volume(self, **params):
        return self.get("/v1/builders/volume", **params)

    def combo_positions(self, user, **params):
        return self.get("/v1/positions/combos", user=user, **params)

    def combo_activity(self, user, **params):
        return self.get("/v1/activity/combos", user=user, **params)

    def iter_combo_positions(self, user, *, page_size=100, max_pages=100, **params):
        _bounds(page_size, max_pages, 1000)
        yield from _cursor_pages(lambda **p: self.combo_positions(user, **p), key="combos",
                                 cursor_param="cursor", nested=True, limit=page_size,
                                 max_pages=max_pages, **params)

    def iter_trades_windowed(self, *, start: int, end: int, page_size=1000, max_requests=200, **params):
        """Recursively split inclusive second windows before exceeding offset 10000.

        Buffer each window before yielding, so overlapping parent probes are never
        emitted twice. A one-second window exceeding the cap raises PaginationError.
        The market/event API still imposes its approximately three-year retention.
        """
        yield from self._windowed(self.trades, start, end, page_size, 10000, 10000, max_requests, params)

    def iter_activity_windowed(self, user, *, start: int, end: int, page_size=500, max_requests=200, **params):
        yield from self._windowed(lambda **p: self.activity(user, **p), start, end, page_size,
                                  5000, 500, max_requests, params)

    @staticmethod
    def _windowed(fetch, start, end, page_size, max_offset, maximum, max_requests, params):
        _bounds(page_size, max_requests, maximum)
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            raise ValueError("Use positive integer epoch seconds with end >= start")
        if any(k in params for k in ("offset", "limit", "start", "end")):
            raise ValueError("Window iterator controls offset, limit, start and end")
        pending = [(start, end)]
        requests = 0
        while pending:
            lo, hi = pending.pop()
            records, seen = [], set()
            offset = 0
            complete = False
            while offset <= max_offset:
                if requests >= max_requests:
                    _warn(f"max_requests={max_requests} reached; window [{lo}, {hi}] and {len(pending)} others remain")
                    return
                page = _rows(fetch(start=lo, end=hi, offset=offset, limit=page_size, **params))
                requests += 1
                if page and _fingerprint(page) in seen:
                    raise PaginationError("Repeated page within history window")
                seen.add(_fingerprint(page))
                records.extend(page)
                if len(page) < page_size:
                    complete = True
                    break
                offset += len(page)
            if complete:
                yield from records
            elif lo == hi:
                raise PaginationError(f"More than the API offset budget at second {lo}; cannot extract completely")
            else:
                mid = (lo + hi) // 2
                pending.extend([(mid + 1, hi), (lo, mid)])


class BridgeAPI(ReadAPI):
    BASE = "https://bridge.polymarket.com"
    GET_PATHS = (r"/supported-assets", r"/status/[^/]+")

    def assets(self):
        return self.get("/supported-assets")

    def status(self, address, **params):
        return self.get(f"/status/{_id(address)}", **params)

    def iter_status(self, address, *, page_size=100, max_pages=100, **params):
        _bounds(page_size, max_pages, 100)
        yield from _cursor_pages(lambda **p: self.status(address, **p), key="transactions",
                                 cursor_param="cursor", cursor_key="nextCursor", limit=page_size,
                                 max_pages=max_pages, **params)

    def quote(self, **body):
        """Fee/output preview only; does not create deposit/withdrawal addresses."""
        return self.client.post("/quote", json=body)


class CombosAPI(ReadAPI):
    BASE = "https://combos-rfq-api.polymarket.com"
    GET_PATHS = (r"/v1/rfq/combo-markets",)

    def markets(self, **params):
        return self.get("/v1/rfq/combo-markets", **params)

    def iter_markets(self, *, page_size=100, max_pages=100, **params):
        _bounds(page_size, max_pages, 100)
        yield from _cursor_pages(self.markets, key="markets", cursor_param="cursor", limit=page_size,
                                 max_pages=max_pages, **params)


class RelayerAPI(ReadAPI):
    BASE = "https://relayer-v2.polymarket.com"
    GET_PATHS = (r"/transaction", r"/transactions", r"/nonce", r"/relay-payload", r"/deployed")
    AUTH_PATHS = (r"/transactions",)

    def transaction(self, transaction_id):
        return self.get("/transaction", id=transaction_id)

    def deployed(self, address, **params):
        return self.get("/deployed", address=address, **params)

    def nonce(self, address, wallet_type="SAFE"):
        return self.get("/nonce", address=address, type=wallet_type)


class PerpsAPI(ReadAPI):
    """Separate perpetual-futures context; prices are NOT election probabilities."""
    BASE = "https://api.perpetuals.polymarket.com"
    GET_PATHS = (r"/v1/info/(?:ping|time|exchange|assets|instruments|tickers|statistics|klines|mark-history|bbo|book|index|trades|portfolio|position-fills|funding|fees|limit-tiers|invite)",)

    def instruments(self, **params):
        return self.get("/v1/info/instruments", **params)

    def tickers(self, **params):
        return self.get("/v1/info/tickers", **params)

    def book(self, instrument_id, **params):
        return self.get("/v1/info/book", instrument_id=instrument_id, **params)

    def trades(self, instrument_id, **params):
        return self.get("/v1/info/trades", instrument_id=instrument_id, **params)

    def funding(self, instrument_id, **params):
        return self.get("/v1/info/funding", instrument_id=instrument_id, **params)

    def klines(self, instrument_id, **params):
        return self.get("/v1/info/klines", instrument_id=instrument_id, **params)


class PolymarketL2Signer:
    """HMAC signer for optional GET-only CLOB participant reads.

    API credentials must already exist. This helper never creates/derives keys.
    Signing protocol: timestamp + GET + URL path; query string is not signed.
    """
    def __init__(self, address, api_key, secret, passphrase, *, clock=time.time):
        if not all((address, api_key, secret, passphrase)):
            raise ValueError("All four Polymarket L2 credential fields are required")
        self.address, self.api_key, self.passphrase = address, api_key, passphrase
        self.secret = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
        self.clock = clock

    @classmethod
    def from_env(cls):
        return cls(*(os.environ.get(name) for name in (
            "POLYMARKET_ADDRESS", "POLYMARKET_API_KEY", "POLYMARKET_API_SECRET", "POLYMARKET_API_PASSPHRASE")))

    def __call__(self, method, url):
        if method.upper() != "GET":
            raise ValueError("This research signer only authorizes GET reads")
        timestamp = str(int(self.clock()))
        message = timestamp + "GET" + urlsplit(url).path
        signature = base64.urlsafe_b64encode(hmac.new(self.secret, message.encode(), hashlib.sha256).digest()).decode()
        return {"POLY_ADDRESS": self.address, "POLY_API_KEY": self.api_key,
                "POLY_PASSPHRASE": self.passphrase, "POLY_TIMESTAMP": timestamp,
                "POLY_SIGNATURE": signature}


class SubgraphAPI:
    """Caller-selected GraphQL analytics endpoint; no stale vendor URLs assumed."""
    def __init__(self, endpoint, *, client=None, **http_options):
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Use a full HTTPS GraphQL endpoint without query/fragment")
        self.path = parsed.path or "/"
        self.client = client if client is not None else HttpClient(
            f"{parsed.scheme}://{parsed.netloc}", **http_options)

    def query(self, query: str, variables=None, operation_name=None):
        # graphql-core is optional. Parsing avoids trusting regex mutation guards
        # or accidentally allowing multiple operations containing a mutation.
        try:
            from graphql import parse
            from graphql.language.ast import OperationDefinitionNode
        except ImportError as exc:
            raise RuntimeError("Install the graphql extra: pip install 'pmresearch[graphql]'") from exc
        document = parse(query)
        operations = [d for d in document.definitions if isinstance(d, OperationDefinitionNode)]
        if not operations or any(op.operation.value != "query" for op in operations):
            raise ValueError("Only GraphQL query operations are permitted")
        body = {"query": query, "variables": variables or {}}
        if operation_name:
            body["operationName"] = operation_name
        payload = self.client.post(self.path, json=body)
        if payload.get("errors"):
            raise RuntimeError(f"GraphQL returned errors: {payload['errors']}")
        return payload

    def close(self):
        self.client.close()


class Polymarket:
    def __init__(self, *, gamma_client=None, clob_client=None, data_client=None,
                 bridge_client=None, combos_client=None, relayer_client=None, perps_client=None,
                 **http_options):
        self.gamma = GammaAPI(gamma_client, **http_options)
        self.clob = ClobAPI(clob_client, **http_options)
        self.data = DataAPI(data_client, **http_options)
        self.bridge = BridgeAPI(bridge_client, **http_options)
        self.combos = CombosAPI(combos_client, **http_options)
        self.relayer = RelayerAPI(relayer_client, **http_options)
        self.perps = PerpsAPI(perps_client, **http_options)

    @property
    def clients(self):
        return [self.gamma.client, self.clob.client, self.data.client, self.bridge.client,
                self.combos.client, self.relayer.client, self.perps.client]

    def close(self):
        for client in self.clients:
            client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
