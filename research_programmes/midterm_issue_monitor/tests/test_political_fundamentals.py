"""Tests for evidence timing, interpretation and underwriting arithmetic."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


BASE = Path(__file__).resolve().parents[1]


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, BASE / "src" / "midterm_monitor" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fundamentals = load_module("fundamentals")
political = load_module("political")
ASOF = pd.Timestamp("2026-09-08T15:07:00Z")


def test_future_and_ambiguous_cutoff_evidence_is_excluded():
    assert not political.eligible_record({"as_of_eligible": True, "date": "2026-09-08"}, ASOF)
    assert not political.eligible_record(
        {"as_of_eligible": True, "published_at": "2026-09-08T16:00:00Z"}, ASOF
    )
    assert political.eligible_record({"as_of_eligible": True, "published_at": "2026-09-08T14:00:00Z"}, ASOF)
    assert political.eligible_record(
        {"as_of_eligible": True, "publication_precision": "undated_standing_background"}, ASOF
    )


def test_joint_probabilities_are_not_normalized():
    config = {"institutional_scenarios": [{"scenario": "A", "ticker": "A"}, {"scenario": "B", "ticker": "B"}]}
    quotes = pd.DataFrame(
        [
            {
                "ticker": "A",
                "mid_probability": 0.7,
                "quote_status": "usable",
                "quote_observed_utc": "2026-09-08T15:00:00Z",
            },
            {
                "ticker": "B",
                "mid_probability": 0.4,
                "quote_status": "usable",
                "quote_observed_utc": "2026-09-08T15:00:00Z",
            },
        ]
    )
    result = political.institutional_table(config, quotes, ASOF)
    assert result["mid_probability"].sum() == pytest.approx(1.1)
    quotes.loc[0, "quote_observed_utc"] = "2026-09-08T16:00:00Z"
    result = political.institutional_table(config, quotes, ASOF)
    assert pd.isna(result.loc[0, "mid_probability"])


def test_case_arithmetic_and_hurdle_are_price_only():
    case = fundamentals.calculate_case(10, 200, 0.1, 20, 2, 0.1)
    assert case["terminal_price_usd"] == pytest.approx(242)
    assert case["price_return"] == pytest.approx(0.21)
    assert case["annualized_price_return"] == pytest.approx(0.1)
    assert case["break_even_eps_cagr"] == pytest.approx(0)
    assert case["eps_cagr_for_hurdle"] == pytest.approx(0.1)
    assert case["maximum_entry_for_hurdle_usd"] == pytest.approx(200)
    assert case["required_entry_discount"] == pytest.approx(0)
    with pytest.raises(ValueError):
        fundamentals.calculate_case(-1, 200, 0.1, 20, 2, 0.1)


def test_stale_or_late_facts_are_withheld_without_annual_fallback():
    base = {
        "ticker": "TEST",
        "metric": "diluted_eps",
        "period_type": "ttm",
        "status": "observed",
        "value": 10,
        "period_end": "2026-06-30",
        "latest_known_at": "2026-07-15T10:00:00Z",
    }
    assert fundamentals.eligible_fact(pd.DataFrame([base]), "TEST", "diluted_eps", ASOF) is not None
    for update in [
        {"status": "stale_value_withheld"},
        {"period_end": "2025-12-31"},
        {"latest_known_at": "2026-09-09T00:00:00Z"},
        {"latest_known_at": None},
        {"period_type": "annual"},
    ]:
        assert (
            fundamentals.eligible_fact(pd.DataFrame([{**base, **update}]), "TEST", "diluted_eps", ASOF)
            is None
        )


def test_intraday_freeze_does_not_take_same_day_close():
    prices = pd.DataFrame(
        [
            {"ticker": "TEST", "date": "2026-09-04", "close": 100},
            {"ticker": "TEST", "date": "2026-09-08", "close": 105},
        ]
    )
    assert fundamentals.latest_completed_price(prices, "TEST", ASOF)["close"] == 100


def test_real_packet_preserves_cross_party_and_policy_distinctions(tmp_path):
    if not (BASE / "inputs" / "public_evidence.json").exists():
        pytest.skip("Frozen inputs unavailable")
    summary = political.run(BASE / "inputs", tmp_path)
    assert summary["joint_normalized"] is False
    assert summary["minimum_partisan_negative_cost_benefit_percent"] == {"P05": 66, "P06": 64}
    states = pd.read_csv(tmp_path / "state_policy_diffusion.csv")
    assert states["policy_class"].nunique() == 5
    assert not states["binding_moratorium_established"].any()
    assert summary["case_moratorium_count"] is None


def test_real_underwriting_normalizes_msft_and_omits_bank_fcf(tmp_path):
    if not (BASE / "inputs" / "company_fundamentals.csv").exists():
        pytest.skip("Frozen inputs unavailable")
    summary = fundamentals.run(BASE / "inputs", tmp_path)
    assert summary["companies"] == 7
    assert summary["scenario_rows"] == 42
    cases = pd.read_csv(tmp_path / "fundamental_scenarios.csv")
    assert not cases["dividends_included"].any()
    assert cases["probability_weight"].isna().all()
    assert cases.loc[cases.ticker.eq("MSFT"), "starting_eps_usd"].unique()[0] == pytest.approx(17.28)
    metrics = pd.read_csv(tmp_path / "financial_sensitivities.csv").set_index("ticker")
    assert pd.isna(metrics.loc["JPM", "cash_fcf_proxy_usd"])
    assert pd.isna(metrics.loc["JPM", "operating_margin"])
    assert metrics.loc["UNH", "ebit_fraction_change_100bp_margin"] == pytest.approx(
        0.01 / metrics.loc["UNH", "operating_margin"]
    )
