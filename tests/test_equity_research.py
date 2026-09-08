"""Meaningful financial-data safeguards: point-in-time, fiscal arithmetic and missing sessions."""
from datetime import date, datetime, timezone

import pytest

from pmresearch.equity_research import (
    calculate_returns, derived_metrics, extract_fundamentals, fact_known_at, parse_prices, parse_time,
    merge_company_payloads,
)


ASOF = parse_time("2026-09-08T15:07:00Z")


def test_daily_cutoff_excludes_partial_september_8_and_requires_adjusted_prices():
    times = [int(datetime(2026, 9, d, 13, 30, tzinfo=timezone.utc).timestamp()) for d in [3, 4, 8]]
    payload = {"chart": {"result": [{"timestamp": times, "meta": {"currency": "USD"},
                                  "indicators": {"quote": [{"close": [10, 11, 12], "volume": [1, 2, 3]}],
                                                 "adjclose": [{"adjclose": [None, 10.5, 11.5]}]}}]}}
    rows, excluded = parse_prices(payload, "TEST", ASOF)
    assert excluded == 1
    assert [r["date"] for r in rows] == ["2026-09-04"]
    assert rows[0]["adjusted_close"] == 10.5


def test_filing_timestamp_excludes_future_and_handles_same_day_conservatively():
    fact = {"filed": "2026-09-08", "accn": "x"}
    assert fact_known_at(fact, {}, ASOF) is None
    assert fact_known_at(fact, {"x": {"accepted": "2026-09-08T14:00:00Z"}}, ASOF)
    assert fact_known_at(fact, {"x": {"accepted": "2026-09-08T16:00:00Z"}}, ASOF) is None


def test_ttm_uses_matched_ytd_not_quarter_plus_annual_or_future_restatement():
    def observation(start, end, val, filed):
        return {"start": start, "end": end, "val": val, "filed": filed, "accn": filed, "form": "10-Q"}
    values = [observation("2025-01-01", "2025-12-31", 100, "2026-02-01"),
              observation("2026-01-01", "2026-06-30", 70, "2026-08-01"),
              observation("2025-01-01", "2025-06-30", 40, "2026-08-01"),
              observation("2026-04-01", "2026-06-30", 35, "2026-08-01"),
              observation("2025-01-01", "2025-12-31", 900, "2026-09-09")]
    payload = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": values}}}}}
    rows = extract_fundamentals(payload, {}, {"ticker": "TEST", "cik": "1"}, ASOF)
    ttm = next(r for r in rows if r["metric"] == "revenue" and r["period_type"] == "ttm")
    assert ttm["value"] == 130
    assert ttm["period_start"] == "2025-07-01"
    assert ttm["period_end"] == "2026-06-30"


def test_missing_stock_session_is_not_forward_filled_or_hidden_in_basket():
    days = [f"2026-07-{d:02d}" for d in [8, 9, 10, 13, 14, 15]]
    rows = {"SPY": [{"date": d, "adjusted_close": 100+i} for i, d in enumerate(days)],
            "A": [{"date": d, "adjusted_close": 100+2*i} for i, d in enumerate(days)],
            "B": [{"date": d, "adjusted_close": 100+i} for i, d in enumerate(days) if i != 2]}
    universe = {"securities": [{"ticker": t, "basket": "group", "sector_benchmark": "SPY", "peer_group": "peers"} for t in ["A", "B"]], "benchmarks": ["SPY"]}
    returns, baskets, history, error = calculate_returns(rows, universe, date(2026, 7, 8))
    assert error is None
    b = next(r for r in returns if r["ticker"] == "B" and r["period"] == "common_window")
    assert b["total_return"] is None
    basket = next(r for r in baskets if r["period"] == "common_window")
    assert basket["excluded_tickers"] == "B"
    assert basket["total_return"] == pytest.approx(.1)
    assert history[-1]["nav"] == pytest.approx(110)


def test_bank_does_not_receive_nonfinancial_fcf_or_operating_margin_proxy():
    rows = [{"ticker": "BANK", "metric": metric, "period_type": "ttm", "period_start": "2025-07-01", "period_end": "2026-06-30", "value": value}
            for metric, value in [("revenue", 100), ("operating_income", 30), ("operating_cash_flow", 50), ("cash_capex", 10)]]
    assert derived_metrics(rows, {"ticker": "BANK", "bank": True}, None) == []


def test_stale_values_are_withheld_when_newer_filing_exists_and_cannot_value_stock():
    fact = {"start": "2025-01-01", "end": "2025-12-31", "val": 2,
            "filed": "2026-02-01", "accn": "old", "form": "10-K"}
    payload = {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [fact]}},
                                    "LongTermDebtNoncurrent": {"units": {"USD": [{k: v for k, v in fact.items() if k != "start"}]}}}}}
    submission = {"filings": {"recent": {"accessionNumber": ["new"], "acceptanceDateTime": ["2026-08-01T12:00:00Z"],
                                           "form": ["10-Q"], "reportDate": ["2026-06-30"], "primaryDocument": ["new.htm"]}}}
    security = {"ticker": "TEST", "cik": "1"}
    rows = extract_fundamentals(payload, submission, security, ASOF)
    eps = next(r for r in rows if r["metric"] == "diluted_eps" and r["period_type"] == "ttm")
    assert eps["status"] == "stale_value_withheld"
    assert eps["value"] is None
    assert "latest available filing report date 2026-06-30" in eps["note"]
    assert not derived_metrics(rows, security, {"close": 20, "date": "2026-09-04"})


def test_fresher_valid_tag_wins_over_old_preferred_tag_and_preserves_successor_cik():
    def fact(start, end, val, filed, accn):
        return {"start": start, "end": end, "val": val, "filed": filed, "accn": accn, "form": "10-Q"}
    old = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [fact("2024-01-01", "2024-12-31", 10, "2025-02-01", "old")]}}}}}
    fresh = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        fact("2025-01-01", "2025-12-31", 100, "2026-02-01", "annual"),
        fact("2026-01-01", "2026-06-30", 70, "2026-08-01", "current"),
        fact("2025-01-01", "2025-06-30", 40, "2026-08-01", "current")]}}}}}
    rows = extract_fundamentals(merge_company_payloads([("1", old), ("2", fresh)]), {}, {"ticker": "TEST", "cik": "1"}, ASOF)
    row = next(r for r in rows if r["metric"] == "revenue" and r["period_type"] == "ttm")
    assert row["value"] == 130
    assert row["tag"] == "Revenues"
    assert "/edgar/data/2/" in row["source_urls"]
    assert "/edgar/data/1/" not in row["source_urls"]


def test_bank_uses_net_interest_revenue_and_utility_cash_construction_is_explicit():
    fact = {"start": "2025-07-01", "end": "2026-06-30", "val": 100,
            "filed": "2026-08-01", "accn": "x", "form": "10-K"}
    payload = {"facts": {"us-gaap": {tag: {"units": {"USD": [fact]}} for tag in ["RevenuesNetOfInterestExpense", "PaymentsForConstructionInProcess"]}}}
    bank = extract_fundamentals(payload, {}, {"ticker": "BANK", "cik": "1", "bank": True}, ASOF)
    assert next(r for r in bank if r["metric"] == "revenue" and r["period_type"] == "ttm")["value"] == 100
    utility = extract_fundamentals(payload, {}, {"ticker": "UTILITY", "cik": "2", "metric_tags": {"cash_capex": ["PaymentsForConstructionInProcess"]}}, ASOF)
    assert next(r for r in utility if r["metric"] == "cash_capex" and r["period_type"] == "ttm")["value"] == 100
