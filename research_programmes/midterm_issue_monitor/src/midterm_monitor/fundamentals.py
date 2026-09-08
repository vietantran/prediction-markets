"""Explicit company underwriting cases; no fitted election-to-EPS conversion."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


CONFIG = Path(__file__).resolve().parents[2] / "config" / "fundamental_cases.json"
ALLOWED_STATUSES = {"observed", "derived_matching_periods"}


def load_config(path: Path = CONFIG) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("dividends_included") is not False:
        raise ValueError("These calculations produce price returns only.")
    for company in config["companies"]:
        for case in company["cases"]:
            if case["eps_cagr"] <= -1 or case["exit_pe"] <= 0:
                raise ValueError("Invalid growth or multiple assumption")
    return config


def eligible_fact(
    facts: pd.DataFrame,
    ticker: str,
    metric: str,
    as_of: pd.Timestamp,
    period_type: str = "ttm",
) -> pd.Series | None:
    """Withhold missing/stale/late facts rather than reverting to a stale annual."""
    candidates = facts.loc[
        (facts["ticker"] == ticker)
        & (facts["metric"] == metric)
        & (facts["period_type"] == period_type)
        & facts["status"].isin(ALLOWED_STATUSES)
    ].copy()
    if candidates.empty:
        return None
    candidates["value"] = pd.to_numeric(candidates["value"], errors="coerce")
    candidates["_period"] = pd.to_datetime(candidates["period_end"], utc=True, errors="coerce")
    candidates["_known"] = pd.to_datetime(candidates["latest_known_at"], utc=True, errors="coerce")
    eligible = (
        candidates["value"].notna()
        & candidates["_period"].notna()
        & candidates["_known"].notna()
        & (candidates["_known"] <= as_of)
        & (candidates["_period"] <= as_of)
        & ((as_of - candidates["_period"]).dt.days <= 180)
    )
    candidates = candidates.loc[eligible].sort_values(["_period", "_known"])
    return None if candidates.empty else candidates.iloc[-1]


def latest_completed_price(prices: pd.DataFrame, ticker: str, as_of: pd.Timestamp) -> pd.Series | None:
    rows = prices.loc[prices["ticker"] == ticker].copy()
    rows["_date"] = pd.to_datetime(rows["date"], errors="coerce")
    rows["close"] = pd.to_numeric(rows["close"], errors="coerce")
    rows = rows.loc[rows["_date"].notna() & rows["close"].gt(0)]
    valid = []
    for date in rows["_date"]:
        close = datetime.combine(date.date(), time(16), tzinfo=ZoneInfo("America/New_York"))
        valid.append(pd.Timestamp(close).tz_convert("UTC") <= as_of)
    rows = rows.loc[valid].sort_values("_date")
    return None if rows.empty else rows.iloc[-1]


def calculate_case(
    eps: float, price: float, growth: float, multiple: float, years: float, hurdle: float
) -> dict:
    if min(eps, price, multiple, years) <= 0 or growth <= -1 or hurdle <= -1:
        raise ValueError("Positive earnings, price, multiple and horizon required")
    terminal_eps = eps * (1 + growth) ** years
    terminal_price = terminal_eps * multiple
    return {
        "terminal_eps_usd": terminal_eps,
        "terminal_price_usd": terminal_price,
        "price_return": terminal_price / price - 1,
        "annualized_price_return": (terminal_price / price) ** (1 / years) - 1,
        "break_even_eps_cagr": (price / (eps * multiple)) ** (1 / years) - 1,
        "eps_cagr_for_hurdle": (price * (1 + hurdle) ** years / (eps * multiple)) ** (1 / years) - 1,
        "maximum_entry_for_hurdle_usd": terminal_price / (1 + hurdle) ** years,
        "required_entry_discount": 1 - terminal_price / ((1 + hurdle) ** years * price),
    }


def _value(row: pd.Series | None) -> float | None:
    return None if row is None else float(row["value"])


def _safe_ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b is not None and b != 0 else None


def run(inputs: Path, out: Path) -> dict:
    config = load_config()
    as_of = pd.Timestamp(config["as_of"])
    facts = pd.read_csv(inputs / "company_fundamentals.csv")
    prices = pd.read_csv(inputs / "equity_prices.csv")
    out.mkdir(parents=True, exist_ok=True)
    scenarios, sensitivities, exclusions, provenance = [], [], [], []
    for company in config["companies"]:
        ticker = company["ticker"]
        price_row = latest_completed_price(prices, ticker, as_of)
        metrics = {
            name: eligible_fact(facts, ticker, name, as_of)
            for name in ["diluted_eps", "revenue", "operating_income", "operating_cash_flow", "cash_capex"]
        }
        shares_row = eligible_fact(facts, ticker, "common_shares_outstanding", as_of, "instant")
        eps_row = metrics["diluted_eps"]
        if price_row is None or eps_row is None:
            exclusions.append(
                {"ticker": ticker, "reason": "Missing eligible EPS or completed-session raw close"}
            )
            continue
        reported_eps, price = float(eps_row["value"]), float(price_row["close"])
        eps = reported_eps + company["eps_adjustment_usd"]
        if eps <= 0:
            exclusions.append({"ticker": ticker, "reason": "Nonpositive adjusted starting EPS"})
            continue
        revenue = _value(metrics["revenue"])
        ebit = _value(metrics["operating_income"])
        cfo = _value(metrics["operating_cash_flow"])
        capex = _value(metrics["cash_capex"])
        market_cap = price * float(shares_row["value"]) if shares_row is not None else None
        # Industrial FCF/EBIT conventions are inappropriate for the bank case.
        fcf = cfo - capex if ticker != "JPM" and cfo is not None and capex is not None else None
        margin = _safe_ratio(ebit, revenue) if ticker != "JPM" else None
        ebit_100bp = revenue * 0.01 if margin is not None and revenue is not None else None
        common = {
            "ticker": ticker,
            "issue": company["issue"],
            "price_date": str(price_row["date"]),
            "raw_close_usd": price,
            "reported_ttm_eps_usd": reported_eps,
            "eps_adjustment_usd": company["eps_adjustment_usd"],
            "starting_eps_usd": eps,
            "reported_trailing_pe": price / reported_eps,
            "starting_pe": price / eps,
            "eps_period_end": eps_row["period_end"],
            "eps_status": eps_row["status"],
            "eps_source_urls": eps_row["source_urls"],
            "company_evidence_url": company["source_url"],
        }
        sensitivities.append(
            {
                **common,
                "revenue_ttm_usd": revenue,
                "operating_income_ttm_usd": ebit,
                "operating_margin": margin,
                "ebit_change_100bp_margin_usd": ebit_100bp,
                "ebit_fraction_change_100bp_margin": _safe_ratio(ebit_100bp, ebit),
                "cash_fcf_proxy_usd": fcf,
                "market_cap_proxy_usd": market_cap,
                "cash_fcf_yield_proxy": _safe_ratio(fcf, market_cap),
                "margin_stress_note": "Constant revenue; a one-percentage-point group EBIT-margin move, not a medical-cost-ratio, tariff-rate, tax-rate or EPS shock.",
                "fcf_note": "CFO less cash capital expenditure; excludes lease additions and is not normalized distributable cash flow. Bank FCF omitted.",
                "business_evidence": company["business_evidence"],
                "political_bridge": company["political_bridge"],
                "decision_gate": company["decision_gate"],
                "invalidation": company["invalidation"],
            }
        )
        for case in company["cases"]:
            for years in config["horizons_years"]:
                scenarios.append(
                    {
                        **common,
                        "case": case["case"],
                        "years": years,
                        "eps_cagr": case["eps_cagr"],
                        "exit_pe": case["exit_pe"],
                        "annual_price_return_hurdle": config["annual_price_return_hurdle"],
                        **calculate_case(
                            eps,
                            price,
                            case["eps_cagr"],
                            case["exit_pe"],
                            years,
                            config["annual_price_return_hurdle"],
                        ),
                        "assumption_status": config["assumption_status"],
                        "probability_weight": None,
                        "dividends_included": False,
                        "eps_adjustment_reason": company["eps_adjustment_reason"],
                        "assumption_rationale": company["assumption_rationale"],
                    }
                )
        for metric, row in {**metrics, "common_shares_outstanding": shares_row}.items():
            if row is not None:
                provenance.append(
                    {
                        "ticker": ticker,
                        "metric": metric,
                        "value": row["value"],
                        "period_end": row["period_end"],
                        "latest_known_at": row["latest_known_at"],
                        "source_urls": row["source_urls"],
                        "status": row["status"],
                    }
                )
    pd.DataFrame(scenarios).to_csv(out / "fundamental_scenarios.csv", index=False)
    pd.DataFrame(sensitivities).to_csv(out / "financial_sensitivities.csv", index=False)
    pd.DataFrame(provenance).to_csv(out / "fundamental_fact_provenance.csv", index=False)
    pd.DataFrame(exclusions, columns=["ticker", "reason"]).to_csv(
        out / "fundamental_exclusions.csv", index=False
    )
    summary = {
        "as_of": config["as_of"],
        "status": "complete" if not exclusions else "complete_with_exclusions",
        "companies": len(sensitivities),
        "scenario_rows": len(scenarios),
        "excluded": exclusions,
        "assumption_status": config["assumption_status"],
        "probability_weights": "none",
        "return_definition": "Nominal USD price return; excludes dividends, taxes, transaction costs and hedging costs.",
        "input_sha256": {
            name: hashlib.sha256((inputs / name).read_bytes()).hexdigest()
            for name in ["company_fundamentals.csv", "equity_prices.csv"]
        },
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "model_limit": "Starting EPS is reconstructed TTM; changing shares and one-offs require underwriting. Only the disclosed Microsoft OpenAI gain is explicitly removed. Cases are financial sensitivities, not election-conditioned earnings forecasts.",
        "all_scenario_results_finite": all(math.isfinite(row["price_return"]) for row in scenarios),
    }
    (out / "fundamental_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
