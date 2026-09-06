import asyncio
import json

import pytest

from pmresearch.streams import capture, kalshi_subscription, polymarket_subscription, rtds_subscription


def test_subscription_identifiers_are_not_interchangeable():
    assert polymarket_subscription(["123"])["assets_ids"] == ["123"]
    assert kalshi_subscription(["KXTEST-26"])["params"]["market_tickers"] == ["KXTEST-26"]
    s = rtds_subscription(event_id=100)
    assert json.loads(s["subscriptions"][0]["filters"])["parentEntityID"] == 100
    with pytest.raises(ValueError):
        polymarket_subscription([])


def test_sequence_gap_is_logged_and_does_not_look_continuous(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.frames = iter([
                {"type": "orderbook_snapshot", "sid": 1, "seq": 1},
                {"type": "orderbook_delta", "sid": 1, "seq": 3},
            ])
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, frame):
            pass
        async def recv(self):
            return json.dumps(next(self.frames))
    monkeypatch.setattr("pmresearch.streams.connect", lambda *a, **k: FakeSocket())
    records = []
    with pytest.raises(RuntimeError, match="reconnect limit"):
        asyncio.run(capture("wss://example.test", {}, records.append, seconds=1, max_reconnects=0))
    assert records[-1]["kind"] == "gap"
    assert records[-1]["error"] == "SequenceGap"


def test_subscription_acknowledgement_is_not_market_data(monkeypatch):
    class FakeSocket:
        done = False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, frame):
            pass
        async def recv(self):
            if self.done:
                raise TimeoutError
            self.done = True
            return json.dumps({"type": "subscribed", "msg": {"channel": "ticker"}})
    monkeypatch.setattr("pmresearch.streams.connect", lambda *a, **k: FakeSocket())
    result = asyncio.run(capture("wss://example.test", {}, lambda x: None, seconds=1))
    assert result["messages"] == 1
    assert result["data_messages"] == 0
