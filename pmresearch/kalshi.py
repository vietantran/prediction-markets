"""Read-only Kalshi Trade API v2 client (REST plus reusable WebSocket signer).

Wrappers retain the original JSON and fixed-point strings. No method creates,
amends, cancels, or transfers anything. See docs/kalshi.md for endpoint coverage.
"""
from __future__ import annotations

import base64
import os
import time
import warnings
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import quote, urlsplit

from .http import HttpClient

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
DEMO_BASE_URL = "https://external-api.demo.kalshi.co/trade-api/v2"
WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
DEMO_WS_URL = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"


class KalshiSigner:
    """Callable signing each attempt; also usable for a WebSocket GET handshake."""

    def __init__(self, api_key_id: str, private_key: Any):
        if not api_key_id:
            raise ValueError("A Kalshi API key ID is required")
        self.api_key_id = api_key_id
        self.private_key = private_key

    @classmethod
    def from_env(cls) -> "KalshiSigner":
        key_id = os.getenv("KALSHI_API_KEY_ID")
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
        if not key_id or not key_path:
            raise ValueError("Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH for authenticated access")
        return cls.from_file(key_id, key_path)

    @classmethod
    def from_file(cls, api_key_id: str, private_key_path: str | Path) -> "KalshiSigner":
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

        private_key = load_pem_private_key(Path(private_key_path).expanduser().read_bytes(), password=None)
        if not isinstance(private_key, RSAPrivateKey):
            raise ValueError("Kalshi requires an RSA private key")
        return cls(api_key_id, private_key)

    def __call__(self, method: str, full_url: str) -> dict[str, str]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp = str(time.time_ns() // 1_000_000)
        path = urlsplit(full_url).path
        message = (timestamp + method.upper() + path).encode("utf-8")
        signature = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        }


def _part(value: str) -> str:
    if not value:
        raise ValueError("A nonempty ticker or identifier is required")
    return quote(str(value), safe="")


def _params(params: dict[str, Any]) -> dict[str, Any]:
    # requests encodes Python bools as True/False; Kalshi expects true/false.
    return {k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items() if v is not None}


def _tickers(values: Sequence[str], maximum: int = 100) -> list[str]:
    if isinstance(values, str):
        raise TypeError("Pass a list of tickers, not a comma-separated string")
    result = list(values)
    if not 1 <= len(result) <= maximum or any(not item for item in result):
        raise ValueError(f"Provide 1–{maximum} nonempty tickers")
    return result


def _window(start_ts: int, end_ts: int, period_interval: int, *, forecast: bool = False) -> dict[str, int]:
    allowed = (0, 1, 60, 1440) if forecast else (1, 60, 1440)
    if period_interval not in allowed:
        raise ValueError(f"period_interval must be one of {allowed} minutes")
    if start_ts < 0 or end_ts < start_ts:
        raise ValueError("Expected nonnegative Unix seconds with start_ts <= end_ts")
    return {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval}


def orderbook_to_yes_quotes(payload: dict[str, Any]) -> dict[str, Any]:
    """Return exact decimal-string YES bids/asks and touch prices.

    A NO bid at x dollars implies a YES ask at 1-x, with the same quantity.
    Empty sides stay empty; a midpoint requires both sides. Prefer fixed-point
    dollars; support archived legacy integer-cent payloads for old snapshots.
    """
    book = payload.get("orderbook_fp") or payload.get("orderbook") or payload

    def levels(side: str) -> list[tuple[Decimal, Decimal]]:
        use_dollars = side + "_dollars" in book
        rows = book.get(side + "_dollars") if use_dollars else book.get(side)
        answer = []
        for row in rows or []:
            try:
                price, quantity = Decimal(str(row[0])), Decimal(str(row[1]))
            except (IndexError, TypeError, InvalidOperation) as exc:
                raise ValueError("Malformed Kalshi orderbook price level") from exc
            if not use_dollars:
                price /= 100
            if not price.is_finite() or not quantity.is_finite() or not 0 <= price <= 1 or quantity < 0:
                raise ValueError("Invalid Kalshi price or quantity")
            if quantity:
                answer.append((price, quantity))
        return answer

    bids = sorted(levels("yes"), reverse=True)
    asks = sorted((Decimal(1) - price, quantity) for price, quantity in levels("no"))
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    midpoint = (best_bid + best_ask) / 2 if spread is not None else None
    return {
        "yes_bids": [{"price": str(price), "size": str(size)} for price, size in bids],
        "yes_asks": [{"price": str(price), "size": str(size)} for price, size in asks],
        "best_bid": str(best_bid) if best_bid is not None else None,
        "best_ask": str(best_ask) if best_ask is not None else None,
        "spread": str(spread) if spread is not None else None,
        "midpoint": str(midpoint) if midpoint is not None else None,
        "crossed": spread is not None and spread < 0,
    }


