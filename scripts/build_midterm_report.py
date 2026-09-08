"""Build the frozen September 8, 2026 midterms research report and exhibits.

Inputs are auditable CSVs from issue_research/equity_research plus the manually
verified public evidence registry. --offline rebuilds from the report's input
snapshot. Narrative conclusions are specific to this dated case study.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from pmresearch.research_report import markdown_table, render, workbook

INK, TEAL, ORANGE, BLUE, GREY = "#172f46", "#008b87", "#ce6a38", "#527fb0", "#8395a4"
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.labelcolor": INK,
        "text.color": INK,
        "axes.edgecolor": "#d1dce4",
        "axes.titleweight": "bold",
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.axisbelow": True,
    }
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pct(value, digits=1, signed=False):
    if value is None or pd.isna(value):
        return "Unavailable"
    return format(100 * float(value), f"{'+' if signed else ''}.{digits}f") + "%"


def pp(value, digits=1):
    return "Unavailable" if value is None or pd.isna(value) else f"{float(value):+.{digits}f} pp"


def plain_csv(df, path):
    df.to_csv(path, index=False, encoding="utf-8-sig")


def build(run_root: Path, out: Path, offline=False):
    out.mkdir(parents=True, exist_ok=True)
    inputs = out / "inputs"
    charts = out / "charts"
    tables = out / "tables"
    charts.mkdir(exist_ok=True)
    tables.mkdir(exist_ok=True)
    if not offline:
        for group, source in [("kalshi", run_root / "kalshi_analysis"), ("equities", run_root / "equities")]:
            target = inputs / group
            target.mkdir(parents=True, exist_ok=True)
            names = (
                [
                    "analysis_manifest.json",
                    "selection_manifest.json",
                    "analysis_market_index.csv",
                    "coverage.csv",
                    "issue_summary.csv",
                    "mention_observations.csv",
                    "mention_cohorts.csv",
                    "mention_robustness.csv",
                    "election_snapshot.csv",
                    "election_changes.csv",
                    "policy_snapshot.csv",
                    "policy_changes.csv",
                    "macro_snapshot.csv",
                    "macro_changes.csv",
                    "daily_issue_series.csv",
                    "weekly_issue_series.csv",
                ]
                if group == "kalshi"
                else [
                    "collection_manifest.json",
                    "equity_prices.csv",
                    "equity_returns.csv",
                    "equity_baskets.csv",
                    "equity_basket_history.csv",
                    "company_fundamentals.csv",
                    "universe_used.json",
                ]
            )
            for name in names:
                if (source / name).exists():
                    shutil.copy2(source / name, target / name)
        shutil.copy2("config/midterm_public_evidence.json", inputs / "public_evidence.json")
        shutil.copy2("config/midterm_analysis_scope.json", inputs / "analysis_scope.json")
        shutil.copy2("config/midterm_issues.json", inputs / "issue_dictionary.json")
        supp = run_root / "kalshi_supplement" / "candle_manifest.json"
        pipeline_manifest = run_root / "pipeline_manifest.json"
        if pipeline_manifest.exists():
            pipeline = read_json(pipeline_manifest)
            supp = Path(pipeline["supplement_root"]) / "candle_manifest.json"
            shutil.copy2(pipeline_manifest, inputs / "pipeline_manifest.json")
        if supp.exists():
            shutil.copy2(supp, inputs / "supplement_collection_manifest.json")
    manifest = read_json(inputs / "kalshi/analysis_manifest.json")
    if not manifest["asof_utc"].startswith("2026-09-08T15:07"):
        raise ValueError(
            "The interpretation is specific to the September 8, 2026 freeze; a new date needs analyst review."
        )
    if manifest.get("candle_dataset") != "common_only" or manifest.get("presettlement_extensions_included"):
        raise ValueError(
            "This report requires the common-window dataset; settlement extensions must remain separate."
        )
    evidence = read_json(inputs / "public_evidence.json")
    sources = {record["id"]: record for record in evidence["records"]}

    def source(sid, label=None):
        record = sources[sid]
        return f"[{label or sid + ': ' + record['source_title']}]({record['source_url']})"

    def frame(group, name):
        return pd.read_csv(inputs / group / f"{name}.csv", low_memory=False)

    elections, policy, macro = [
        frame("kalshi", name + "_snapshot") for name in ["election", "policy", "macro"]
    ]
    snapshots = pd.concat([elections, policy, macro], ignore_index=True).set_index("ticker")
    changes = pd.concat(
        [frame("kalshi", name + "_changes") for name in ["election", "policy", "macro"]], ignore_index=True
    )
    index = frame("kalshi", "analysis_market_index")
    coverage = frame("kalshi", "coverage")
    mentions = frame("kalshi", "mention_observations")
    issues = frame("kalshi", "issue_summary")
    robustness = frame("kalshi", "mention_robustness")
    prices, returns, baskets, fundamentals = [
        frame("equities", name)
        for name in ["equity_prices", "equity_returns", "equity_baskets", "company_fundamentals"]
    ]
    eq_manifest = read_json(inputs / "equities/collection_manifest.json")

    def market(ticker):
        if ticker not in snapshots.index:
            return {"quote_status": "not_in_research_cohort"}
        return snapshots.loc[ticker].to_dict()

    def mp(ticker):
        return pct(market(ticker).get("mid_probability"))

    def delta(ticker, days=28):
        row = changes[(changes.ticker == ticker) & (changes.lookback_days == days)]
        return None if row.empty else row.iloc[0].change_pp

    def ret(ticker, period="20_sessions", field="total_return"):
        row = returns[(returns.ticker == ticker) & (returns.period == period)]
        return None if row.empty else row.iloc[0][field]

    def fundamental(ticker, metric):
        row = fundamentals[(fundamentals.ticker == ticker) & (fundamentals.metric == metric)]
        return None if row.empty else row.iloc[-1].value

    def savefig(fig, name, note):
        fig.text(0.01, 0.008, note, fontsize=8, color="#536575", va="bottom")
        fig.tight_layout(rect=(0, 0.065, 1, 1))
        for suffix in ["png", "svg"]:
            fig.savefig(charts / f"{name}.{suffix}", dpi=180, bbox_inches="tight")
        plt.close(fig)
        return f"![{name.replace('_', ' ')}](charts/{name}.png)"

    plots = {}

    # Survey changes use one question/population, rather than a pooled salience score.
    p02 = pd.DataFrame(sources["P02"]["observations"])
    labels = {
        "healthcare_cost": "Health care",
        "food_consumer_goods": "Food / goods",
        "housing_cost": "Housing",
        "gasoline_cost": "Gasoline",
        "electricity_cost": "Electricity",
        "job_availability": "Job availability",
    }
    poll = p02[p02.metric.isin(labels)].pivot(index="metric", columns="date", values="value").reindex(labels)
    poll["change_pp"] = poll["2026-07"] - poll["2026-01"]
    poll["label"] = poll.index.map(labels)
    plain_csv(poll.reset_index(), tables / "national_concern_changes.csv")
    fig, ax = plt.subplots(figsize=(10.5, 4.6))
    y = np.arange(len(poll))
    for pos, (_, row) in enumerate(poll.iterrows()):
        ax.plot(
            [row["2026-01"], row["2026-07"]],
            [pos, pos],
            lw=4,
            color=ORANGE if row.label == "Gasoline" else "#cbd8e2",
        )
        ax.text(
            76,
            pos,
            f"{row.change_pp:+.0f} pp",
            va="center",
            weight="bold" if row.label == "Gasoline" else "normal",
        )
    ax.scatter(poll["2026-01"], y, s=65, color=GREY, label="January 2026", zorder=3)
    ax.scatter(poll["2026-07"], y, s=65, color=TEAL, label="July 2026", zorder=4)
    ax.set(
        yticks=y,
        yticklabels=poll.label,
        xlim=(0, 87),
        xlabel="Adults very concerned (%)",
        title="Gasoline is the sharpest change; high concern is not always rising concern",
    )
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.15)
    ax.legend(loc="lower left", frameon=False)
    plots["poll"] = savefig(
        fig,
        "01_household_concerns",
        "Source: Pew July 23, 2026 (P02). Gasoline/electricity are split-sample questions. Descriptive changes; no significance test.",
    )

    trends = pd.DataFrame(evidence["trend_comparisons"])
    plain_csv(trends, tables / "public_evidence_trends.csv")
    state_rows = [
        {
            "record": "P05",
            "scope": "US adults",
            "baseline": 62,
            "latest": 73,
            "baseline_period": "Jan",
            "latest_period": "Jul",
        },
        {
            "record": "P06",
            "scope": "Wisconsin registered voters",
            "baseline": 70,
            "latest": 78,
            "baseline_period": "Feb",
            "latest_period": "Aug",
        },
    ]
    # Values are checked against the cited registry before a chart is produced.
    for row, tid in zip(state_rows, ["T05", "T06"]):
        observed = trends[trends.id == tid].iloc[0]
        assert row["baseline"] == observed.baseline and row["latest"] == observed.latest
    plain_csv(pd.DataFrame(state_rows), tables / "data_center_opposition.csv")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), gridspec_kw={"width_ratios": [1.25, 1]})
    for pos, row in enumerate(state_rows):
        axes[0].barh(pos - 0.16, row["baseline"], height=0.28, color=GREY)
        axes[0].barh(pos + 0.16, row["latest"], height=0.28, color=TEAL)
        axes[0].text(
            row["baseline"] + 1,
            pos - 0.16,
            f"{row['baseline']}% ({row['baseline_period']})",
            va="center",
            fontsize=9,
        )
        axes[0].text(
            row["latest"] + 1,
            pos + 0.16,
            f"{row['latest']}% ({row['latest_period']})",
            va="center",
            fontsize=9,
        )
    axes[0].set(
        yticks=[0, 1],
        yticklabels=[r["scope"] for r in state_rows],
        xlim=(0, 104),
        title="Data-center costs outweigh benefits",
        xlabel="Respondents (%)",
    )
    axes[0].invert_yaxis()
    p06 = sources["P06"]["observations"]
    priority = [
        ("Inflation / living costs", "most_important_issue_inflation"),
        ("Data centers", "most_important_issue_data_centers"),
    ]
    vals = [next(v["value"] for v in p06 if v["metric"] == metric) for _, metric in priority]
    axes[1].barh([0, 1], vals, color=[ORANGE, BLUE], height=0.45)
    axes[1].set(
        yticks=[0, 1],
        yticklabels=[x[0] for x in priority],
        xlim=(0, 60),
        title="Wisconsin: issue that matters most now",
        xlabel="Registered voters (%), August",
    )
    axes[1].invert_yaxis()
    for pos, value in enumerate(vals):
        axes[1].text(value + 1, pos, f"{value}%", va="center")
    plots["state"] = savefig(
        fig,
        "02_opposition_and_priority",
        "Sources: Marquette national (P05) and Wisconsin (P06). Different samples/questions; panels must not be combined into one index.",
    )

    election_watch = [
        ("CONTROLH-2026-D", "Democratic House"),
        ("CONTROLS-2026-D", "Democratic Senate"),
        ("GOVPARTYWI-26-D", "Wisconsin D governor"),
        ("GOVPARTYMI-26-D", "Michigan D governor"),
        ("GOVPARTYPA-26-D", "Pennsylvania D governor"),
        ("GOVPARTYGA-26-D", "Georgia D governor"),
        ("GOVPARTYAZ-26-D", "Arizona D governor"),
    ]

    def watch_table(watch):
        rows = []
        for ticker, label in watch:
            q = market(ticker)
            rows.append(
                {
                    "ticker": ticker,
                    "label": label,
                    "probability": q.get("mid_probability"),
                    "bid_usd": q.get("bid_usd"),
                    "ask_usd": q.get("ask_usd"),
                    "spread_probability": q.get("spread_probability"),
                    "change_28d_pp": delta(ticker),
                    "change_7d_pp": delta(ticker, 7),
                    "quote_status": q.get("quote_status"),
                    "open_interest_contracts": q.get("open_interest_contracts"),
                    "quote_observed_utc": q.get("quote_observed_utc"),
                }
            )
        return pd.DataFrame(rows)

    election_table = watch_table(election_watch)
    plain_csv(election_table, tables / "election_watchlist.csv")
    fig, ax = plt.subplots(figsize=(10.8, 4.5))
    for pos, row in election_table.iterrows():
        if pd.notna(row.probability):
            ax.plot([100 * row.bid_usd, 100 * row.ask_usd], [pos, pos], lw=4, color=TEAL)
            ax.scatter(100 * row.probability, pos, color=TEAL, s=40)
            if pd.notna(row.change_28d_pp):
                ax.scatter(100 * row.probability - row.change_28d_pp, pos, color=GREY, marker="x", s=55)
            ax.text(103, pos, pp(row.change_28d_pp), va="center", fontsize=9)
    ax.set(
        yticks=np.arange(len(election_table)),
        yticklabels=election_table.label,
        xlim=(0, 120),
        xlabel="Quoted probability (%); grey × = 28 days earlier; right = change",
        title="House quotes favor Democrats; state repricing is uneven",
    )
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.15)
    ax.axvline(50, ls="--", color=GREY, lw=0.8)
    plots["election"] = savefig(
        fig,
        "03_election_repricing",
        "Source: public Kalshi hourly bid/ask candles through Sep 8, 15:07 UTC. Midpoints, not vote shares. No causal attribution to an issue.",
    )

    policy_watch = [
        ("KXMORATORIUMCOUNT-26DEC31-AL3", "At least 3 state moratoriums in 2026"),
        ("KXMORATORIUMCOUNT-26DEC31-AL4", "At least 4 state moratoriums in 2026"),
        ("KXMORATORIUMCOUNT-26DEC31-AL5", "At least 5 state moratoriums in 2026"),
        ("KXAIBILL-26JUN-27JAN01", "Federal AI framework law before 2027"),
        ("KXAILEGISLATION-27-JAN01", "AI regulation contract before 2027"),
        ("KXCORPTAXCUT-27JAN01-21", "Corporate tax below 21% before 2027"),
        ("KXACAEXT-26JAN-27", "Enhanced ACA credits law before 2027"),
        ("KXSUSPENDGASTAX-26MAY-JAN01", "Federal gas-tax reduction before 2027"),
    ]
    policy_table = watch_table(policy_watch)
    plain_csv(policy_table, tables / "policy_watchlist.csv")
    fig, ax = plt.subplots(figsize=(11, 4.8))
    for pos, row in policy_table.iterrows():
        if pd.notna(row.probability):
            ax.barh(
                pos, row.probability * 100, height=0.55, color=TEAL if "moratorium" in row.label else BLUE
            )
            ax.errorbar(
                row.probability * 100,
                pos,
                xerr=np.array(
                    [[100 * (row.probability - row.bid_usd)], [100 * (row.ask_usd - row.probability)]]
                ),
                color=INK,
                capsize=3,
            )
            ax.text(min(95, row.probability * 100 + 5), pos, pct(row.probability), va="center")
        else:
            ax.text(1, pos, row.quote_status.replace("_", " "), va="center", color=GREY)
    ax.set(
        yticks=np.arange(len(policy_table)),
        yticklabels=policy_table.label,
        xlim=(0, 100),
        xlabel="Quoted probability (%); whiskers are bid–ask spreads, not confidence intervals",
        title="State restrictions and federal legislation are different exposures",
    )
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.15)
    plots["policy"] = savefig(
        fig,
        "04_policy_pricing",
        "Source: Kalshi. All displayed deadlines precede the incoming Congress. Threshold moratorium contracts overlap; probabilities are not additive.",
    )

    gas = macro[macro.ticker.str.startswith("KXAAAGASED-26NOV03-")].copy()
    gas["threshold"] = gas.ticker.str.rsplit("-", n=1).str[-1].astype(float)
    gas = gas.sort_values("threshold")
    fed = macro[macro.ticker.str.startswith("KXFEDFUNDSYEAR-28JAN01-")].copy()
    fed["threshold"] = fed.ticker.str.rsplit("-T", n=1).str[-1].astype(float)
    fed = fed.sort_values("threshold")
    plain_csv(gas, tables / "election_day_gas_ladder.csv")
    plain_csv(fed, tables / "year_end_2027_fed_ladder.csv")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for ax, df, title, xlabel in [
        (axes[0], gas, "Election-day gasoline", "National AAA price threshold ($/gallon)"),
        (axes[1], fed, "Fed upper bound at end-2027", "Fed target upper-bound threshold (%)"),
    ]:
        valid = df[df.quote_status == "usable"]
        ax.errorbar(
            valid.threshold,
            valid.mid_probability * 100,
            yerr=[
                100 * (valid.mid_probability - valid.bid_usd),
                100 * (valid.ask_usd - valid.mid_probability),
            ],
            fmt="o",
            color=TEAL,
            capsize=3,
        )
        ax.set(title=title, xlabel=xlabel, ylabel="Price of above-threshold outcome (%)", ylim=(0, 100))
        ax.axhline(50, color=GREY, ls="--", lw=0.8)
        ax.grid(alpha=0.15)
    plots["macro"] = savefig(
        fig,
        "05_gas_and_fed_distributions",
        "Source: Kalshi exceedance ladders, not fitted distributions. Whiskers: bid–ask. No smoothing/filling; small non-monotonicities retained.",
    )

    counts = mentions.quote_status.value_counts().rename_axis("status").reset_index(name="contracts")
    plain_csv(counts, tables / "mention_quality_funnel.csv")
    plain_csv(issues, tables / "issue_momentum_tests.csv")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), gridspec_kw={"width_ratios": [1.3, 1]})
    counts = counts.sort_values("contracts", ascending=False)
    status_labels = {
        "lead_unavailable_listed_too_late": "Listed too late for 24h lead",
        "usable": "Usable quote",
        "missing_usable_quote": "No usable quote",
        "lead_target_not_yet_observed": "Lead target still in future",
        "stale_observation": "Stale observation",
    }
    axes[0].barh(
        counts.status.map(status_labels).fillna(counts.status),
        counts.contracts,
        color=[TEAL if x == "usable" else GREY for x in counts.status],
    )
    axes[0].invert_yaxis()
    axes[0].set(xlabel="Issue-target mention contracts", title="Availability at a fixed 24-hour lead")
    diagnostics = issues[issues.raw_change_pp.notna()].sort_values("raw_change_pp")
    short_labels = {
        "ai_jobs_regulation": "AI / regulation",
        "energy_iran": "Energy / Iran",
        "trade_tariffs": "Trade / tariffs",
        "affordability_inflation": "Affordability",
        "electricity_datacenters": "Power / data centers",
        "immigration": "Immigration",
    }
    axes[1].barh(
        diagnostics.issue_id.map(short_labels).fillna(diagnostics.issue_id),
        diagnostics.raw_change_pp,
        color=ORANGE,
    )
    axes[1].axvline(0, color=GREY, lw=0.8)
    axes[1].set(xlabel="Raw mean expected-mention change (pp)", title="Unmatched changes: diagnostic only")
    plots["mentions"] = savefig(
        fig,
        "06_mentions_falsification",
        "Source: Kalshi. Latest 14 versus prior 28 completed UTC days; fixed retrospective close proxy. Orange bars are not validated salience signals.",
    )

    selected_stocks = [
        "CEG",
        "VST",
        "AEP",
        "DUK",
        "VRT",
        "ETN",
        "DLR",
        "EQIX",
        "XOM",
        "CVX",
        "UNH",
        "HCA",
        "DHI",
        "WMT",
    ]
    equity_table = (
        returns[(returns.ticker.isin(selected_stocks)) & (returns.period == "20_sessions")]
        .set_index("ticker")
        .reindex(selected_stocks)
        .reset_index()
    )
    plain_csv(equity_table, tables / "stock_pricing_comparison.csv")
    fig, ax = plt.subplots(figsize=(10.8, 6.1))
    ax.barh(
        equity_table.ticker,
        100 * equity_table.sector_excess_return,
        color=[TEAL if v >= 0 else ORANGE for v in equity_table.sector_excess_return],
    )
    ax.axvline(0, color=GREY, lw=0.8)
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.15)
    ax.set(
        title="Company dispersion is larger than a single theme label suggests",
        xlabel="20-session total return minus sector ETF return (percentage points)",
    )
    plots["stocks"] = savefig(
        fig,
        "07_stock_sector_relative_returns",
        "Source: Yahoo daily adjusted prices, Aug 7–Sep 4. Simple sector control; XLU is an imperfect benchmark for merchant generators. Not causal alpha.",
    )
    fig, ax = plt.subplots(figsize=(11, 4.8))
    basket_names = list(baskets.basket.drop_duplicates())
    for offset, period, label, color in [
        (-0.18, "common_window", "Jul 8–Sep 4", BLUE),
        (0.18, "20_sessions", "Aug 7–Sep 4", TEAL),
    ]:
        temp = baskets[baskets.period == period].set_index("basket").reindex(basket_names)
        ax.barh(
            np.arange(len(temp)) + offset, temp.spy_excess_return * 100, height=0.32, color=color, label=label
        )
    ax.set(
        yticks=np.arange(len(basket_names)),
        yticklabels=[x.replace("_", " ") for x in basket_names],
        title="Energy strength persists; other themes depend on the comparison window",
        xlabel="Equal-initial-weight basket return minus SPY (percentage points)",
    )
    ax.axvline(0, color=GREY, lw=0.8)
    ax.invert_yaxis()
    ax.legend(frameon=False)
    ax.grid(axis="x", alpha=0.15)
    plots["baskets"] = savefig(
        fig,
        "08_basket_window_sensitivity",
        "Source: same public price panel. Fixed disclosed membership, buy-and-hold weights. Illustrative baskets, not an MSCI index or backtested strategy.",
    )

    sensitivity = []
    for ticker, exit_pe in [
        ("MSFT", 25),
        ("VRT", 40),
        ("XOM", 18),
        ("DHI", 12),
        ("JPM", 13),
        ("UNH", 20),
        ("WMT", 30),
    ]:
        pe = fundamental(ticker, "trailing_pe_proxy")
        margin = fundamental(ticker, "operating_margin_ttm")
        sensitivity.append(
            {
                "ticker": ticker,
                "trailing_pe_proxy": pe,
                "cash_fcf_yield_proxy": fundamental(ticker, "cash_fcf_yield_proxy"),
                "operating_margin_ttm": margin,
                "ebit_change_for_100bp_margin": 0.01 / margin if margin else None,
                "illustrative_exit_pe": exit_pe,
                "two_year_eps_cagr_for_flat_price": (pe / exit_pe) ** 0.5 - 1 if pe else None,
                "scenario_basis": "Analyst stress assumption, not fair value, forecast, consensus, or calibrated probability",
            }
        )
    sensitivity = pd.DataFrame(sensitivity)
    plain_csv(sensitivity, tables / "valuation_and_margin_sensitivities.csv")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    margin_rows = sensitivity[sensitivity.operating_margin_ttm.notna()].sort_values(
        "ebit_change_for_100bp_margin"
    )
    axes[0].barh(margin_rows.ticker, margin_rows.ebit_change_for_100bp_margin * 100, color=ORANGE)
    axes[0].set(
        title="A 100 bp margin change has unequal impact", xlabel="Change in trailing operating profit (%)"
    )
    axes[1].barh(sensitivity.ticker, sensitivity.two_year_eps_cagr_for_flat_price * 100, color=TEAL)
    axes[1].set(
        title="Growth needed to absorb multiple compression",
        xlabel="Two-year EPS CAGR for unchanged share price (%)",
    )
    for pos, row in sensitivity.iterrows():
        axes[1].text(
            row.two_year_eps_cagr_for_flat_price * 100 + 0.5,
            pos,
            f"{row.trailing_pe_proxy:.1f}x → {row.illustrative_exit_pe:.0f}x",
            va="center",
            fontsize=8,
        )
    axes[1].set_xlim(0, max(40, sensitivity.two_year_eps_cagr_for_flat_price.max() * 100 + 15))
    for ax in axes:
        ax.grid(axis="x", alpha=0.15)
    plots["valuation"] = savefig(
        fig,
        "09_earnings_and_valuation_stress",
        "Source: SEC facts + Sep 4 prices. Margin stress holds revenue fixed. Exit P/Es are assumptions; break-even excludes dividends and discounting.",
    )

    # Compact numerical audit pack, including inputs for all original figures.
    source_rows = [
        {
            "id": r["id"],
            "date": r["date"],
            "title": r["source_title"],
            "url": r["source_url"],
            "type": r["source_type"],
            "claim": r["claim"],
            "uncertainty": " | ".join(r["uncertainty"]),
        }
        for r in evidence["records"]
    ]
    plain_csv(pd.DataFrame(source_rows), tables / "public_source_register.csv")
    plain_csv(returns, tables / "all_equity_returns.csv")
    plain_csv(fundamentals, tables / "company_fundamentals.csv")
    coverage_summary = (
        coverage.groupby("channel")
        .agg(
            markets=("ticker", "size"),
            with_artifact=("artifact_count", lambda x: int((x > 0).sum())),
            with_hourly_rows=("hourly_rows", lambda x: int((x > 0).sum())),
            hourly_rows=("hourly_rows", "sum"),
            usable_quote_hours=("usable_two_sided_hours", "sum"),
        )
        .reset_index()
    )
    plain_csv(coverage_summary, tables / "coverage_summary.csv")

    def formatted_watch(df):
        return [
            {
                "market": r.label,
                "quote": pct(r.probability),
                "spread": pct(r.spread_probability),
                "change": pp(r.change_28d_pp),
                "status": r.quote_status.replace("_", " "),
            }
            for _, r in df.iterrows()
        ]

    market_columns = [
        ("market", "Contract outcome"),
        ("quote", "Midpoint"),
        ("spread", "Spread"),
        ("change", "28-day change"),
    ]
    stock_rows = [
        {
            "ticker": r.ticker,
            "return": pct(r.total_return, signed=True),
            "sector": r.sector_benchmark,
            "excess": pp(100 * r.sector_excess_return),
            "full": pct(ret(r.ticker, "common_window"), signed=True),
        }
        for _, r in equity_table.iterrows()
    ]
    fund_rows = [
        {
            "ticker": r.ticker,
            "pe": f"{r.trailing_pe_proxy:.1f}x",
            "fcf": pct(r.cash_fcf_yield_proxy),
            "stress": f"{r.illustrative_exit_pe:.0f}x",
            "growth": pct(r.two_year_eps_cagr_for_flat_price),
        }
        for _, r in sensitivity.iterrows()
    ]
    counts_dict = mentions.quote_status.value_counts().to_dict()
    supported = int(manifest["supported_matched_cohorts"])
    total_hours = int(coverage.hourly_rows.sum())
    # Narrative in a separate function keeps methods and interpretation reviewable.
    context = locals()
    report = narrative(context)
    path = out / "Midterms_Issue_Momentum_Research.md"
    path.write_text(report, encoding="utf-8")
    notes = [
        "Frozen Kalshi cutoff: " + manifest["asof_utc"],
        "US equities: completed closes through September 4, 2026.",
        "Public Kalshi research cohort only; the broader category historical extraction remains separate.",
        "Probabilities are decimal in data sheets; changes labelled pp are percentage points.",
        "Missing values are unavailable, never zero. OI is outstanding contracts, not trader positioning.",
        "Read the report's methodology and primary-source dates before using any signal.",
    ]
    workbook(
        sorted(tables.glob("*.csv"))
        + [inputs / "kalshi/mention_robustness.csv", inputs / "equities/equity_prices.csv"],
        out / "Midterms_Research_Data.xlsx",
        notes,
    )
    render(path)
    qa = {
        "asof_utc": manifest["asof_utc"],
        "cohort_markets": len(index),
        "coverage_rows": len(coverage),
        "duplicate_index_tickers": int(index.ticker.duplicated().sum()),
        "duplicate_coverage_tickers": int(coverage.ticker.duplicated().sum()),
        "missing_cohort_artifacts": int((coverage.artifact_count == 0).sum()),
        "read_error_markets": int(coverage.read_errors.notna().sum()),
        "artifact_error_count": int(coverage.artifact_errors.sum()),
        "hourly_rows": total_hours,
        "candle_dataset": manifest["candle_dataset"],
        "price_rows": len(prices),
        "last_equity_date": prices.date.max(),
        "equity_duplicates": int(prices.duplicated(["ticker", "date"]).sum()),
        "future_equity_rows": int((prices.date > "2026-09-04").sum()),
        "supported_mention_cohorts": supported,
        "public_source_records": len(sources),
        "chart_count": len(plots),
        "tables": len(list(tables.glob("*.csv"))),
        "classification": "Ready to share with documented limitations; descriptive research, not causal or out-of-sample alpha evidence.",
    }
    if any(
        qa[x]
        for x in [
            "duplicate_index_tickers",
            "duplicate_coverage_tickers",
            "missing_cohort_artifacts",
            "read_error_markets",
            "artifact_error_count",
            "equity_duplicates",
            "future_equity_rows",
        ]
    ):
        qa["classification"] = "Needs coverage or data-quality review"
    (out / "validation.json").write_text(json.dumps(qa, indent=2), encoding="utf-8")
    print(json.dumps(qa, indent=2))
    return qa


def narrative(c):
    """Written after empirical review; numerical statements are bound to inputs."""
    mp, delta, ret, fund = [c[name] for name in ["mp", "delta", "ret", "fundamental"]]
    empty = (
        c["coverage"]
        .loc[c["coverage"].hourly_rows == 0, ["ticker"]]
        .merge(c["index"][["ticker", "settlement_ts"]], on="ticker", validate="one_to_one")
    )
    start_time = pd.Timestamp(c["manifest"]["common_window_start_utc"])
    settlements = pd.to_datetime(empty.settlement_ts, errors="coerce", format="mixed", utc=True)
    old_settlements = int((settlements < start_time).sum())
    values = {
        "asof": c["manifest"]["asof_utc"],
        "cohort_count": f"{len(c['index']):,}",
        "full_classified": f"{c['manifest'].get('inventory_selected_markets', 61018):,}",
        "hourly_rows": f"{c['total_hours']:,}",
        "row_markets": f"{int((c['coverage'].hourly_rows > 0).sum()):,}",
        "zero_markets": f"{int((c['coverage'].hourly_rows == 0).sum()):,}",
        "empty_old_settlements": f"{old_settlements:,}",
        "empty_other": f"{len(empty) - old_settlements:,}",
        "mention_usable": str(c["counts_dict"].get("usable", 0)),
        "mention_late": str(c["counts_dict"].get("lead_unavailable_listed_too_late", 0)),
        "matched_cohorts": str(c["supported"]),
        "house_prob": mp("CONTROLH-2026-D"),
        "senate_prob": mp("CONTROLS-2026-D"),
        "wi_prob": mp("GOVPARTYWI-26-D"),
        "wi_change": pp(delta("GOVPARTYWI-26-D")),
        "moratorium_prob": mp("KXMORATORIUMCOUNT-26DEC31-AL3"),
        "moratorium_change": pp(delta("KXMORATORIUMCOUNT-26DEC31-AL3")),
        "ai_bill_prob": mp("KXAIBILL-26JUN-27JAN01"),
        "aca_prob": mp("KXACAEXT-26JAN-27"),
        "gas_prob": mp("KXAAAGASED-26NOV03-4.00"),
        "gas_change": pp(delta("KXAAAGASED-26NOV03-4.00")),
        "fed_sept": mp("KXFEDDECISION-26SEP-H25"),
        "fed_change": pp(delta("KXFEDDECISION-26SEP-H25")),
        "fed_2027": mp("KXFEDFUNDSYEAR-28JAN01-T3.75"),
        "fed_2027_4": mp("KXFEDFUNDSYEAR-28JAN01-T4.00"),
        "spy_full": pct(ret("SPY", "common_window"), signed=True),
        "xle_full": pct(ret("XLE", "common_window"), signed=True),
        "ceg_full": pct(ret("CEG", "common_window"), signed=True),
        "vrt_full": pct(ret("VRT", "common_window"), signed=True),
        "vrt_pe": f"{fund('VRT', 'trailing_pe_proxy'):.1f}",
        "wmt_pe": f"{fund('WMT', 'trailing_pe_proxy'):.1f}",
        "dhi_pe": f"{fund('DHI', 'trailing_pe_proxy'):.1f}",
        "dhi_fcf": pct(fund("DHI", "cash_fcf_yield_proxy")),
        "wmt_fcf": pct(fund("WMT", "cash_fcf_yield_proxy")),
        "unh_excess": pp(ret("UNH", "common_window", "sector_excess_return") * 100),
        "vrt_growth": pct((fund("VRT", "trailing_pe_proxy") / 40) ** 0.5 - 1),
        "wmt_growth": pct((fund("WMT", "trailing_pe_proxy") / 30) ** 0.5 - 1),
        "vrt_25_return": pct(40 * 1.25**2 / fund("VRT", "trailing_pe_proxy") - 1, signed=True),
        "wmt_10_return": pct(30 * 1.10**2 / fund("WMT", "trailing_pe_proxy") - 1, signed=True),
        "unh_margin_sens": pct(0.01 / fund("UNH", "operating_margin_ttm")),
        "wmt_margin_sens": pct(0.01 / fund("WMT", "operating_margin_ttm")),
        "election_table": markdown_table(c["formatted_watch"](c["election_table"]), c["market_columns"]),
        "policy_table": markdown_table(c["formatted_watch"](c["policy_table"]), c["market_columns"]),
        "stocks_table": markdown_table(
            c["stock_rows"],
            [
                ("ticker", "Stock"),
                ("full", "Jul 8–Sep 4"),
                ("return", "20 sessions"),
                ("sector", "Sector ETF"),
                ("excess", "20-session excess"),
            ],
        ),
        "fund_table": markdown_table(
            c["fund_rows"],
            [
                ("ticker", "Stock"),
                ("pe", "Trailing P/E"),
                ("fcf", "Cash FCF yield"),
                ("stress", "Assumed exit P/E"),
                ("growth", "EPS CAGR for flat price"),
            ],
        ),
        "coverage_table": markdown_table(
            c["coverage_summary"].to_dict("records"),
            [
                ("channel", "Channel"),
                ("markets", "Contracts"),
                ("with_artifact", "With cache"),
                ("with_hourly_rows", "With bars"),
                ("hourly_rows", "Hourly rows"),
            ],
        ),
    }
    for key, chart in c["plots"].items():
        values[key + "_chart"] = chart
    for sid in c["sources"]:
        values["source_" + sid] = c["source"](sid)
    combinations = []
    cases = [
        (
            "RR",
            "R House / R Senate",
            "Strongest route for Republican fiscal legislation; margins and procedure still matter.",
        ),
        (
            "DR",
            "D House / R Senate",
            "House oversight and fiscal bargaining; Republican Senate retains nomination leverage.",
        ),
        (
            "RD",
            "R House / D Senate",
            "Senate nomination and legislative constraints; House remains a fiscal gatekeeper.",
        ),
        (
            "DD",
            "D House / D Senate",
            "Democratic legislative agendas and oversight; presidential veto remains a constraint.",
        ),
    ]
    for suffix, label, meaning in cases:
        combinations.append(
            {"case": label, "quote": mp("KXBALANCEPOWERCOMBO-27FEB-" + suffix), "implication": meaning}
        )
    values["joint_table"] = markdown_table(
        combinations,
        [
            ("case", "February 2027 organization"),
            ("quote", "Joint quote"),
            ("implication", "Policy implications with Republican presidency"),
        ],
    )
    mention_rows = [
        {
            "issue": r.issue_id.replace("_", " "),
            "events": f"{r.latest14d_events} / {r.prior28d_events}",
            "raw": pp(r.raw_change_pp),
            "matched": int(r.supported_matched_cohorts),
            "verdict": "Insufficient matched issue evidence",
        }
        for _, r in c["issues"].iterrows()
    ]
    values["mention_table"] = markdown_table(
        mention_rows,
        [
            ("issue", "Issue"),
            ("events", "Events: latest / prior"),
            ("raw", "Raw change"),
            ("matched", "Supported cohorts"),
            ("verdict", "Interpretation"),
        ],
    )
    values["source_register"] = "\n\n".join(
        f"- **{sid} — {r.get('date') or 'Standing institutional reference'}.** [{r['source_title']}]({r['source_url']})"
        for sid, r in c["sources"].items()
    )
    template_path = Path(__file__).resolve().parents[1] / "research/templates/midterms_20260908.md"
    template = template_path.read_text(encoding="utf-8")
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", str(value))
    import re

    if re.search(r"\{\{[^}]+\}\}", template):
        raise ValueError("Unresolved report template fields: " + str(re.findall(r"\{\{[^}]+\}\}", template)))
    return template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, default=Path("data/midterm_research_20260908"))
    parser.add_argument("--out", type=Path, default=Path("research/midterms_2026_09_08"))
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    build(args.run_root, args.out, args.offline)


if __name__ == "__main__":
    main()
