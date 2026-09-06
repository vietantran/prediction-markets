from types import SimpleNamespace

import pytest

from pmresearch.http import APIError
from pmresearch.pipeline import collect, discover, epoch, time_windows
from pmresearch.storage import RunStore, read_jsonl
from pmresearch.topics import TopicClassifier


def test_discovery_deduplicates_and_retains_rejected_candidates(tmp_path):
    event = {"id": "e", "title": "2026 US Senate election", "markets": [
        {"id": "m", "question": "Will Democrats control the Senate in 2026?", "active": True,
         "outcomes": '["Yes","No"]', "outcomePrices": '["0.55","0.45"]',
         "clobTokenIds": '["a","b"]', "conditionId": "0xabc"}]}
    poly = SimpleNamespace(gamma=SimpleNamespace(iter_search=lambda *a, **k: iter([event, event])))
    store = RunStore(tmp_path)
    try:
        rows, coverage = discover(poly, None, TopicClassifier(), store, platform="polymarket",
                                  queries=["Senate", "election"], max_pages=1)
        assert len(rows) == 2
        assert coverage["candidate_markets"] == 1
        assert len(read_jsonl(store.path / "candidates.jsonl")) == 1
    finally:
        store.close()


def test_collection_uses_token_for_history_condition_for_trades(tmp_path):
    calls = []
    def history(token, **params):
        calls.append(("history", token, params))
        return {"history": [{"t": 10, "p": 0.55}]}
    def trades(**params):
        calls.append(("trades", params))
        return iter([])
    poly = SimpleNamespace(clob=SimpleNamespace(price_history=history), data=SimpleNamespace(iter_trades=trades))
    store = RunStore(tmp_path)
    try:
        result = collect(poly, None, store, [{"platform": "polymarket", "market_id": "gamma-id",
             "condition_id": "0xcondition", "token_id": "outcome-token", "outcome": "Yes"}],
             datasets=["history", "trades"], start=1, end=15 * 86400)
        assert calls[0][1] == "outcome-token"
        assert calls[-1][1]["market"] == ["0xcondition"]
        assert len(result[0]["outcomes"][0]["history"]) == 1
    finally:
        store.close()


def test_kalshi_old_market_candles_and_overlapping_trades(tmp_path):
    trade = {"trade_id": "x", "yes_price_dollars": "0.25"}
    def missing(*a, **k):
        raise APIError("missing", status_code=404)
    kalshi = SimpleNamespace(
        get_historical_cutoff=lambda: {"trades_created_ts": 100},
        iter_trades=lambda **k: iter([trade]),
        iter_historical_trades=lambda **k: iter([trade]),
        get_candlesticks=missing,
        get_historical_candlesticks=lambda *a, **k: {"candlesticks": [{"end_period_ts": 50}]},
    )
    store = RunStore(tmp_path)
    try:
        result = collect(None, kalshi, store, [{"platform": "kalshi", "market_id": "M", "series_id": "S"}],
                         datasets=["history", "trades"], start=1, end=200)
        assert len(result[0]["trades"]) == 1
        assert result[0]["candlesticks"][0]["end_period_ts"] == 50
        assert not store.errors
    finally:
        store.close()


def test_epoch_timezone_and_window_bounds():
    assert epoch("2026-01-01T00:00:00Z") == 1767225600
    with pytest.raises(ValueError, match="timezone"):
        epoch("2026-01-01")
    assert list(time_windows(1, 10, 4)) == [(1, 5), (5, 9), (9, 10)]
    with pytest.raises(ValueError):
        list(time_windows(10, 1, 4))
