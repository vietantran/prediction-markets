"""Generate a self-contained, offline HTML research report and auditable figures."""
from __future__ import annotations

import base64
import gzip
import hashlib
import html
import json
from pathlib import Path
import re

import markdown
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd

from .data import sha, write_json

TEAL, BLUE, ORANGE, RED = "#087f8c", "#345a98", "#da913b", "#b83b50"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.titleweight": "bold", "axes.labelcolor": "#324656",
                     "text.color": "#172b3a", "axes.edgecolor": "#b6c4cf", "savefig.facecolor": "white"})


def pct(x, digits=1):
    return "—" if pd.isna(x) else f"{x * 100:,.{digits}f}%"


def fmt(x, digits=1):
    return "—" if pd.isna(x) else f"{x:,.{digits}f}"


def table(frame, columns, formats=None):
    subset = frame[list(columns)].copy()
    for key, function in (formats or {}).items():
        subset[key] = subset[key].map(function)
    subset = subset.rename(columns=columns).fillna("—")
    return '<div class="table-wrap">' + subset.to_html(index=False, border=0, escape=True, classes="data-table") + '</div>'


def build(root: Path):
    # The editorial conclusions belong to one reviewed data/configuration vintage.
    # A changed model must not silently publish these same hard-coded conclusions.
    lock_path = root / "config/report_input_lock.json"
    if lock_path.exists():
        locked = json.loads(lock_path.read_text(encoding="utf-8"))
        for name, expected in locked["content_sha256"].items():
            path = root / name
            payload = path.read_bytes()
            if path.suffix == ".gz":
                payload = gzip.decompress(payload)
            if hashlib.sha256(payload).hexdigest() != expected:
                raise ValueError(f"Reviewed report input changed: {name}. Review the narrative and update the release lock before publication.")
    out, inputs, report = root / "outputs", root / "inputs", root / "report"
    figures = report / "charts"
    figures.mkdir(parents=True, exist_ok=True)
    attention = report / "tables/attention"
    def read(name):
        return pd.read_csv(out / name)
    values = {}
    chart_names = []

    def figure(key, fig, caption):
        path = figures / f"{key}.png"
        fig.tight_layout(pad=1.5)
        fig.savefig(path, dpi=155, bbox_inches="tight")
        plt.close(fig)
        chart_names.append(path.name)
        encoded = base64.b64encode(path.read_bytes()).decode()
        values[key] = f'<figure><img alt="{html.escape(caption, quote=True)}" src="data:image/png;base64,{encoded}"><figcaption>{html.escape(caption)}</figcaption></figure>'

    trends = read("public_opinion_trends.csv")
    selected = trends.loc[trends.id.isin(["T01", "T02", "T03", "T04", "T05", "T06", "T08"])].copy()
    labels = ["Gasoline cost concern · US", "Electricity cost concern · US", "Housing cost concern · US",
              "Healthcare cost concern · US", "Data-center opposition · US", "Data-center opposition · Wisconsin",
              "Inflation as top national problem · US"]
    fig, ax = plt.subplots(figsize=(10.7, 4.5))
    y = np.arange(len(selected))
    ax.hlines(y, selected.baseline, selected.latest, color="#b7c6d1", linewidth=5)
    ax.scatter(selected.baseline, y, color=BLUE, label="Earlier wave", s=55, zorder=3)
    ax.scatter(selected.latest, y, color=TEAL, label="Latest wave", s=55, zorder=3)
    for i, row in enumerate(selected.itertuples()):
        ax.text(max(row.baseline, row.latest) + 2, i, f"{row.delta_pp:+.0f}pp", va="center")
    ax.set(yticks=y, yticklabels=labels, xlim=(0, 95), xlabel="Respondents (%) — different questions and populations", title="Concern, opposition and electoral priority are different signals")
    ax.invert_yaxis()
    ax.legend(loc="lower right")
    figure("political_trends_chart", fig, "Comparable changes within each series only. Pew cost concern January–July; national Marquette opposition January–July; Wisconsin February–August; Gallup top problem May–July. No cross-series pooling or significance claim. Sources: public_opinion_trends.csv and linked primary polls.")

    cp = read("cross_party_evidence.csv")
    fig, ax = plt.subplots(figsize=(10.7, 3.8))
    for j, (record, label) in enumerate([("P05", "US adults · July"), ("P06", "Wisconsin registered voters · August")]):
        frame = cp.loc[cp.record_id == record].set_index("party")
        vals = [frame.loc[x, "numeric_value"] for x in ["Republican", "independent", "Democratic"]]
        bars = ax.bar(np.arange(3) + (j - .5) * .34, vals, .34, color=[BLUE, TEAL][j], label=label)
        ax.bar_label(bars, fmt="%.0f%%", padding=3)
    ax.set(xticks=np.arange(3), xticklabels=["Republican", "Independent", "Democratic"], ylim=(0, 105), ylabel="Costs outweigh benefits (%)", title="Cross-party concern is clear; agreement on a remedy is unproven")
    ax.axhline(50, color="#b6c4cf", linestyle=":")
    ax.legend(loc="upper left", fontsize=9)
    figure("cross_party_chart", fig, "Marquette national and Wisconsin surveys. Distinct populations and dates; subgroup uncertainty is larger. These are cost-benefit opinions, not votes for a construction ban or a candidate.")

    election = pd.read_csv(inputs / "election_snapshot.csv")
    wanted = {"CONTROLH-2026-D": "Democratic House", "CONTROLS-2026-D": "Democratic Senate", "GOVPARTYWI-26-D": "Wisconsin D governor", "GOVPARTYGA-26-D": "Georgia D governor", "GOVPARTYMI-26-D": "Michigan D governor", "GOVPARTYPA-26-D": "Pennsylvania D governor", "GOVPARTYAZ-26-D": "Arizona D governor", "GOVPARTYOH-26-D": "Ohio D governor"}
    elected = election.loc[election.ticker.isin(wanted)].copy()
    elected["race"] = elected.ticker.map(wanted)
    values["election_table"] = table(elected, {"race": "Race proposition", "mid_probability": "Midpoint", "bid_usd": "YES bid ($)", "ask_usd": "YES ask ($)", "ticker": "Contract"}, {"mid_probability": pct, "bid_usd": lambda x: fmt(x, 3), "ask_usd": lambda x: fmt(x, 3)})
    inst = read("institutional_scenarios.csv")
    values["institution_table"] = table(inst, {"scenario": "February 2027 organization", "mid_probability": "Quoted midpoint", "legislative_route": "Possible route", "constraint": "Constraint"}, {"mid_probability": pct})
    states = read("state_policy_diffusion.csv")
    values["state_table"] = table(states, {"state": "State", "observed_remedy": "Observed response", "policy_class": "Classification", "cash_flow_channel": "Investment transmission"})

    issuance = pd.read_csv(attention / "issuance_family_detail.csv")
    issuance = issuance.loc[(issuance.recent_days == 14) & (issuance.channel == "macro")]
    energy = issuance.loc[issuance.issue_id == "energy_iran"].nlargest(6, "recent_new_contracts")
    electric = issuance.loc[issuance.issue_id == "electricity_datacenters"].nlargest(5, "recent_new_contracts")
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.0), gridspec_kw={"width_ratios": [1.25, 1]})
    for ax, frame, title in zip(axes, [energy, electric], ["Energy: repeated listings dominate", "Electricity: daily ERCOT dominates"]):
        ax.barh(frame.series_ticker, frame.recent_new_contracts, color=TEAL)
        ax.invert_yaxis()
        ax.set(title=title, xlabel="New contracts · Aug 25–Sep 7")
        ax.grid(axis="x", alpha=.15)
    figure("creation_chart", fig, "Creation from the 61,018-contract classified archive, grouped by series. A contract is not a unique event or voter signal; the displayed series are the largest contributors, not the full inventory.")

    activity = pd.read_csv(attention / "issue_summary.csv")
    keys = [("fed_rates", "macro"), ("affordability_inflation", "macro"), ("electricity_datacenters", "policy"), ("ai_jobs_regulation", "policy"), ("healthcare", "policy"), ("energy_iran", "macro")]
    act = pd.concat([activity.loc[(activity.issue_id == i) & (activity.channel == c) & (activity.recent_days == 14)] for i, c in keys])
    oi = pd.read_csv(attention / "oi_decomposition.csv")
    act = act.merge(oi[["issue_id", "channel", "continuing_oi_change", "continuing_oi_pct_change"]], on=["issue_id", "channel"])
    values["attention_table"] = table(act, {"issue_label": "Issue", "channel": "Channel", "median_family_volume_ratio": "Median family volume rate ×", "eligible_continuing_families": "Families", "largest_family_volume_share": "Largest family share", "continuing_oi_change": "Continuing ΔOI (contracts)", "classification_stable_across_coverage": "Coverage-stable class"}, {"median_family_volume_ratio": lambda x: fmt(x, 2), "largest_family_volume_share": pct, "continuing_oi_change": lambda x: fmt(x, 0)})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    labels = act.issue_label + " · " + act.channel
    axes[0].barh(labels, act.median_family_volume_ratio, color=TEAL)
    axes[0].axvline(1, color=ORANGE, linestyle="--")
    axes[0].set(title="Change in trading rate", xlabel="Median family ratio; recent14 vs prior28")
    axes[1].barh(labels, act.continuing_oi_pct_change, color=BLUE)
    axes[1].set(title="Change in outstanding contracts", xlabel="Continuing-contract OI change (%)")
    for ax in axes:
        ax.invert_yaxis()
    figure("attention_chart", fig, "Continuing panels control changes in observed contract membership, not scheduled-news effects. Equal-family volume and contract-weighted OI answer different questions. Topics overlap; do not add these totals.")
    channel_oi = pd.read_csv(attention / "oi_channel_summary.csv")
    values["oi_table"] = table(channel_oi, {"channel": "Channel (deduplicated)", "prior_oi": "Aug 24 OI", "latest_oi": "Sep 7 OI", "continuing_oi_change": "Continuing Δ", "entry_oi": "Observed entries +", "exit_oi": "Observed exits −"}, {c: lambda x: fmt(x, 0) for c in ["prior_oi", "latest_oi", "continuing_oi_change", "entry_oi", "exit_oi"]})

    specs = json.loads((inputs / "scenarios.json").read_text())
    daily = pd.read_csv(inputs / "market_daily.csv")
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.5), sharex=True)
    for ax, ident in zip(axes.flat, ["house_dem", "datacenter_three_states", "sept_fed_hike25", "election_gas_four"]):
        spec = next(s for s in specs if s["id"] == ident)
        d = daily.loc[(daily.ticker == spec["ticker"]) & daily.spread.le(.10)].copy()
        # Reindex to preserve missing dates as gaps instead of drawing false continuity.
        d = d.set_index(pd.to_datetime(d.utc_date)).reindex(pd.date_range("2026-07-09", "2026-09-07"))
        ax.plot(d.index, d.mid, color=TEAL, linewidth=1.6, marker=".", markersize=2)
        ax.set_title({"house_dem": "House Democratic victory", "datacenter_three_states": "Three state moratoriums by end-2026", "sept_fed_hike25": "September Fed: exactly +25bp", "election_gas_four": "Election-day gasoline above $4"}[ident], fontsize=11)
        ax.yaxis.set_major_formatter(PercentFormatter(1))
        ax.grid(alpha=.15)
        ax.tick_params(axis="x", labelrotation=30)
    figure("odds_paths_chart", fig, "Last retained hourly midpoint in each UTC day, shown only when that endpoint spread is at most 10 percentage points. Missing or excluded endpoints remain gaps. Thin-policy quote changes may occur without substantial executions.")

    tape = read("trade_summary.csv")
    all_tape = tape.loc[tape.period == "all28"].copy()
    liquidity = read("liquidity_validation.csv")
    all_tape = all_tape.merge(liquidity[["scenario_id", "active_days", "liquidity_validation_status"]], on="scenario_id")
    shortnames = {"house_dem": "House D", "senate_dem": "Senate D", "wisconsin_dem": "Wisconsin D", "georgia_dem": "Georgia D", "datacenter_three_states": "State moratoriums ≥3", "federal_ai_framework": "Federal AI framework", "election_gas_four": "Election gas >$4", "fed_2027_above375": "2027 Fed >3.75%", "sept_fed_hike25": "Sept Fed +25bp", "aca_extension": "ACA extension", "michigan_dem": "Michigan D", "pennsylvania_dem": "Pennsylvania D", "arizona_dem": "Arizona D"}
    all_tape["short_label"] = all_tape.scenario_id.map(shortnames)
    values["tape_table"] = table(all_tape, {"short_label": "Contract proposition", "executions": "Executions", "active_days": "Active days /28", "contracts_traded": "Traded contracts", "top_five_print_volume_share": "Top-five share", "nonblock_yes_taker_share": "YES-taker share"}, {"contracts_traded": lambda x: fmt(x, 0), "top_five_print_volume_share": pct, "nonblock_yes_taker_share": pct})
    fig, ax = plt.subplots(figsize=(11, 5))
    offsets = {"election_gas_four": (-35, 18), "georgia_dem": (8, -17),
               "fed_2027_above375": (-75, -22), "arizona_dem": (8, -13),
               "senate_dem": (-40, -20), "house_dem": (8, 7), "wisconsin_dem": (5, 12)}
    for row in all_tape.itertuples():
        ax.scatter(row.executions, row.top_five_print_volume_share, color=TEAL if row.executions >= 100 else RED, s=45)
        ax.annotate(row.short_label, (row.executions, row.top_five_print_volume_share), xytext=offsets.get(row.scenario_id, (5, 5)), textcoords="offset points", fontsize=8)
    ax.set(xscale="log", xlim=(5, 22000), ylim=(-.04, 1.09), xlabel="Executions in 28 days (log scale)", ylabel="Share of contract volume in the five largest prints", title="A quoted probability can rest on very little trading")
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.axhline(.5, color=ORANGE, linestyle=":")
    figure("tape_chart", fig, "All 21,585 public executions across 13 scenario contracts, Aug 11–Sep 7. All reported non-block. Print concentration cannot identify traders or institutional positions. The 50% line is a descriptive liquidity screen, not an optimized threshold.")

    prices = pd.read_csv(inputs / "equity_prices.csv")
    perf = []
    for ticker, p in prices.groupby("ticker"):
        p = p.sort_values("date")
        perf.append({"ticker": ticker, "period_return": p.adjusted_close.iloc[-1] / p.adjusted_close.iloc[0] - 1, "start": p.date.iloc[0], "end": p.date.iloc[-1]})
    perf = pd.DataFrame(perf)
    spy = perf.loc[perf.ticker == "SPY", "period_return"].iloc[0]
    perf["versus_spy_pp"] = 100 * (perf.period_return - spy)
    perf.to_csv(out / "equity_performance.csv", index=False)
    chosen = perf.loc[perf.ticker.isin(["SPY", "XLE", "XLU", "XLV", "CEG", "VST", "AEP", "DUK", "VRT", "ETN", "MSFT", "GOOGL", "UNH", "HCA", "DHI", "WMT"])].sort_values("period_return")
    fig, ax = plt.subplots(figsize=(10.7, 5.5))
    ax.barh(chosen.ticker, chosen.period_return, color=np.where(chosen.period_return >= 0, TEAL, RED))
    ax.axvline(spy, color=ORANGE, linestyle="--", label=f"SPY {pct(spy)}")
    ax.xaxis.set_major_formatter(PercentFormatter(1))
    ax.set(title="The same political narrative contains sharply different equity outcomes", xlabel="Adjusted-close return · Jul 8–Sep 4")
    ax.legend()
    figure("equity_chart", fig, "Vendor-adjusted historical returns, not a causal event attribution or implementable backtest. Constituents were chosen with knowledge of the research themes and prior performance. Every ticker's full-period return is exported in equity_performance.csv.")

    robust = read("sensitivity_robustness.csv")
    selected_pairs = [("house_dem", "hospitals"), ("wisconsin_dem", "data_center_reits"), ("datacenter_three_states", "power_generators"), ("federal_ai_framework", "generators_minus_regulated"), ("sept_fed_hike25", "sector_energy"), ("election_gas_four", "integrated_energy")]
    beta = pd.concat([robust.loc[(robust.scenario_id == s) & (robust.basket_id == b)] for s, b in selected_pairs])
    beta["local_beta_pct_1pp"] = beta.beta_per_10pp * 10
    beta["probability_range_pp"] = (beta.probability_max - beta.probability_min) * 100
    beta["relationship"] = beta.scenario_id.map(shortnames) + " → " + beta.basket_label
    values["beta_table"] = table(beta, {"relationship": "Relationship", "n_observations": "Dates", "local_beta_pct_1pp": "Return % / +1pp odds", "probability_range_pp": "Observed odds range (pp)", "q_value_conservative_primary": "Conservative q", "robustness_screen_status": "Sizing status"}, {"local_beta_pct_1pp": lambda x: fmt(x, 2), "probability_range_pp": lambda x: fmt(x, 1), "q_value_conservative_primary": lambda x: fmt(x, 3)})
    fig, ax = plt.subplots(figsize=(11.2, 5.0))
    y = np.arange(len(beta))
    for i, row in enumerate(beta.itertuples()):
        ax.plot([row.hc3_low * 10, row.hc3_high * 10], [i-.08, i-.08], color=BLUE, lw=3)
        ax.plot([row.bootstrap_low * 10, row.bootstrap_high * 10], [i+.08, i+.08], color=TEAL, lw=3)
        ax.scatter(row.beta_per_10pp * 10, i, color=ORANGE, zorder=3)
    ax.axvline(0, color="#777", lw=.8)
    ax.set(yticks=y, yticklabels=beta.relationship, xlabel="Associated equity return (%) per +1 percentage-point odds move", title="Statistical precision depends on method, liquidity and observed variation")
    ax.invert_yaxis()
    ax.plot([], [], color=BLUE, lw=3, label="HC3 95% interval")
    ax.plot([], [], color=TEAL, lw=3, label="Five-session block bootstrap 95% interval")
    ax.legend(loc="best", fontsize=9)
    figure("beta_chart", fig, "Selected market-conditioned associations, rescaled from the exported per-10pp coefficients to +1pp. All remain observational and fail the full sizing screen. The first relationship uses HCA alone as a hospital proxy. Whiskers are not 12–24 month return forecasts.")
    values["diagnostic_table"] = table(robust.drop_duplicates("scenario_id"), {"scenario_label": "Scenario", "n_observations": "Valid adjacent dates", "meaningful_shock_days": "≥1pp move dates", "large_move_days": "Moves above noise rule", "liquidity_validation_status": "Execution screen"})
    values["sensitivity_explorer"] = '<div class="explorer"><label for="model-filter">Filter all primary sensitivity results</label><input id="model-filter" placeholder="Try Fed, House, energy, utilities…"><p id="model-count" class="small"></p><div class="table-wrap"><table id="model-table"><thead><tr><th>Scenario</th><th>Basket</th><th>n</th><th>Return %/+1pp</th><th>Conservative q</th><th>Status</th></tr></thead><tbody></tbody></table></div><button id="download-models">Download displayed rows as CSV</button></div>'

    financial = read("financial_sensitivities.csv")
    scenarios = read("fundamental_scenarios.csv")
    central = scenarios.loc[(scenarios.case == "central") & (scenarios.years == 2)].copy()
    values["valuation_table"] = table(central, {"ticker": "Stock", "starting_pe": "Starting P/E", "eps_cagr": "Assumed EPS CAGR", "exit_pe": "Assumed exit P/E", "price_return": "24m price return", "eps_cagr_for_hurdle": "EPS CAGR for 10% annual price hurdle", "maximum_entry_for_hurdle_usd": "Entry meeting hurdle ($)"}, {"starting_pe": lambda x: fmt(x, 1), "eps_cagr": pct, "exit_pe": lambda x: fmt(x, 0), "price_return": pct, "eps_cagr_for_hurdle": pct, "maximum_entry_for_hurdle_usd": lambda x: fmt(x, 2)})
    values["fundamental_table"] = table(financial, {"ticker": "Stock", "operating_margin": "Group EBIT margin", "ebit_fraction_change_100bp_margin": "EBIT impact of +100bp margin", "cash_fcf_yield_proxy": "Cash FCF yield proxy", "decision_gate": "Underwriting gate"}, {"operating_margin": pct, "ebit_fraction_change_100bp_margin": pct, "cash_fcf_yield_proxy": pct})
    values["company_source_links"] = '<div class="small"><p>Company source documents (matching-period fact provenance is also exported):</p><ul>' + "".join(
        f'<li><strong>{html.escape(row.ticker)}</strong> · <a href="{html.escape(row.company_evidence_url, quote=True)}">Company release</a> · ' +
        " / ".join(f'<a href="{html.escape(url, quote=True)}">SEC EPS source {j+1}</a>' for j, url in enumerate(row.eps_source_urls.split("|"))) + '</li>'
        for row in financial.itertuples()) + '</ul></div>'
    fig, ax = plt.subplots(figsize=(10.7, 4.2))
    y = np.arange(len(central))
    ax.bar(y - .18, central.eps_cagr, .36, color=TEAL, label="Central assumed EPS CAGR")
    ax.bar(y + .18, central.eps_cagr_for_hurdle, .36, color=ORANGE, label="EPS CAGR needed for 10% annual price return")
    ax.set(xticks=y, xticklabels=central.ticker, ylabel="Annual earnings growth", title="The valuation hurdle can be harder than the political thesis")
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(fontsize=9)
    figure("hurdle_chart", fig, "24-month horizon at each company's explicitly assumed central exit P/E. Starting prices are Sep 4 closes; company EPS is sourced and adjustments disclosed. Analyst assumptions, no probability weights; dividends excluded.")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)
    for ax, years in zip(axes, [1, 2]):
        for i, ticker in enumerate(central.ticker):
            rows = scenarios.loc[(scenarios.ticker == ticker) & (scenarios.years == years)].set_index("case")
            ax.plot([rows.loc["lower", "price_return"], rows.loc["upper", "price_return"]], [i, i], color="#aebdc9", lw=5)
            ax.scatter(rows.loc["central", "price_return"], i, color=TEAL, zorder=3)
        ax.set(yticks=np.arange(len(central)), yticklabels=central.ticker, title=f"{years * 12}-month designed price-return range")
        ax.xaxis.set_major_formatter(PercentFormatter(1))
        ax.axvline(0, color=ORANGE, linestyle=":")
        ax.invert_yaxis()
    figure("valuation_chart", fig, "Lower/central/upper EPS-growth and exit-multiple combinations are configurable underwriting cases, not confidence intervals or forecasts. Company risks differ; the ranges do not have common assigned probabilities.")
    values["scenario_calculator"] = '<div class="calculator"><h3>Test your own underwriting assumptions</h3><p>This calculator changes EPS growth and exit valuation, not the political probability. It estimates price return only.</p><div class="controls"><label>Company<select id="stock"></select></label><label>EPS growth per year (%)<input id="growth" type="number" min="-90" max="100" step="1" value="20"></label><label>Exit P/E<input id="multiple" type="number" min="1" max="150" step="1" value="40"></label><label>Months<select id="months"><option>24</option><option>12</option></select></label></div><div id="scenario-result" aria-live="polite"></div><p class="small">Source starting EPS and Sep 4 close; MSFT removes the disclosed $0.67 OpenAI gain. No dividends, tax, costs or scenario probability weights.</p></div>'

    registry = json.loads((inputs / "public_evidence.json").read_text(encoding="utf-8"))
    sources = read("political_sources.csv")
    values["sources_table"] = table(sources, {c: c.replace("_", " ").title() for c in ["id", "date", "title", "url"] if c in sources})
    # Keep links clickable without accepting arbitrary HTML from input source text.
    values["primary_source_links"] = "<ul>" + "".join(f'<li>{html.escape(str(r.get("id", "")))} · <a href="{html.escape(str(r.get("source_url", "")), quote=True)}">{html.escape(str(r.get("source_title", "Primary source")))}</a></li>' for r in registry["records"]) + "</ul>"
    for key, filename in [("sensitivity", "sensitivity_manifest.json"), ("trades", "trade_analysis_manifest.json")]:
        values[key + "_manifest"] = json.loads((out / filename).read_text())
    primary = robust.loc[robust.primary_test]
    values["hac_discoveries"] = str(int(primary.q_value_bh_primary.lt(.05).sum()))
    values["conservative_discoveries"] = str(int(primary.q_value_conservative_primary.lt(.05).sum()))
    values["sizing_passes"] = str(int(primary.robustness_screen_status.eq("passes_pilot_diagnostics").sum()))
    numerical_audit = json.loads((out / "sensitivity_numerical_audit.json").read_text(encoding="utf-8"))
    values["numerical_error"] = f'{numerical_audit["maximum_absolute_coefficient_difference"]:.2e}'
    narrative = (root / "assets/report_template.md").read_text(encoding="utf-8")
    political = (root / "assets/political_narrative.md").read_text(encoding="utf-8")
    narrative = narrative.replace("{{political_narrative}}", political)
    for key, value in values.items():
        if isinstance(value, str):
            narrative = narrative.replace("{{" + key + "}}", value)
    unresolved = re.findall(r"\{\{[^}]+\}\}", narrative)
    if unresolved:
        raise ValueError("Unresolved report slots " + repr(unresolved))
    (report / "RESEARCH.md").write_text(narrative, encoding="utf-8")
    body = markdown.markdown(narrative, extensions=["tables", "fenced_code", "toc", "attr_list"])
    css = (root / "assets/report.css").read_text(encoding="utf-8")
    js = (root / "assets/report.js").read_text(encoding="utf-8")
    model_rows = primary[["scenario_label", "basket_label", "n_observations", "beta_per_10pp", "q_value_conservative_primary", "robustness_screen_status"]].replace({np.nan: None}).to_dict("records")
    stocks = financial[["ticker", "starting_eps_usd", "raw_close_usd"]].replace({np.nan: None}).to_dict("records")
    payload = json.dumps({"models": model_rows, "stocks": stocks}, allow_nan=False).replace("</", "<\\/")
    document = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Midterm issues & equity implications | September 2026</title><style>{css}</style></head><body><nav><a href="#top">MIDTERM RESEARCH</a><span>PUBLIC DATA · FROZEN 8 SEP 2026</span><button onclick="window.print()">Print / PDF</button></nav><main id="top">{body}</main><footer>Separate research programme · Reproducible inputs and full blueprint accompany this report.</footer><script id="research-data" type="application/json">{payload}</script><script>{js}</script></body></html>'
    target = report / "index.html"
    target.write_text(document, encoding="utf-8")
    manifest = {"status": "complete", "asof": "2026-09-08T15:07:00Z", "html": "report/index.html",
                "charts": chart_names, "input_sha256": {str(p.relative_to(root)): sha(p) for p in sorted(inputs.iterdir()) if p.is_file()},
                "config_sha256": {str(p.relative_to(root)): sha(p) for p in sorted((root / "config").glob("*.json"))},
                "derived_tables": [str(p.relative_to(root)) for p in sorted(out.glob("*.csv"))],
                "primary_tests": len(primary), "hac_bh_q05": int(primary.q_value_bh_primary.lt(.05).sum()),
                "conservative_bh_q05": int(primary.q_value_conservative_primary.lt(.05).sum()),
                "sizing_status_counts": primary.robustness_screen_status.value_counts().to_dict(),
                "private_positions_available": False, "full_original_category_extraction_claimed_complete": False}
    write_json(report / "build_manifest.json", manifest)
    return {"report": str(target.resolve()), "charts": len(chart_names), "primary_tests": len(primary)}
