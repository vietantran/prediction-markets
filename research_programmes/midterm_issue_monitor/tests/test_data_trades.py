"""Protect financial units, time boundaries, missingness and flow interpretation."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from midterm_monitor.data import daily_rows, normalized_bar, session_quotes, timestamp
from midterm_monitor.trades import normalize, tape_summary


def test_price_units_and_crossed_quotes():
    base = {"end_period_ts": 100, "yes_bid": {"close": 42}, "yes_ask": {"close": 44}}
    assert normalized_bar(base)["mid"] == pytest.approx(.43)
    hist = {"end_period_ts": 100, "yes_bid": {"close": .42}, "yes_ask": {"close": .44}}
    assert normalized_bar(hist, source_tier="historical")["mid"] == pytest.approx(.43)
    base["yes_bid"] = {"close_dollars": ".45"}
    assert normalized_bar(base)["mid"] is None


def test_daily_midnight_and_missing_volume():
    ts = timestamp("2026-08-12T00:00:00Z")
    raw = {"end_period_ts": ts, "volume_fp": None, "open_interest_fp": "12.5"}
    day = daily_rows("X", [normalized_bar(raw)])[0]
    assert day["utc_date"] == "2026-08-11"
    assert day["volume"] is None
    assert day["volume_hours"] == 0
    assert day["oi"] == 12.5


def test_equity_session_never_uses_future_bar():
    bars = [normalized_bar({"end_period_ts": timestamp(t), "yes_bid": {"close_dollars": ".4"},
                           "yes_ask": {"close_dollars": ".5"}}) for t in ["2026-08-11T19:00:00Z", "2026-08-11T21:00:00Z"]]
    row = session_quotes("X", bars, ["2026-08-11"])[0]
    assert row["bar_timestamp"] == bars[0]["ts"]
    assert row["quote_age_hours"] == 1
    assert row["quote_status"] == "valid"


def trade(date="2026-08-11T00:00:00Z", **overrides):
    return {"trade_id": "test", "ticker": "X", "created_time": date, "count_fp": "2.5",
            "yes_price_dollars": ".40", "no_price_dollars": ".60", "taker_outcome_side": "yes",
            "is_block_trade": False, **overrides}


def test_trade_interval_and_fractional_quantity():
    lo, hi = timestamp("2026-08-11T00:00:00Z"), timestamp("2026-08-12T00:00:00Z")
    row = normalize(trade(), "X", lo, hi)
    assert row["quantity"] == 2.5
    assert row["taker_premium_usd"] == 1
    assert normalize(trade("2026-08-12T00:00:00Z"), "X", lo, hi) is None
    assert normalize(trade("2026-08-10T23:59:59.999Z"), "X", lo, hi) is None
    with pytest.raises(ValueError):
        normalize(trade(no_price_dollars=".50"), "X", lo, hi)
    with pytest.raises(ValueError):
        normalize(trade(count_fp="NaN"), "X", lo, hi)


def test_unknown_block_and_direction_are_not_invented():
    lo, hi = timestamp("2026-08-11T00:00:00Z"), timestamp("2026-08-12T00:00:00Z")
    rows = [normalize(trade(), "X", lo, hi), normalize(trade(is_block_trade=True, count_fp="20"), "X", lo, hi),
            normalize(trade(is_block_trade=None, count_fp="10", taker_outcome_side=None), "X", lo, hi)]
    result = tape_summary(pd.DataFrame(rows))
    assert result["contracts_traded"] == 32.5
    assert result["nonblock_directional_contracts"] == 2.5
    assert result["nonblock_yes_taker_share"] == 1
    assert result["unknown_block_volume_share"] == pytest.approx(10 / 32.5)


def test_complete_frozen_artifacts_have_expected_integrity():
    root = Path(__file__).resolve().parents[1]
    daily = pd.read_csv(root / "inputs/market_daily.csv")
    assert not daily.duplicated(["ticker", "utc_date"]).any()
    assert daily.hours.max() <= 24
    assert not daily.volume.lt(0).any()
    assert not daily.oi.lt(0).any()
    assert daily.hours.sum() == 1276909
    quotes = pd.read_csv(root / "inputs/market_session_quotes.csv")
    close = quotes.session_close_utc.map(timestamp)
    assert not (quotes.bar_timestamp > close).any()
