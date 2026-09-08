"""Meaningful accounting, denominator and coverage checks for attention."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from midterm_monitor.attention import (  # noqa: E402
    LAST_DAY,
    OI_BASE_DAY,
    endpoint_oi,
    family_activity,
    issuance_diagnostics,
    lifecycle_diagnostic,
    oi_decomposition,
    prepare_daily,
    prepare_metadata,
)


def metadata(tickers, **overrides):
    rows = []
    for t in tickers:
        rows.append(
            {
                "ticker": t,
                "series_ticker": "FAMILY",
                "event_ticker": "EVENT",
                "channel": "policy",
                "issue_ids": "energy",
                "created_time": "2026-07-01T00:00:00Z",
                "event_created_time": "2026-07-01T00:00:00Z",
                "settlement_ts": None,
                **overrides.get(t, {}),
            }
        )
    return prepare_metadata(pd.DataFrame(rows))


def panel_row(ticker, day, volume=10, oi=5, **kw):
    d = pd.Timestamp(day)
    return {
        "ticker": ticker,
        "utc_date": day,
        "volume": volume,
        "oi": oi,
        "oi_ts": int((d + pd.Timedelta(hours=23)).timestamp()),
        "mid": 0.5,
        "bid": 0.49,
        "ask": 0.51,
        "spread": 0.02,
        "hours": 24,
        "usable_hours": 24,
        "first_ts": int(d.timestamp()),
        "last_ts": int((d + pd.Timedelta(hours=23)).timestamp()),
        **kw,
    }


def test_events_not_inflated_by_contract_thresholds():
    m = metadata(
        ["A", "B", "C"],
        A={"created_time": "2026-09-02T01:00:00Z", "event_created_time": "2026-09-02T00:00:00Z"},
        B={"created_time": "2026-09-02T02:00:00Z", "event_created_time": "2026-09-02T00:00:00Z"},
        C={"created_time": "2026-09-03T02:00:00Z", "event_created_time": "2026-09-02T00:00:00Z"},
    )
    daily, summary = issuance_diagnostics(m)
    row = summary[summary.recent_days.eq(7)].iloc[0]
    assert row.new_contracts_recent == 3
    assert row.new_events_recent == 1
    assert row.first_observed_families_recent == 1
    assert row.contracts_added_to_existing_events_recent == 1
    assert daily.new_events.sum() == 1
    assert np.isnan(row.new_events_rate_ratio)  # no artificial prior denominator


def test_rate_denominators_are_days_not_contract_counts():
    m = metadata(
        ["OLD", "NEW"],
        OLD={
            "created_time": "2026-08-15T00:00:00Z",
            "event_created_time": "2026-08-15T00:00:00Z",
            "event_ticker": "OLD_EVENT",
        },
        NEW={
            "created_time": "2026-09-02T00:00:00Z",
            "event_created_time": "2026-09-02T00:00:00Z",
            "event_ticker": "NEW_EVENT",
        },
    )
    _, summary = issuance_diagnostics(m)
    row = summary[summary.recent_days.eq(7)].iloc[0]
    assert row.new_events_rate_ratio == 4
    assert row.new_events_in_preexisting_archive_families_recent == 1


def test_oi_identity_and_observation_entry_are_separate_from_listing():
    m = metadata(
        ["CONT", "NEW", "MISSING", "SETTLED"],
        NEW={"created_time": "2026-08-30T00:00:00.123Z"},
        SETTLED={"settlement_ts": "2026-09-01T00:00:00Z"},
    )
    raw = pd.DataFrame(
        [
            panel_row("CONT", "2026-08-24", oi=100),
            panel_row("CONT", "2026-09-07", oi=120),
            panel_row("NEW", "2026-09-07", oi=30),
            panel_row("MISSING", "2026-09-07", oi=40),
            panel_row("SETTLED", "2026-08-24", oi=50),
        ]
    )
    d = prepare_daily(raw, m)
    summary, _ = oi_decomposition(d, m)
    row = summary.iloc[0]
    assert row.prior_oi == 150 and row.latest_oi == 190
    assert row.continuing_oi_change == 20
    assert row.entry_oi == 70 and row.exit_oi == 50
    assert row.entry_oi_new_listing == 30
    assert row.entry_oi_old_or_unknown_creation == 40
    assert row.exit_oi_settlement_corroborated == 50
    assert row.identity_residual == 0


def test_stale_oi_is_not_carried_and_missing_volume_is_not_zero():
    m = metadata(["A", "B"])
    raw = pd.DataFrame(
        [
            panel_row("A", "2026-09-01", volume=np.nan, oi=50),
            panel_row("B", "2026-09-07", volume=0, oi=0),
        ]
    )
    d = prepare_daily(raw, m)
    assert d.volume.isna().sum() == 1
    assert d.volume.eq(0).sum() == 1
    endpoint = endpoint_oi(d, LAST_DAY)
    assert list(endpoint.ticker) == ["B"]
    assert endpoint.oi.iloc[0] == 0


def test_fixed_panel_excludes_recent_issuance_and_preserves_missingness():
    m = metadata(["CONT", "NEW"])
    rows = []
    for day in pd.date_range("2026-07-28", "2026-09-07"):
        vol = 20 if day >= pd.Timestamp("2026-08-25") else 10
        rows.append(panel_row("CONT", str(day.date()), volume=vol))
        if day >= pd.Timestamp("2026-08-25"):
            rows.append(panel_row("NEW", str(day.date()), volume=1000))
    d = prepare_daily(pd.DataFrame(rows), m)
    family, _ = family_activity(d)
    row = family[family.recent_days.eq(14)].iloc[0]
    assert row.status == "eligible"
    assert row.continuing_contracts == 1
    assert row.volume_ratio == 2
    assert row.recent_total_volume_observed == 14 * 20


def test_sparse_volume_cannot_be_imputed_to_eligible_panel():
    m = metadata(["A"])
    rows = [
        panel_row("A", str(day.date()), volume=np.nan if day.day % 2 else 10)
        for day in pd.date_range("2026-07-28", "2026-09-07")
    ]
    d = prepare_daily(pd.DataFrame(rows), m)
    family, _ = family_activity(d)
    assert family.continuing_contracts.sum() == 0
    assert set(family.status) == {"insufficient_continuing_contract_coverage"}


def test_oi_endpoint_uses_actual_timestamp_not_daily_label():
    m = metadata(["A"])
    raw = pd.DataFrame(
        [panel_row("A", "2026-08-24", oi_ts=int(pd.Timestamp("2026-08-15T00:00Z").timestamp()))]
    )
    d = prepare_daily(raw, m)
    assert endpoint_oi(d, OI_BASE_DAY).empty


def test_calendar_match_counts_independent_events_not_many_daily_rows():
    m = metadata(
        ["OLD", "CURRENT"],
        OLD={
            "channel": "macro",
            "event_ticker": "OLD_EVENT",
            "expected_expiration_time": "2026-08-10T12:30:00Z",
        },
        CURRENT={
            "channel": "macro",
            "event_ticker": "CURRENT_EVENT",
            "expected_expiration_time": "2026-09-10T12:30:00Z",
        },
    )
    rows = [panel_row("OLD", "2026-07-30", volume=10), panel_row("CURRENT", "2026-08-30", volume=20)]
    d = prepare_daily(pd.DataFrame(rows), m)
    result = lifecycle_diagnostic(d)
    row = result[result.recent_days.eq(14)].iloc[0]
    assert row.independent_past_events == 1
    assert row.lead_matched_activity_ratio == 2
    assert row.status == "insufficient_independent_past_events_or_days"
