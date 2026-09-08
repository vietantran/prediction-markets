"""Transparent issue attention statistics from a frozen public-data panel.

All inference is about the observed Kalshi marketplace, not voter priorities or
trader identities. Missing candles/volume are never silently replaced by zero.
The broad metadata inventory is used for issuance diagnostics only; activity
uses the explicitly selected and collected study cohort.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ASOF = pd.Timestamp("2026-09-08T15:07:00Z")
FIRST_DAY = pd.Timestamp("2026-07-09T00:00:00Z")
LAST_DAY = pd.Timestamp("2026-09-07T00:00:00Z")
OI_BASE_DAY = pd.Timestamp("2026-08-24T00:00:00Z")
MIN_COVERAGE = 0.8
MIN_FAMILIES = 3
MAX_OI_AGE_DAYS = 2


def _date(value):
    """Accept both epoch seconds and ISO values without treating blanks as 0."""
    s = pd.Series(value, copy=True)
    numeric = pd.to_numeric(s, errors="coerce")
    result = pd.to_datetime(s.where(numeric.isna()), errors="coerce", utc=True, format="mixed")
    is_epoch = numeric.notna()
    if is_epoch.any():
        result.loc[is_epoch] = pd.to_datetime(numeric[is_epoch], unit="s", utc=True)
    return result


def _as_numeric(frame, names):
    for name in names:
        if name not in frame:
            frame[name] = np.nan
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return frame


def prepare_metadata(index):
    """Validate unique contracts and create explicit, nonexclusive issue tags."""
    m = index.copy()
    if m.ticker.duplicated().any():
        raise ValueError("Duplicate ticker in market metadata")
    for c in [
        "created_time",
        "event_created_time",
        "settlement_ts",
        "close_time",
        "expected_expiration_time",
    ]:
        if c not in m:
            m[c] = None
        m[c + "_parsed"] = _date(m[c])
    for c in ["series_ticker", "event_ticker", "channel", "issue_ids"]:
        if c not in m:
            m[c] = ""
        m[c] = m[c].fillna("").astype(str)
    if (m.series_ticker.eq("") | m.event_ticker.eq("")).any():
        raise ValueError("Missing event or series keys")
    m = m[m.created_time_parsed.isna() | (m.created_time_parsed <= ASOF)].copy()
    # Actual event creation is preferable. The fallback is labelled, and can
    # only mean earliest contract creation within this left-truncated archive.
    event_actual = m.groupby("event_ticker")["event_created_time_parsed"].min()
    event_contract = m.groupby("event_ticker")["created_time_parsed"].min()
    m["event_created"] = m.event_ticker.map(event_actual.fillna(event_contract))
    m["event_creation_basis"] = np.where(
        m.event_ticker.map(event_actual).notna(),
        "api_event_created_time",
        "earliest_contract_in_archive",
    )
    m["series_first_observed_creation"] = m.series_ticker.map(
        m.groupby("series_ticker")["event_created"].min()
    )
    m["issues"] = m.issue_ids.map(lambda s: [x for x in s.split("|") if x])
    m.loc[m.issues.map(len).eq(0), "issues"] = m.loc[m.issues.map(len).eq(0), "channel"].map(
        lambda c: ["electoral_context"] if c == "election" else ["unclassified"]
    )
    return m


def prepare_daily(daily, metadata):
    d = daily.copy()
    d["day"] = pd.to_datetime(d.utc_date, utc=True, errors="coerce").dt.floor("D")
    if d[["ticker", "day"]].duplicated().any():
        raise ValueError("Duplicate contract-day observation")
    d = _as_numeric(
        d,
        [
            "volume",
            "oi",
            "oi_ts",
            "mid",
            "bid",
            "ask",
            "spread",
            "hours",
            "usable_hours",
            "first_ts",
            "last_ts",
        ],
    )
    if (d.volume.dropna() < 0).any() or (d.oi.dropna() < 0).any():
        raise ValueError("Negative contract volume or open interest")
    unknown = set(d.ticker) - set(metadata.ticker)
    if unknown:
        raise ValueError(f"Daily panel includes {len(unknown)} unknown contracts")
    d = d[d.day.between(FIRST_DAY, LAST_DAY)].copy()
    cols = [
        "ticker",
        "series_ticker",
        "event_ticker",
        "channel",
        "issues",
        "created_time_parsed",
        "event_created",
        "settlement_ts_parsed",
        "expected_expiration_time_parsed",
    ]
    return d.merge(metadata[cols], on="ticker", how="left", validate="many_to_one")


def issuance_diagnostics(metadata):
    """Count contracts and questions separately; archive-first series != novelty."""
    m = metadata.explode("issues").rename(columns={"issues": "issue_id"})
    m["creation_day"] = m.created_time_parsed.dt.floor("D")
    m["event_creation_day"] = m.event_created.dt.floor("D")
    m["series_creation_day"] = m.series_first_observed_creation.dt.floor("D")
    keys = ["issue_id", "channel"]
    contracts = (
        m.groupby(keys + ["creation_day"])
        .agg(
            new_contracts=("ticker", "nunique"),
            affected_events=("event_ticker", "nunique"),
            affected_families=("series_ticker", "nunique"),
        )
        .reset_index()
        .rename(columns={"creation_day": "day"})
    )
    # Additional contracts on later dates are an observable threshold/outcome
    # extension diagnostic; same-day outcomes are shown via contracts/event.
    later = (
        m[m.creation_day > m.event_creation_day]
        .groupby(keys + ["creation_day"])["ticker"]
        .nunique()
        .rename("contracts_added_to_existing_events")
        .reset_index()
        .rename(columns={"creation_day": "day"})
    )
    events = m.drop_duplicates(keys + ["event_ticker"])
    event_counts = (
        events.groupby(keys + ["event_creation_day"])
        .agg(
            new_events=("event_ticker", "nunique"),
            families_with_new_events=("series_ticker", "nunique"),
        )
        .reset_index()
        .rename(columns={"event_creation_day": "day"})
    )
    recurring = events[events.event_creation_day > events.series_creation_day]
    recurring_counts = (
        recurring.groupby(keys + ["event_creation_day"])["event_ticker"]
        .nunique()
        .rename("new_events_in_preexisting_archive_families")
        .reset_index()
        .rename(columns={"event_creation_day": "day"})
    )
    first_series = (
        m.drop_duplicates(keys + ["series_ticker"])
        .groupby(keys + ["series_creation_day"])["series_ticker"]
        .nunique()
        .rename("first_observed_families")
        .reset_index()
        .rename(columns={"series_creation_day": "day"})
    )
    result = contracts
    for other in [later, event_counts, recurring_counts, first_series]:
        result = result.merge(other, on=keys + ["day"], how="outer")
    result = result[result.day.between(FIRST_DAY, LAST_DAY)].copy()
    # Zero is legitimate for absence of a creation record in a metadata census.
    countcols = [c for c in result if c not in keys + ["day"]]
    result[countcols] = result[countcols].fillna(0).astype(int)
    rows = []
    for (issue, channel), frame in result.groupby(keys):
        for days in [7, 14]:
            recent_start = LAST_DAY - pd.Timedelta(days=days - 1)
            previous_start = recent_start - pd.Timedelta(days=28)
            recent = frame[frame.day.between(recent_start, LAST_DAY)]
            prior = frame[frame.day.ge(previous_start) & frame.day.lt(recent_start)]
            row = {
                "issue_id": issue,
                "channel": channel,
                "recent_days": days,
                "recent_start": recent_start.date().isoformat(),
                "prior_start": previous_start.date().isoformat(),
                "prior_days": 28,
            }
            for col in countcols:
                a, b = int(recent[col].sum()), int(prior[col].sum())
                row[col + "_recent"] = a
                row[col + "_prior"] = b
                row[col + "_per_day_recent"] = a / days
                row[col + "_per_day_prior"] = b / 28
                row[col + "_rate_ratio"] = (a / days) / (b / 28) if b else np.nan
            row["contracts_per_new_event_recent"] = (
                row["new_contracts_recent"] / row["new_events_recent"] if row["new_events_recent"] else np.nan
            )
            row["creation_scope"] = "classified_metadata_archive_not_historical_website_census"
            rows.append(row)
    return result.sort_values(keys + ["day"]), pd.DataFrame(rows)


def issuance_family_detail(metadata):
    """Expose which products account for a headline rise in creation counts."""
    expanded = metadata.explode("issues").rename(columns={"issues": "issue_id"})
    rows = []
    for (issue, channel, family), frame in expanded.groupby(["issue_id", "channel", "series_ticker"]):
        for days in [7, 14]:
            prior_start, recent_start, end = _window(days)
            recent = frame[
                frame.created_time_parsed.ge(recent_start)
                & frame.created_time_parsed.lt(end + pd.Timedelta(days=1))
            ]
            prior = frame[
                frame.created_time_parsed.ge(prior_start) & frame.created_time_parsed.lt(recent_start)
            ]
            if recent.empty and prior.empty:
                continue
            new_events = frame[
                frame.event_created.ge(recent_start) & frame.event_created.lt(end + pd.Timedelta(days=1))
            ]
            prior_events = frame[frame.event_created.ge(prior_start) & frame.event_created.lt(recent_start)]
            rows.append(
                {
                    "issue_id": issue,
                    "channel": channel,
                    "series_ticker": family,
                    "series_title": frame.series_title.iloc[0] if "series_title" in frame else family,
                    "recent_days": days,
                    "recent_new_contracts": len(recent),
                    "prior_new_contracts": len(prior),
                    "recent_new_events": new_events.event_ticker.nunique(),
                    "prior_new_events": prior_events.event_ticker.nunique(),
                    "contract_creation_rate_ratio": (len(recent) / days) / (len(prior) / 28)
                    if len(prior)
                    else np.nan,
                    "contracts_per_affected_event_recent": len(recent) / recent.event_ticker.nunique()
                    if len(recent)
                    else np.nan,
                    "archive_first_family_creation": frame.series_first_observed_creation.iloc[0],
                    "family_first_observed_within_recent_window": bool(
                        frame.series_first_observed_creation.iloc[0] >= recent_start
                    ),
                    "is_genuinely_new_issue": "not_identifiable_from_metadata",
                }
            )
    return pd.DataFrame(rows)


def endpoint_oi(daily, day, max_age_days=MAX_OI_AGE_DAYS):
    """Use fresh observed OI only; no carry beyond a two-day maximum age."""
    end = day + pd.Timedelta(days=1)
    d = daily[daily.oi.notna() & daily.day.le(day)].copy()
    d["observed_at"] = pd.to_datetime(d.oi_ts, unit="s", utc=True, errors="coerce")
    # OI observation timestamps are mandatory for freshness: a dated row alone
    # does not prove that its OI is contemporary.
    d = d[d.observed_at.notna() & d.observed_at.le(end)]
    d = d[d.observed_at.ge(end - pd.Timedelta(days=max_age_days))]
    return d.sort_values("observed_at").drop_duplicates("ticker", keep="last")


def oi_decomposition(daily, metadata, baseline=OI_BASE_DAY, latest=LAST_DAY):
    """Exact accounting identity over fresh, observed endpoint panels.

    'Entry' and 'exit' describe observation-panel membership. Listing/settlement
    attribution requires corroborating dates and is reported separately.
    """
    prior = endpoint_oi(daily, baseline)[["ticker", "oi", "observed_at"]].rename(
        columns={"oi": "oi_prior", "observed_at": "oi_prior_observed"}
    )
    current = endpoint_oi(daily, latest)[["ticker", "oi", "observed_at"]].rename(
        columns={"oi": "oi_latest", "observed_at": "oi_latest_observed"}
    )
    panel = prior.merge(current, on="ticker", how="outer", validate="one_to_one")
    panel = panel.merge(
        metadata[["ticker", "issues", "channel", "created_time_parsed", "settlement_ts_parsed"]],
        on="ticker",
        validate="one_to_one",
    )
    both = panel.oi_prior.notna() & panel.oi_latest.notna()
    entry = panel.oi_prior.isna() & panel.oi_latest.notna()
    exit_ = panel.oi_prior.notna() & panel.oi_latest.isna()
    panel["component"] = np.select([both, entry, exit_], ["continuing", "entry", "exit"], "invalid")
    base_end, latest_end = baseline + pd.Timedelta(days=1), latest + pd.Timedelta(days=1)
    new_listing = panel.created_time_parsed.ge(base_end) & panel.created_time_parsed.lt(latest_end)
    settled = panel.settlement_ts_parsed.ge(base_end) & panel.settlement_ts_parsed.lt(latest_end)
    panel["entry_corroborated_new_listing"] = entry & new_listing
    panel["exit_corroborated_settlement"] = exit_ & settled
    rows = []
    expanded = panel.explode("issues").rename(columns={"issues": "issue_id"})
    all_groups = metadata.explode("issues").rename(columns={"issues": "issue_id"})
    for (issue, channel), meta in all_groups.groupby(["issue_id", "channel"]):
        frame = expanded[expanded.issue_id.eq(issue) & expanded.channel.eq(channel)]
        continued = frame[frame.component.eq("continuing")]
        entries = frame[frame.component.eq("entry")]
        exits = frame[frame.component.eq("exit")]
        prior_total, latest_total = frame.oi_prior.sum(), frame.oi_latest.sum()
        continuing_delta = (continued.oi_latest - continued.oi_prior).sum()
        entry_total, exit_total = entries.oi_latest.sum(), exits.oi_prior.sum()
        delta = latest_total - prior_total
        residual = delta - (continuing_delta + entry_total - exit_total)
        rows.append(
            {
                "issue_id": issue,
                "channel": channel,
                "baseline_day": baseline.date().isoformat(),
                "latest_day": latest.date().isoformat(),
                "endpoint_max_age_days": MAX_OI_AGE_DAYS,
                "metadata_contracts": len(meta),
                "prior_observed_contracts": int(frame.oi_prior.notna().sum()),
                "latest_observed_contracts": int(frame.oi_latest.notna().sum()),
                "continuing_contracts": len(continued),
                "entry_contracts": len(entries),
                "exit_contracts": len(exits),
                "no_fresh_oi_at_either_endpoint": len(meta) - len(frame),
                "prior_oi": prior_total,
                "latest_oi": latest_total,
                "observed_panel_oi_change": delta,
                "continuing_oi_change": continuing_delta,
                "entry_oi": entry_total,
                "exit_oi": exit_total,
                "entry_oi_new_listing": entries.loc[
                    entries.entry_corroborated_new_listing, "oi_latest"
                ].sum(),
                "entry_oi_old_or_unknown_creation": entries.loc[
                    ~entries.entry_corroborated_new_listing, "oi_latest"
                ].sum(),
                "exit_oi_settlement_corroborated": exits.loc[
                    exits.exit_corroborated_settlement, "oi_prior"
                ].sum(),
                "exit_oi_without_settlement_evidence": exits.loc[
                    ~exits.exit_corroborated_settlement, "oi_prior"
                ].sum(),
                "identity_residual": residual,
                "continuing_oi_pct_change": continuing_delta / continued.oi_prior.sum() * 100
                if continued.oi_prior.sum()
                else np.nan,
                "interpretation": "outstanding_contracts_not_net_directional_positions_or_cash_inflows",
            }
        )
    return pd.DataFrame(rows), panel


def _window(days):
    recent_start = LAST_DAY - pd.Timedelta(days=days - 1)
    return recent_start - pd.Timedelta(days=28), recent_start, LAST_DAY


def family_activity(daily, minimum_coverage=MIN_COVERAGE):
    """Within-family volume comparisons using continuing contracts only.

    Each contract must have observed volume on >=80% of days in BOTH windows.
    For an eligible panel the family statistic is observed daily volume per
    covered contract. Family aggregation is equal weight, not raw volume weight.
    """
    d = daily.explode("issues").rename(columns={"issues": "issue_id"})
    rows, daily_rows = [], []
    for days in [7, 14]:
        prior_start, recent_start, end = _window(days)
        window = d[d.day.between(prior_start, end)].copy()
        for (issue, channel, family), frame in window.groupby(["issue_id", "channel", "series_ticker"]):
            vol = frame[frame.volume.notna()]
            prior = vol[vol.day.lt(recent_start)]
            recent = vol[vol.day.ge(recent_start)]
            counts = (
                prior.groupby("ticker")
                .day.nunique()
                .rename("prior_days")
                .to_frame()
                .join(recent.groupby("ticker").day.nunique().rename("recent_days"), how="outer")
                .fillna(0)
            )
            eligible = counts.index[
                (counts.prior_days / 28 >= minimum_coverage) & (counts.recent_days / days >= minimum_coverage)
            ]
            panel = vol[vol.ticker.isin(eligible)]
            row = {
                "issue_id": issue,
                "channel": channel,
                "series_ticker": family,
                "recent_days": days,
                "prior_days": 28,
                "prior_start": prior_start.date().isoformat(),
                "recent_start": recent_start.date().isoformat(),
                "candidate_contracts": frame.ticker.nunique(),
                "continuing_contracts": len(eligible),
                "minimum_observed_day_fraction": minimum_coverage,
                "volume_missing_rows": int(frame.volume.isna().sum()),
                "method": "fixed_continuing_contracts_mean_daily_volume_per_observed_contract",
            }
            if not len(eligible):
                row["status"] = "insufficient_continuing_contract_coverage"
                rows.append(row)
                continue
            activity = panel.groupby("day").agg(
                volume=("volume", "sum"),
                observed_contracts=("ticker", "nunique"),
                events=("event_ticker", "nunique"),
                traded_contracts=("volume", lambda x: int(x.gt(0).sum())),
                median_spread=("spread", "median"),
                usable_hours=("usable_hours", "sum"),
                hours=("hours", "sum"),
            )
            activity["volume_per_observed_contract"] = activity.volume / activity.observed_contracts
            # Avoid a day with sparse panel coverage dominating an otherwise
            # dense contract panel. No imputation on discarded days.
            activity = activity[activity.observed_contracts.ge(len(eligible) * minimum_coverage)]
            pa, ra = activity[activity.index < recent_start], activity[activity.index >= recent_start]
            if len(pa) < 28 * minimum_coverage or len(ra) < days * minimum_coverage:
                row["status"] = "insufficient_family_day_coverage"
                rows.append(row)
                continue
            pv, rv = pa.volume_per_observed_contract, ra.volume_per_observed_contract
            median = float(np.median(np.log1p(pv)))
            mad = float(np.median(np.abs(np.log1p(pv) - median)))
            row.update(
                {
                    "status": "eligible",
                    "prior_valid_days": len(pa),
                    "recent_valid_days": len(ra),
                    "prior_mean_daily_volume_per_contract": pv.mean(),
                    "recent_mean_daily_volume_per_contract": rv.mean(),
                    "volume_ratio": rv.mean() / pv.mean() if pv.mean() else np.nan,
                    "log1p_activity_change": np.log1p(rv).mean() - np.log1p(pv).mean(),
                    "robust_activity_z": (np.log1p(rv).mean() - median) / (1.4826 * mad)
                    if mad > 0
                    else np.nan,
                    "recent_days_above_prior_median": int(rv.gt(pv.median()).sum()),
                    "persistence_share": float(rv.gt(pv.median()).mean()),
                    "prior_total_volume_observed": pa.volume.sum(),
                    "recent_total_volume_observed": ra.volume.sum(),
                    "prior_distinct_events": prior[prior.ticker.isin(eligible)].event_ticker.nunique(),
                    "recent_distinct_events": recent[recent.ticker.isin(eligible)].event_ticker.nunique(),
                    "recent_trading_days": int(ra.volume.gt(0).sum()),
                    "median_spread_recent": ra.median_spread.median(),
                    "usable_quote_hour_share_recent": ra.usable_hours.sum() / ra.hours.sum()
                    if ra.hours.sum()
                    else np.nan,
                }
            )
            for day, values in activity.iterrows():
                daily_rows.append(
                    {
                        "issue_id": issue,
                        "channel": channel,
                        "series_ticker": family,
                        "recent_days": days,
                        "day": day.date().isoformat(),
                        "period": "recent" if day >= recent_start else "prior",
                        "fixed_panel_contracts": len(eligible),
                        **values.to_dict(),
                    }
                )
            rows.append(row)
    return pd.DataFrame(rows), pd.DataFrame(daily_rows)


def repeated_event_adoption(daily, metadata):
    """Separate diagnostic: first two FULL UTC days after event creation.

    This is launch adoption, not a speech-language or voter-salience estimate.
    It is intentionally excluded from the continuing-panel attention ranking.
    """
    d = daily.copy()
    d["event_start_day"] = d.event_created.dt.floor("D")
    d["event_age_days"] = (d.day - d.event_start_day).dt.days
    d = d[d.event_age_days.isin([1, 2]) & d.volume.notna()]
    rows = []
    for event, frame in d.groupby("event_ticker"):
        expected = metadata[metadata.event_ticker.eq(event)]
        if frame.day.nunique() != 2:
            continue
        # Only contracts already open before the two full observation days are
        # counted in the denominator; later threshold additions do not dilute.
        start = frame.event_start_day.iloc[0] + pd.Timedelta(days=1)
        expected = expected[expected.created_time_parsed.le(start)]
        frame = frame[frame.ticker.isin(expected.ticker)]
        fraction = len(frame) / (2 * len(expected)) if len(expected) else 0
        if fraction < MIN_COVERAGE:
            continue
        for issue in sorted({i for items in frame.issues for i in items}):
            subset = frame[frame.issues.map(lambda v: issue in v)]
            if len(subset):
                rows.append(
                    {
                        "issue_id": issue,
                        "channel": subset.channel.iloc[0],
                        "series_ticker": subset.series_ticker.iloc[0],
                        "event_ticker": event,
                        "event_creation_day": subset.event_start_day.iloc[0],
                        "observed_contract_days": len(subset),
                        "coverage_fraction": fraction,
                        "volume_per_observed_contract_day": subset.volume.mean(),
                        "total_volume": subset.volume.sum(),
                    }
                )
    event_rows = pd.DataFrame(
        rows,
        columns=[
            "issue_id",
            "channel",
            "series_ticker",
            "event_ticker",
            "event_creation_day",
            "observed_contract_days",
            "coverage_fraction",
            "volume_per_observed_contract_day",
            "total_volume",
        ],
    )
    comparisons = []
    if len(event_rows):
        for days in [7, 14]:
            prior_start, recent_start, end = _window(days)
            for (issue, channel, family), frame in event_rows.groupby(
                ["issue_id", "channel", "series_ticker"]
            ):
                a = frame[frame.event_creation_day.between(recent_start, end)]
                b = frame[
                    frame.event_creation_day.ge(prior_start) & frame.event_creation_day.lt(recent_start)
                ]
                x, y = (
                    a.volume_per_observed_contract_day.median(),
                    b.volume_per_observed_contract_day.median(),
                )
                comparisons.append(
                    {
                        "issue_id": issue,
                        "channel": channel,
                        "series_ticker": family,
                        "recent_days": days,
                        "recent_events": len(a),
                        "prior_events": len(b),
                        "recent_median_launch_activity": x,
                        "prior_median_launch_activity": y,
                        "median_activity_ratio": x / y if y > 0 else np.nan,
                        "status": "eligible" if min(len(a), len(b)) >= 3 else "too_few_matched_launches",
                        "method": "first_two_full_UTC_days_after_event_creation_not_48_hours_exact",
                    }
                )
    return event_rows, pd.DataFrame(
        comparisons,
        columns=[
            "issue_id",
            "channel",
            "series_ticker",
            "recent_days",
            "recent_events",
            "prior_events",
            "recent_median_launch_activity",
            "prior_median_launch_activity",
            "median_activity_ratio",
            "status",
            "method",
        ],
    )


def lifecycle_diagnostic(daily):
    """Match macro event-days to older events at the same scheduled lead day.

    This is a calendar confounding diagnostic, not a causal correction. With
    two months of history, most monthly series have fewer than three distinct
    past events and must remain explicitly under-supported.
    """
    d = daily[daily.channel.eq("macro") & daily.volume.notna()].copy()
    d["scheduled_day"] = d.expected_expiration_time_parsed.dt.floor("D")
    d["lead_days"] = (d.scheduled_day - d.day).dt.days
    d = d[d.lead_days.ge(0) & d.lead_days.le(60)]
    events = (
        d.groupby(["series_ticker", "event_ticker", "day", "scheduled_day", "lead_days"])
        .agg(event_volume=("volume", "sum"), observed_contracts=("ticker", "nunique"))
        .reset_index()
    )
    events["activity"] = events.event_volume / events.observed_contracts
    rows = []
    for days in [7, 14]:
        _, recent_start, end = _window(days)
        for family, frame in events.groupby("series_ticker"):
            recent = frame[frame.day.between(recent_start, end)]
            history = frame[frame.scheduled_day.lt(recent_start)]
            pairs = recent.merge(
                history[["event_ticker", "lead_days", "activity"]], on="lead_days", suffixes=("", "_past")
            )
            pairs = pairs[pairs.event_ticker.ne(pairs.event_ticker_past)]
            if pairs.empty:
                continue
            matches = pairs.groupby(["event_ticker", "day"]).agg(
                recent_activity=("activity", "first"),
                past_matched_median_activity=("activity_past", "median"),
                reference_events=("event_ticker_past", "nunique"),
            )
            denominator = matches.past_matched_median_activity.mean()
            independent_past = pairs.event_ticker_past.nunique()
            rows.append(
                {
                    "series_ticker": family,
                    "recent_days": days,
                    "matched_recent_event_days": len(matches),
                    "recent_distinct_events": pairs.event_ticker.nunique(),
                    "independent_past_events": independent_past,
                    "mean_recent_activity_per_contract": matches.recent_activity.mean(),
                    "mean_lead_matched_past_activity": denominator,
                    "lead_matched_activity_ratio": matches.recent_activity.mean() / denominator
                    if denominator > 0
                    else np.nan,
                    "status": "calendar_comparison_supported"
                    if independent_past >= 3 and len(matches) >= 5
                    else "insufficient_independent_past_events_or_days",
                    "limitation": "scheduled_expiration_lead_not_verified_release_time_or_causal_adjustment",
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "series_ticker",
            "recent_days",
            "matched_recent_event_days",
            "recent_distinct_events",
            "independent_past_events",
            "mean_recent_activity_per_contract",
            "mean_lead_matched_past_activity",
            "lead_matched_activity_ratio",
            "status",
            "limitation",
        ],
    )


def summarize_activity(families, daily, metadata):
    """Equal-family summaries; directional/persistence labels have clear gates."""
    rows = []
    groups = metadata.explode("issues").rename(columns={"issues": "issue_id"})
    for (issue, channel), meta in groups.groupby(["issue_id", "channel"]):
        for days in [7, 14]:
            f = families[
                families.issue_id.eq(issue) & families.channel.eq(channel) & families.recent_days.eq(days)
            ]
            good = f[f.status.eq("eligible")]
            row = {
                "issue_id": issue,
                "channel": channel,
                "recent_days": days,
                "metadata_contracts": meta.ticker.nunique(),
                "metadata_families": meta.series_ticker.nunique(),
                "families_with_any_window_observation": len(f),
                "eligible_continuing_families": len(good),
                "eligible_continuing_contracts": int(good.continuing_contracts.sum()) if len(good) else 0,
                "minimum_required_families": MIN_FAMILIES,
            }
            if len(good):
                total = good.recent_total_volume_observed.sum()
                row.update(
                    {
                        "equal_family_log1p_activity_change": good.log1p_activity_change.mean(),
                        "median_family_volume_ratio": good.volume_ratio.median(),
                        "median_family_robust_activity_z": good.robust_activity_z.median(),
                        "families_activity_rising": int(good.log1p_activity_change.gt(0).sum()),
                        "share_families_activity_rising": good.log1p_activity_change.gt(0).mean(),
                        "equal_family_persistence_share": good.persistence_share.mean(),
                        "recent_fixed_panel_volume": total,
                        "largest_family_volume_share": good.recent_total_volume_observed.max() / total
                        if total
                        else np.nan,
                        "largest_family": good.loc[
                            good.recent_total_volume_observed.idxmax(), "series_ticker"
                        ],
                        "median_quote_usable_share": good.usable_quote_hour_share_recent.median(),
                    }
                )
            if len(good) < MIN_FAMILIES:
                row["attention_class"] = "insufficient_continuing_family_coverage"
            elif row["largest_family_volume_share"] > 0.75:
                row["attention_class"] = "concentrated_activity_no_broad_attention_claim"
            elif (
                row["share_families_activity_rising"] >= 2 / 3
                and row["equal_family_persistence_share"] >= 2 / 3
                and row["equal_family_log1p_activity_change"] > 0
            ):
                row["attention_class"] = "broad_persistent_marketplace_activity_rising"
            elif row["equal_family_log1p_activity_change"] > 0:
                row["attention_class"] = "mixed_or_transient_marketplace_activity_rising"
            else:
                row["attention_class"] = "no_broad_marketplace_activity_increase"
            row["electoral_salience_status"] = "requires_independent_poll_campaign_and_policy_evidence"
            rows.append(row)
    return pd.DataFrame(rows)


def _write_csv(path, frame):
    frame = frame.copy()
    for c in frame.select_dtypes(include=["datetimetz", "datetime"]).columns:
        frame[c] = frame[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    for c in frame.select_dtypes(include=["object"]).columns:
        frame[c] = frame[c].map(
            lambda v: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v
        )
    frame.to_csv(path, index=False)


def run(inputs: Path, out: Path):
    """Run reproducible offline analysis, writing only to the chosen output dir."""
    inputs, out = Path(inputs), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    meta = prepare_metadata(pd.read_csv(inputs / "market_index.csv", low_memory=False))
    broad = prepare_metadata(pd.read_csv(inputs / "broad_market_index.csv", low_memory=False))
    raw = pd.read_csv(inputs / "market_daily.csv", low_memory=False)
    daily = prepare_daily(raw, meta)
    issuance, issued_summary = issuance_diagnostics(broad)
    issued_families = issuance_family_detail(broad)
    oi, oi_panel = oi_decomposition(daily, meta)
    channel_meta = meta.copy()
    channel_meta["issues"] = [["all_issues"] for _ in range(len(channel_meta))]
    channel_oi, _ = oi_decomposition(daily, channel_meta)
    total_meta = channel_meta.copy()
    total_meta["channel"] = "all"
    total_oi, _ = oi_decomposition(daily, total_meta)
    channel_oi = pd.concat([channel_oi, total_oi], ignore_index=True)
    if not np.allclose(oi.identity_residual, 0, atol=1e-6, rtol=0) or not np.allclose(
        channel_oi.identity_residual, 0, atol=1e-6, rtol=0
    ):
        raise ValueError("Open-interest accounting identity failed")
    family, activity = family_activity(daily)
    summary = summarize_activity(family, daily, meta)
    adoption, adoption_comparison = repeated_event_adoption(daily, meta)
    lifecycle = lifecycle_diagnostic(daily)
    sensitivity = []
    for coverage in [0.7, 0.8, 0.9]:
        tested = family if coverage == MIN_COVERAGE else family_activity(daily, coverage)[0]
        s = summarize_activity(tested, daily, meta)
        s["minimum_day_coverage"] = coverage
        sensitivity.append(s)
    sensitivity = pd.concat(sensitivity, ignore_index=True)
    robustness_keys = ["issue_id", "channel", "recent_days"]
    robustness = (
        sensitivity.groupby(robustness_keys)
        .agg(
            distinct_coverage_classifications=("attention_class", "nunique"),
            minimum_eligible_families_across_coverage=("eligible_continuing_families", "min"),
        )
        .reset_index()
    )
    robustness["classification_stable_across_coverage"] = robustness.distinct_coverage_classifications.eq(1)
    summary = summary.merge(robustness, on=robustness_keys, how="left", validate="one_to_one")
    tables = {
        "issue_summary": summary,
        "activity_daily": activity,
        "family_activity": family,
        "issuance_daily": issuance,
        "issuance_summary": issued_summary,
        "issuance_family_detail": issued_families,
        "oi_decomposition": oi,
        "oi_channel_summary": channel_oi,
        "oi_endpoint_panel": oi_panel.drop(columns=["issues"]),
        "event_launch_adoption": adoption,
        "event_launch_comparison": adoption_comparison,
        "coverage_sensitivity": sensitivity,
        "lifecycle_diagnostic": lifecycle,
    }
    dictionary = json.loads((inputs / "issue_dictionary.json").read_text(encoding="utf-8"))
    labels = {item["id"]: item["label"] for item in dictionary["issues"]}
    labels.update({"electoral_context": "Election outcomes and control", "unclassified": "Unclassified"})
    for name, frame in tables.items():
        if "issue_id" in frame:
            frame["issue_label"] = frame.issue_id.map(labels).fillna(frame.issue_id)
        _write_csv(out / (name + ".csv"), frame)
    manifest = {
        "status": "complete",
        "asof": ASOF.isoformat(),
        "full_utc_day_start": FIRST_DAY.date().isoformat(),
        "full_utc_day_end": LAST_DAY.date().isoformat(),
        "market_count": len(meta),
        "broad_metadata_count": len(broad),
        "daily_rows": len(daily),
        "missing_volume_rows": int(daily.volume.isna().sum()),
        "observed_zero_volume_rows": int(daily.volume.eq(0).sum()),
        "missing_oi_rows": int(daily.oi.isna().sum()),
        "missing_creation_times_broad": int(broad.created_time_parsed.isna().sum()),
        "api_event_creation_basis_broad": int(broad.event_creation_basis.eq("api_event_created_time").sum()),
        "minimum_day_coverage": MIN_COVERAGE,
        "minimum_families_for_breadth_claim": MIN_FAMILIES,
        "oi_max_freshness_days": MAX_OI_AGE_DAYS,
        "oi_identity_max_absolute_residual": float(oi.identity_residual.abs().max()) if len(oi) else None,
        "oi_identity_floating_point_tolerance_contracts": 1e-6,
        "tables": {name: len(frame) for name, frame in tables.items()},
        "cautions": [
            "Issue tags overlap; issue totals cannot be added into a universe total.",
            "Activity is the selected 6032-contract study cohort, not all public Kalshi trading.",
            "Creation diagnostics use a left-truncated classified metadata archive; first-observed families are not proven novel series or issues.",
            "Volume measures gross traded contracts; OI measures outstanding contracts, not cash flows or net bullish positions.",
            "Observed-panel entry/exit is not a listing/settlement claim without date corroboration.",
            "No political-language, voter-salience or causal-return inference follows from attention alone.",
            "New short-lived events are excluded from continuing panels; matched launch adoption is separately reported.",
            "Launch comparisons require two full UTC days and exclude launches too recent to be completely observed.",
            "Continuing-contract attention is not automatically calendar adjusted; scheduled-lead comparisons are a separate diagnostic requiring independent past events.",
            "Ratios have no invented denominator when prior volume is zero; robust z is missing if prior MAD is zero.",
        ],
    }
    (out / "quality_manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8"
    )
    return manifest
