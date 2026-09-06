"""Finite-duration raw WebSocket capture. Reconnects are visible; gaps are never filled in."""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from .http import utc_now

POLYMARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_RTDS = "wss://ws-live-data.polymarket.com"
KALSHI_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
KALSHI_DEMO_WS = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"


def polymarket_subscription(token_ids):
    if not token_ids:
        raise ValueError("Supply at least one outcome token ID")
    return {"assets_ids": list(token_ids), "type": "market", "custom_feature_enabled": True,
            "initial_dump": True}


def kalshi_subscription(tickers, channels=None):
    if not tickers:
        raise ValueError("Supply at least one Kalshi market ticker")
    return {"id": 1, "cmd": "subscribe", "params": {
        "channels": channels or ["ticker", "trade", "orderbook_delta"], "market_tickers": list(tickers)}}


def rtds_subscription(*, event_id=None, equity=None, crypto=None):
    subscriptions = []
    if event_id is not None:
        subscriptions.append({"topic": "comments", "type": "comment_created", "filters": json.dumps({
            "parentEntityID": int(event_id), "parentEntityType": "Event"})})
    if equity:
        subscriptions.append({"topic": "equity_prices", "type": "*", "filters": json.dumps({"symbol": equity})})
    if crypto:
        subscriptions.append({"topic": "crypto_prices", "type": "update", "filters": crypto})
    if not subscriptions:
        raise ValueError("RTDS needs event_id, equity, or crypto")
    return {"action": "subscribe", "subscriptions": subscriptions}


class SequenceGap(RuntimeError):
    pass


class StreamServerError(RuntimeError):
    pass


async def capture(url, subscription, record, *, seconds=60, signer=None, heartbeat_seconds=None,
                  max_reconnects=5):
    """Write parsed frames with receipt timestamps and connection IDs to record(dict).

    Order book deltas are raw; consumers must start from a snapshot after EVERY
    reconnect/gap. This function never claims a continuous historical order book.
    """
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    deadline = time.monotonic() + seconds
    count = 0
    data_count = 0

    async def heartbeat(ws):
        while True:
            await asyncio.sleep(heartbeat_seconds)
            await ws.send("PING")

    for connection_id in range(max_reconnects + 1):
        if time.monotonic() >= deadline:
            break
        headers = signer("GET", url) if signer else None
        task = None
        sequences = {}
        try:
            async with connect(url, additional_headers=headers, ping_interval=20, ping_timeout=20,
                               open_timeout=min(15, max(0.1, deadline-time.monotonic())), close_timeout=2) as ws:
                await ws.send(json.dumps(subscription))
                record({"observed_at": utc_now(), "kind": "connected", "connection_id": connection_id,
                        "url": url, "subscription": subscription})
                if heartbeat_seconds:
                    task = asyncio.create_task(heartbeat(ws))
                while time.monotonic() < deadline:
                    try:
                        frame = await asyncio.wait_for(ws.recv(), max(0.01, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    if frame in ("PING", "ping"):
                        await ws.send("PONG" if frame == "PING" else "pong")
                        continue
                    if frame in ("PONG", "pong", ""):
                        continue
                    try:
                        message = json.loads(frame)
                    except (ValueError, TypeError):
                        message = {"text": str(frame)}
                    record({"observed_at": utc_now(), "kind": "message", "connection_id": connection_id,
                            "data": message})
                    count += 1
                    data_types = {"book", "price_change", "last_trade_price", "tick_size_change", "best_bid_ask",
                                  "new_market", "market_resolved", "ticker", "trade", "orderbook_snapshot",
                                  "orderbook_delta", "fill", "market_lifecycle_v2", "market_positions"}
                    items = message if isinstance(message, list) else [message]
                    data_count += sum(isinstance(item, dict) and (
                        item.get("event_type") in data_types or item.get("type") in data_types or
                        bool(item.get("topic") and item.get("payload"))) for item in items)
                    if isinstance(message, dict):
                        if message.get("type") == "error" or message.get("error"):
                            raise StreamServerError("Server rejected subscription; see captured frame")
                        # Kalshi orderbook messages carry per-subscription sequence numbers.
                        # Other channels do not promise this continuity rule.
                        if message.get("type") in {"orderbook_snapshot", "orderbook_delta"}:
                            sid, seq = message.get("sid"), message.get("seq")
                            if sid is not None and isinstance(seq, int):
                                previous = sequences.get(sid)
                                if previous is not None and seq != previous + 1:
                                    raise SequenceGap(f"subscription {sid}: expected {previous+1}, got {seq}")
                                sequences[sid] = seq
                return {"messages": count, "data_messages": data_count, "connections": connection_id + 1}
        except StreamServerError:
            raise
        except (OSError, TimeoutError, SequenceGap, WebSocketException) as exc:
            record({"observed_at": utc_now(), "kind": "gap", "connection_id": connection_id,
                    "error": type(exc).__name__, "detail": str(exc)[:500]})
            if connection_id >= max_reconnects:
                raise RuntimeError("WebSocket reconnect limit reached; captured data are incomplete") from exc
            await asyncio.sleep(min(2 ** connection_id, 15, max(0, deadline - time.monotonic())))
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
    return {"messages": count, "data_messages": data_count, "connections": connection_id + 1}
