from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from midterm_monitor.sensitivity import (
    align_probability,
    bh_adjust,
    equity_returns,
    fit_sensitivity,
    liquidity_validation,
    moving_block_interval,
    ols,
)


def _frame(n=43):
    rng = np.random.default_rng(5)
    delta = rng.normal(0, 0.035, n)
    market = rng.normal(0, 0.01, n)
    returns = 0.002 + 0.015 * delta / 0.10 + 1.2 * market + rng.normal(0, 0.002, n)
    return pd.DataFrame({"date": pd.bdate_range("2026-07-08", periods=n).strftime("%Y-%m-%d"),
                         "session_index": np.arange(n), "delta_p": delta, "delta_p_10pp": delta / 0.10,
                         "basket_return": returns, "market_return": market, "p": 0.5 + np.cumsum(delta),
                         "spread": 0.02, "large_move": abs(delta) >= 0.04})


def test_coefficient_is_return_per_ten_probability_points():
    frame = _frame(80)
    frame.basket_return = 0.002 + 0.015 * frame.delta_p_10pp + 1.2 * frame.market_return
    x = np.column_stack([np.ones(len(frame)), frame.delta_p_10pp, frame.market_return])
    fit = ols(frame.basket_return.to_numpy(), x)
    np.testing.assert_allclose(fit["coef"], [0.002, 0.015, 1.2], atol=1e-10)


def test_missing_quotes_do_not_bridge_equity_sessions():
    dates = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    calendar = pd.DataFrame({"date": dates, "session_index": range(4)})
    quotes = pd.DataFrame({"date": [dates[0], dates[2], dates[3]], "ticker": "A", "p": [0.4, 0.5, 0.52],
                           "bid": [0.39, 0.49, 0.51], "ask": [0.41, 0.51, 0.53], "spread": 0.02,
                           "quote_age_hours": 0.5, "quote_status": "valid"})
    result = align_probability(quotes, "A", calendar)
    assert result.delta_p.iloc[:3].isna().all()
    assert result.delta_p.iloc[3] == pytest.approx(0.02)


def test_stale_wide_and_inconsistent_quotes_are_rejected():
    dates = pd.bdate_range("2026-09-01", periods=5).strftime("%Y-%m-%d")
    calendar = pd.DataFrame({"date": dates, "session_index": range(5)})
    quotes = pd.DataFrame({"date": dates, "ticker": "A", "p": 0.5, "bid": [0.49, 0.49, 0.40, 0.49, 0.49],
                           "ask": [0.51, 0.51, 0.60, 0.51, 0.51], "spread": [0.02, 0.02, 0.2, 0.01, 0.02],
                           "quote_age_hours": [0.5, 4, 0.5, 0.5, -1], "quote_status": "valid"})
    result = align_probability(quotes, "A", calendar)
    assert result.quote_valid.tolist() == [True, False, False, False, False]


def test_returns_never_fill_missing_constituents_or_use_external_first_return():
    prices = pd.DataFrame({"date": ["2026-09-01", "2026-09-02", "2026-09-03"] * 3,
                           "ticker": ["SPY"] * 3 + ["A"] * 3 + ["B"] * 3,
                           "adjusted_close": [100, 102, 103, 20, 22, 24, 30, np.nan, 33]})
    config = {"benchmark": "SPY", "baskets": [{"id": "pair", "label": "Pair", "kind": "theme", "tickers": ["A", "B"]}]}
    panel, output = equity_returns(prices, config)
    assert panel.pair.isna().all()
    assert pd.isna(panel.market_return.iloc[0])
    assert panel.market_return.iloc[1] == pytest.approx(0.02)
    assert output["return"].isna().all()


def test_constant_probability_is_not_an_estimated_zero_sensitivity():
    frame = _frame()
    frame["delta_p"] = 0
    frame["delta_p_10pp"] = 0
    result = fit_sensitivity(frame, True, bootstrap_draws=0)
    assert result["status"] == "insufficient_probability_variation"
    assert np.isnan(result["beta_per_10pp"])


def test_bootstrap_deterministic_and_allows_calendar_missingness():
    frame = _frame(60)
    frame.loc[[3, 4, 19, 33], "delta_p_10pp"] = np.nan
    first = moving_block_interval(frame, True, draws=100, seed=7)
    assert first == moving_block_interval(frame, True, draws=100, seed=7)
    assert first[2] >= 80
    assert first[0] < 0.015 < first[1]


def test_bh_adjusts_whole_observed_family_preserving_missing():
    result = bh_adjust(np.array([0.01, 0.04, 0.03, np.nan]))
    np.testing.assert_allclose(result[:3], [0.03, 0.04, 0.04])
    assert np.isnan(result[3])


def test_duplicate_equity_rows_fail_instead_of_averaging():
    prices = pd.DataFrame({"date": ["2026-09-01"] * 2, "ticker": ["SPY"] * 2, "adjusted_close": [100, 101]})
    with pytest.raises(ValueError, match="Duplicate"):
        equity_returns(prices, {"benchmark": "SPY", "baskets": []})


def test_relative_contrast_is_difference_not_average_of_four_stocks():
    prices = pd.DataFrame({"date": ["2026-09-01", "2026-09-02"] * 3,
                           "ticker": ["SPY"] * 2 + ["A"] * 2 + ["B"] * 2,
                           "adjusted_close": [100, 101, 100, 110, 100, 103]})
    config = {"benchmark": "SPY", "baskets": [{"id": "contrast", "label": "A less B", "kind": "relative_return_contrast",
                                                "tickers": ["A", "B"], "weights": [1, -1]}]}
    panel, _ = equity_returns(prices, config)
    assert panel.contrast.iloc[1] == pytest.approx(0.07)


def test_tape_validation_rejects_sparse_and_concentrated_activity(tmp_path):
    pd.DataFrame([{"scenario_id": "thin", "period": "all28", "executions": 99, "top_five_print_volume_share": 0.8},
                  {"scenario_id": "liquid", "period": "all28", "executions": 100, "top_five_print_volume_share": 0.5}]).to_csv(tmp_path / "trade_summary.csv", index=False)
    pd.DataFrame([{"scenario_id": scenario, "date": f"2026-08-{day:02d}", "executions": 10}
                  for scenario in ["thin", "liquid"] for day in range(1, 11)]).to_csv(tmp_path / "trade_daily.csv", index=False)
    result = liquidity_validation([{"id": "thin", "ticker": "T"}, {"id": "liquid", "ticker": "L"}, {"id": "missing", "ticker": "M"}], tmp_path)
    assert result.liquidity_validation_status.tolist() == ["veto_sizing_use", "passes_descriptive_tape_gate", "not_available"]
    assert "fewer_than_100" in result.iloc[0].liquidity_veto_reasons


def test_meaningful_move_boundary_allows_float_roundoff():
    dates = ["2026-09-01", "2026-09-02"]
    quotes = pd.DataFrame({"date": dates, "ticker": "A", "p": [0.82, 0.83], "bid": [0.815, 0.825],
                           "ask": [0.825, 0.835], "spread": 0.01, "quote_age_hours": 0, "quote_status": "valid"})
    aligned = align_probability(quotes, "A", pd.DataFrame({"date": dates, "session_index": [0, 1]}))
    assert aligned.meaningful_move.tolist() == [False, True]