class Kalshi:
    """Public by default; authenticated=True explicitly loads environment keys."""

    def __init__(self, client: Any = None, *, base_url: str = BASE_URL,
                 authenticated: bool = False, signer: Any = None, **http_options: Any):
        self.authenticated = authenticated
        self.signer = signer or (KalshiSigner.from_env() if authenticated and client is None else None)
        self.client = client if client is not None else HttpClient(base_url, signer=self.signer, **http_options)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Kalshi":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def get(self, path: str, params: dict[str, Any] | None = None,
            *, authenticated: bool | None = None) -> dict[str, Any]:
        """Read another Trade API endpoint; path is relative to /trade-api/v2."""
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
            raise ValueError("Use an API-relative /path and pass query parameters separately")
        return self.client.get(path, params=_params(params or {}),
                               authenticated=self.authenticated if authenticated is None else authenticated)

    def iter_pages(self, path: str, *, max_pages: int = 100,
                   authenticated: bool | None = None, **params: Any) -> Iterator[dict[str, Any]]:
        """Cursor pages retaining sidecar metadata; warns when a bound truncates."""
        if max_pages < 1:
            raise ValueError("max_pages must be >= 1")
        params = dict(params)
        seen = {str(params["cursor"])} if params.get("cursor") else set()
        for _ in range(max_pages):
            page = self.get(path, params, authenticated=authenticated)
            if not isinstance(page, dict):
                raise ValueError(f"Kalshi {path} response is not an object")
            cursor = page.get("cursor") or page.get("next_cursor")
            # Check before yielding a repeated page so it does not enter storage.
            if cursor and str(cursor) in seen:
                raise RuntimeError(f"Kalshi repeated a pagination cursor at {path}; extraction stopped")
            yield page
            if not cursor:
                return
            seen.add(str(cursor))
            params["cursor"] = cursor
        warnings.warn(f"Kalshi {path} reached max_pages={max_pages} with more data available; "
                      "results are incomplete. Increase the bound or resume from the saved cursor.",
                      RuntimeWarning, stacklevel=2)

    def iter_rows(self, path: str, key: str, *, max_pages: int = 100,
                  authenticated: bool | None = None, **params: Any) -> Iterator[dict[str, Any]]:
        for page in self.iter_pages(path, max_pages=max_pages, authenticated=authenticated, **params):
            if key not in page:
                raise ValueError(f"Kalshi {path} response is missing expected field {key}")
            rows = page.get(key)
            if rows is None:
                rows = []  # Kalshi occasionally uses null for empty collections.
            if not isinstance(rows, list):
                raise ValueError(f"Kalshi {path} response field {key} is not a list")
            yield from rows

    def get_series_list(self, **params: Any) -> dict[str, Any]:
        return self.get("/series", params)

    def get_series(self, series_ticker: str, **params: Any) -> dict[str, Any]:
        return self.get(f"/series/{_part(series_ticker)}", params)

    def get_events(self, **params: Any) -> dict[str, Any]:
        return self.get("/events", params)

    def iter_events(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        params.setdefault("limit", 200)
        return self.iter_rows("/events", "events", max_pages=max_pages, **params)

    def get_event(self, event_ticker: str, **params: Any) -> dict[str, Any]:
        return self.get(f"/events/{_part(event_ticker)}", params)

    def get_event_metadata(self, event_ticker: str) -> dict[str, Any]:
        return self.get(f"/events/{_part(event_ticker)}/metadata")

    def get_markets(self, **params: Any) -> dict[str, Any]:
        return self.get("/markets", params)

    def iter_markets(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        params.setdefault("limit", 1000)
        return self.iter_rows("/markets", "markets", max_pages=max_pages, **params)

    def get_market(self, ticker: str) -> dict[str, Any]:
        return self.get(f"/markets/{_part(ticker)}")

    def get_orderbook(self, ticker: str, depth: int = 0) -> dict[str, Any]:
        if not 0 <= depth <= 100:
            raise ValueError("depth must be 0 (all levels) or 1–100")
        return self.get(f"/markets/{_part(ticker)}/orderbook", {"depth": depth})

    def get_orderbooks(self, tickers: Sequence[str]) -> dict[str, Any]:
        return self.get("/markets/orderbooks", {"tickers": _tickers(tickers)})

    def get_trades(self, **params: Any) -> dict[str, Any]:
        return self.get("/markets/trades", params)

    def iter_trades(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        params.setdefault("limit", 1000)
        return self.iter_rows("/markets/trades", "trades", max_pages=max_pages, **params)

    def get_candlesticks(self, series_ticker: str, ticker: str, start_ts: int,
                         end_ts: int, period_interval: int = 60, **params: Any) -> dict[str, Any]:
        return self.get(f"/series/{_part(series_ticker)}/markets/{_part(ticker)}/candlesticks",
                        {**_window(start_ts, end_ts, period_interval), **params})

    def get_batch_candlesticks(self, market_tickers: Sequence[str], start_ts: int,
                               end_ts: int, period_interval: int = 60, **params: Any) -> dict[str, Any]:
        tickers = _tickers(market_tickers)
        window = _window(start_ts, end_ts, period_interval)
        # The API returns at most 10,000 total candles. Reject a potentially
        # truncated query rather than silently treating a partial series as complete.
        if ((end_ts - start_ts) // (period_interval * 60) + 2) * len(tickers) > 10_000:
            raise ValueError("Batch could exceed 10,000 candles; split tickers/time windows")
        return self.get("/markets/candlesticks", {"market_tickers": ",".join(tickers), **window, **params})

    def get_event_candlesticks(self, series_ticker: str, event_ticker: str, start_ts: int,
                               end_ts: int, period_interval: int = 60) -> dict[str, Any]:
        return self.get(f"/series/{_part(series_ticker)}/events/{_part(event_ticker)}/candlesticks",
                        _window(start_ts, end_ts, period_interval))

    def get_forecast_percentile_history(self, series_ticker: str, event_ticker: str,
                                        percentiles: Sequence[int], start_ts: int, end_ts: int,
                                        period_interval: int = 60) -> dict[str, Any]:
        values = list(percentiles)
        if not 1 <= len(values) <= 10 or any(not isinstance(p, int) or not 0 <= p <= 9999 for p in values):
            raise ValueError("Provide 1–10 integer percentiles from 0 to 9999 (5000 = median)")
        return self.get(f"/series/{_part(series_ticker)}/events/{_part(event_ticker)}/forecast_percentile_history",
                        {**_window(start_ts, end_ts, period_interval, forecast=True), "percentiles": values})

    def get_historical_cutoff(self) -> dict[str, Any]:
        return self.get("/historical/cutoff")

    def get_historical_market(self, ticker: str) -> dict[str, Any]:
        return self.get(f"/historical/markets/{_part(ticker)}")

    def iter_historical_markets(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        params.setdefault("limit", 1000)
        return self.iter_rows("/historical/markets", "markets", max_pages=max_pages, **params)

    def iter_historical_trades(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        params.setdefault("limit", 1000)
        return self.iter_rows("/historical/trades", "trades", max_pages=max_pages, **params)

    def get_historical_candlesticks(self, ticker: str, start_ts: int, end_ts: int,
                                    period_interval: int = 60) -> dict[str, Any]:
        return self.get(f"/historical/markets/{_part(ticker)}/candlesticks",
                        _window(start_ts, end_ts, period_interval))

    def iter_candlesticks(self, series_ticker: str, ticker: str, start_ts: int, end_ts: int,
                          period_interval: int = 60, *, historical: bool = False,
                          max_windows: int = 100, bars_per_window: int = 1000) -> Iterator[dict[str, Any]]:
        """Bounded time-window extraction; historical route chosen by settlement cutoff."""
        _window(start_ts, end_ts, period_interval)
        if max_windows < 1 or not 1 <= bars_per_window <= 1000:
            raise ValueError("max_windows >= 1 and bars_per_window between 1 and 1000 required")
        cursor, seen = start_ts, set()
        for _ in range(max_windows):
            stop = min(end_ts, cursor + period_interval * 60 * bars_per_window - 1)
            page = (self.get_historical_candlesticks(ticker, cursor, stop, period_interval) if historical
                    else self.get_candlesticks(series_ticker, ticker, cursor, stop, period_interval))
            for row in page.get("candlesticks") or []:
                stamp = row.get("end_period_ts")
                if stamp is None or stamp not in seen:
                    yield row
                    if stamp is not None:
                        seen.add(stamp)
            if stop >= end_ts:
                return
            cursor = stop + 1
        warnings.warn(f"Candlestick extraction reached max_windows={max_windows}; results are incomplete",
                      RuntimeWarning, stacklevel=2)

    def get_tags_by_categories(self) -> dict[str, Any]:
        return self.get("/search/tags_by_categories")

    def get_exchange_status(self) -> dict[str, Any]:
        return self.get("/exchange/status")

    def get_exchange_schedule(self) -> dict[str, Any]:
        return self.get("/exchange/schedule")

    def get_series_fee_changes(self, **params: Any) -> dict[str, Any]:
        return self.get("/series/fee_changes", params)

    def get_event_fee_changes(self, **params: Any) -> dict[str, Any]:
        return self.get("/events/fee_changes", params)

    def iter_milestones(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        return self.iter_rows("/milestones", "milestones", max_pages=max_pages, **params)

    def get_milestone(self, milestone_id: str) -> dict[str, Any]:
        return self.get(f"/milestones/{_part(milestone_id)}")

    def iter_structured_targets(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        return self.iter_rows("/structured_targets", "structured_targets", max_pages=max_pages, **params)

    def get_structured_target(self, target_id: str) -> dict[str, Any]:
        return self.get(f"/structured_targets/{_part(target_id)}")

    def get_live_data(self, milestone_id: str, **params: Any) -> dict[str, Any]:
        return self.get(f"/live_data/milestone/{_part(milestone_id)}", params)

    def get_batch_live_data(self, milestone_ids: Sequence[str], **params: Any) -> dict[str, Any]:
        return self.get("/live_data/batch", {"milestone_ids": list(milestone_ids), **params})

    def get_event_live_data(self, event_ticker: str, **params: Any) -> dict[str, Any]:
        return self.get(f"/live_data/events/{_part(event_ticker)}", params)

    def iter_multivariate_events(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        return self.iter_rows("/events/multivariate", "events", max_pages=max_pages, **params)

    def get_multivariate_collection(self, collection_ticker: str) -> dict[str, Any]:
        return self.get(f"/multivariate_event_collections/{_part(collection_ticker)}")

    def iter_multivariate_collections(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        return self.iter_rows("/multivariate_event_collections", "multivariate_contracts", max_pages=max_pages, **params)

    def iter_incentive_programs(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        return self.iter_rows("/incentive_programs", "incentive_programs", max_pages=max_pages, **params)

    def get_balance(self, **params: Any) -> dict[str, Any]:
        return self.get("/portfolio/balance", params, authenticated=True)

    def get_positions(self, **params: Any) -> dict[str, Any]:
        """Full page retaining both market_positions and event_positions."""
        return self.get("/portfolio/positions", params, authenticated=True)

    def iter_positions(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        return self.iter_rows("/portfolio/positions", "market_positions", max_pages=max_pages, authenticated=True, **params)

    def iter_fills(self, *, max_pages: int = 100, historical: bool = False, **params: Any) -> Iterator[dict[str, Any]]:
        path = "/historical/fills" if historical else "/portfolio/fills"
        return self.iter_rows(path, "fills", max_pages=max_pages, authenticated=True, **params)

    def iter_orders(self, *, max_pages: int = 100, historical: bool = False, **params: Any) -> Iterator[dict[str, Any]]:
        path = "/historical/orders" if historical else "/portfolio/orders"
        return self.iter_rows(path, "orders", max_pages=max_pages, authenticated=True, **params)

    def get_order(self, order_id: str, **params: Any) -> dict[str, Any]:
        return self.get(f"/portfolio/orders/{_part(order_id)}", params, authenticated=True)

    def iter_settlements(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        return self.iter_rows("/portfolio/settlements", "settlements", max_pages=max_pages, authenticated=True, **params)

    def iter_historical_positions(self, *, max_pages: int = 100, **params: Any) -> Iterator[dict[str, Any]]:
        return self.iter_rows("/historical/positions", "market_positions", max_pages=max_pages, authenticated=True, **params)

    def get_account_limits(self) -> dict[str, Any]:
        return self.get("/account/limits", authenticated=True)

    def get_endpoint_costs(self) -> dict[str, Any]:
        return self.get("/account/endpoint_costs", authenticated=True)

    def get_cfbenchmarks(self, path: str = "values", **params: Any) -> dict[str, Any]:
        """Entitled accounts only; e.g. values or history/values, id='BRTI'."""
        if path.startswith("/") or any(p in {"", ".", ".."} for p in path.split("/")):
            raise ValueError("Use a relative CF Benchmarks endpoint such as history/values")
        return self.get("/cfbenchmarks/" + path, params, authenticated=True)
