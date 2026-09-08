"""Interpretable, explicitly observational political-probability return sensitivities.

All regressions use probability changes between consecutive equity sessions. Missing,
stale, or wide quotes invalidate both adjoining changes, never become zero changes.
The module is standalone: it does not import the pre-existing research package.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import t as student_t


BASE_SPREAD = 0.10
SPREAD_GATES = (0.05, 0.10, 0.15)
MAX_AGE_HOURS = 3.0
MIN_OBSERVATIONS = 20
BOOTSTRAP_DRAWS = 500
BLOCK_SESSIONS = 5
RNG_SEED = 20260909


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def equity_returns(prices: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Recompute returns on a strict SPY session calendar; never forward-fill prices."""
    required = {"ticker", "date", "adjusted_close"}
    if not required.issubset(prices):
        raise ValueError(f"equity_prices.csv missing {sorted(required - set(prices))}")
    data = prices.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.strftime("%Y-%m-%d")
    if data.duplicated(["ticker", "date"]).any():
        raise ValueError("Duplicate equity ticker/date observations")
    data["adjusted_close"] = pd.to_numeric(data["adjusted_close"], errors="coerce")
    data.loc[data.adjusted_close <= 0, "adjusted_close"] = np.nan
    benchmark = config["benchmark"]
    calendar = sorted(data.loc[data.ticker.eq(benchmark), "date"].unique())
    if not calendar:
        raise ValueError("Benchmark has no equity session calendar")
    wide = data.pivot(index="date", columns="ticker", values="adjusted_close").reindex(calendar)
    returns = wide.pct_change(fill_method=None)
    panel = pd.DataFrame({"date": calendar, "session_index": np.arange(len(calendar))})
    panel["market_return"] = returns[benchmark].to_numpy()
    records = []
    for basket in config["baskets"]:
        missing = set(basket["tickers"]) - set(returns.columns)
        if missing:
            raise ValueError(f"Missing fixed constituents in {basket['id']}: {sorted(missing)}")
        members = returns[basket["tickers"]]
        weights = basket.get("weights", [1 / len(basket["tickers"])] * len(basket["tickers"]))
        if len(weights) != len(basket["tickers"]):
            raise ValueError(f"Weight count mismatch for {basket['id']}")
        result = members.mul(weights, axis=1).sum(axis=1).where(members.notna().all(axis=1))
        panel[basket["id"]] = result.to_numpy()
        for date, value in result.items():
            records.append({"date": date, "basket_id": basket["id"], "basket_label": basket["label"],
                            "kind": basket["kind"], "constituents": "|".join(basket["tickers"]),
                            "weights": "|".join(map(str, weights)),
                            "return": value, "market_return": returns.loc[date, benchmark],
                            "excess_return": value - returns.loc[date, benchmark]
                            if basket["kind"] != "relative_return_contrast" else np.nan})
    return panel, pd.DataFrame(records)


def align_probability(quotes: pd.DataFrame, ticker: str, calendar: pd.DataFrame,
                      spread_gate: float = BASE_SPREAD) -> pd.DataFrame:
    """Require trustworthy endpoints on adjacent SPY sessions, including after gaps."""
    selected = quotes.loc[quotes.ticker.eq(ticker)].copy()
    if selected.duplicated("date").any():
        raise ValueError(f"Duplicate session quotes for {ticker}")
    selected["date"] = pd.to_datetime(selected["date"]).dt.strftime("%Y-%m-%d")
    selected = selected.set_index("date").reindex(calendar.date)
    for col in ("p", "bid", "ask", "spread", "quote_age_hours"):
        selected[col] = pd.to_numeric(selected[col], errors="coerce")
    # The recorded spread must agree with the bid/ask arithmetic when present.
    actual_spread = selected.ask - selected.bid
    consistent = (actual_spread - selected.spread).abs().le(1e-8)
    valid = (selected.p.between(0, 1) & selected.bid.between(0, 1) & selected.ask.between(0, 1)
             & selected.spread.between(0, spread_gate + 1e-10) & consistent
             & selected.p.ge(selected.bid - 1e-10) & selected.p.le(selected.ask + 1e-10)
             & selected.quote_age_hours.between(0, MAX_AGE_HOURS))
    if "quote_status" in selected:
        valid &= selected.quote_status.isin(["valid", "ok", "usable"])
    p = selected.p.where(valid)
    result = calendar[["date", "session_index"]].copy()
    result["p"] = p.to_numpy()
    result["spread"] = selected.spread.to_numpy()
    result["quote_valid"] = valid.to_numpy()
    result["delta_p"] = p.diff().to_numpy()
    result["delta_p_10pp"] = result.delta_p / 0.10
    result["endpoint_spread_sum"] = (selected.spread + selected.spread.shift()).to_numpy()
    result["meaningful_move"] = result.delta_p.abs().ge(0.01 - 1e-10)
    result["large_move_threshold"] = np.maximum(0.03, result.endpoint_spread_sum)
    result["large_move"] = result.delta_p.abs().ge(result.large_move_threshold - 1e-10)
    return result


def ols(y: np.ndarray, x: np.ndarray, session_index: np.ndarray | None = None) -> dict:
    """OLS with intercept already in X; HC3 and Newey-West covariance (three sessions)."""
    y, x = np.asarray(y, float), np.asarray(x, float)
    n, k = x.shape
    if n <= k + 2 or np.linalg.matrix_rank(x) != k:
        raise ValueError("Insufficient observations or rank-deficient design")
    inv = np.linalg.inv(x.T @ x)
    coefficients = inv @ x.T @ y
    residual = y - x @ coefficients
    leverage = np.einsum("ij,jk,ik->i", x, inv, x)
    hc3scores = x * (residual / np.maximum(1 - leverage, 1e-10))[:, None]
    hc3cov = inv @ hc3scores.T @ hc3scores @ inv
    scores = x * residual[:, None]
    meat = scores.T @ scores
    positions = np.arange(n) if session_index is None else np.asarray(session_index)
    # Session distances, rather than compressed valid-row positions, determine lag.
    for i in range(n):
        for j in range(i):
            distance = positions[i] - positions[j]
            if 0 < distance <= 3:
                pair = np.outer(scores[i], scores[j])
                meat += (1 - distance / 4) * (pair + pair.T)
    hac_cov = inv @ meat @ inv * n / (n - k)
    hc3se = np.sqrt(np.maximum(np.diag(hc3cov), 0))
    hacse = np.sqrt(np.maximum(np.diag(hac_cov), 0))
    critical = student_t.ppf(0.975, n - k)
    pvalue = float(2 * student_t.sf(abs(coefficients[1] / hacse[1]), n - k)) if hacse[1] > 0 else np.nan
    pvalue_hc3 = float(2 * student_t.sf(abs(coefficients[1] / hc3se[1]), n - k)) if hc3se[1] > 0 else np.nan
    centered = y - y.mean()
    return {"coef": coefficients, "residual": residual, "hc3_se": hc3se,
            "hac_se": hacse, "critical": float(critical), "p_value_hac": pvalue, "p_value_hc3": pvalue_hc3,
            "r_squared": float(1 - residual @ residual / (centered @ centered)) if centered @ centered > 0 else np.nan}


def design(frame: pd.DataFrame, conditioned: bool) -> tuple[np.ndarray, np.ndarray]:
    cols = [np.ones(len(frame)), frame.delta_p_10pp.to_numpy(float)]
    if conditioned:
        cols.append(frame.market_return.to_numpy(float))
    return frame.basket_return.to_numpy(float), np.column_stack(cols)


def moving_block_interval(frame: pd.DataFrame, conditioned: bool, draws: int = BOOTSTRAP_DRAWS,
                          seed: int = RNG_SEED, block: int = BLOCK_SESSIONS) -> tuple[float, float, int]:
    """Pairs bootstrap samples five consecutive calendar sessions, retaining missingness.

    The ordered input contains every equity session. Missing rows are dropped only
    after resampling, so data gaps are not converted to adjacent observations.
    """
    rng = np.random.default_rng(seed)
    n = len(frame)
    if n < block:
        return np.nan, np.nan, 0
    required = ["basket_return", "delta_p_10pp"] + (["market_return"] if conditioned else [])
    values = frame[required].to_numpy(float)
    estimates = []
    for _ in range(draws):
        starts = rng.integers(0, n - block + 1, size=int(np.ceil(n / block)))
        index = np.concatenate([np.arange(start, start + block) for start in starts])[:n]
        sample = values[index]
        sample = sample[np.isfinite(sample).all(axis=1)]
        if len(sample) < 10:
            continue
        y = sample[:, 0]
        x = np.column_stack([np.ones(len(sample)), sample[:, 1:]])
        if np.linalg.matrix_rank(x) != x.shape[1]:
            continue
        estimates.append(float(np.linalg.lstsq(x, y, rcond=None)[0][1]))
    if len(estimates) < max(20, int(0.8 * draws)):
        return np.nan, np.nan, len(estimates)
    low, high = np.quantile(estimates, [0.025, 0.975])
    return float(low), float(high), len(estimates)


def bh_adjust(p_values: np.ndarray) -> np.ndarray:
    values = np.asarray(p_values, float)
    result = np.full(len(values), np.nan)
    observed = np.flatnonzero(np.isfinite(values))
    if not len(observed):
        return result
    order = observed[np.argsort(values[observed])]
    adjusted = values[order] * len(order) / np.arange(1, len(order) + 1)
    result[order] = np.minimum(1, np.minimum.accumulate(adjusted[::-1])[::-1])
    return result


def fit_sensitivity(frame: pd.DataFrame, conditioned: bool, bootstrap_draws: int = BOOTSTRAP_DRAWS) -> dict:
    required = ["basket_return", "delta_p_10pp"] + (["market_return"] if conditioned else [])
    valid = frame.dropna(subset=required)
    n = len(valid)
    meaningful = int(valid.delta_p.abs().ge(0.01 - 1e-10).sum())
    out = {"n_observations": n, "n_calendar_sessions": len(frame),
           "valid_change_coverage": n / max(1, len(frame) - 1),
           "meaningful_shock_days": meaningful, "large_move_days": int(valid.large_move.sum()),
           "probability_min": float(frame.p.min()), "probability_max": float(frame.p.max()),
           "max_abs_daily_delta_pp": float(valid.delta_p.abs().max() * 100) if n else np.nan,
           "median_endpoint_spread_pp": float(valid.spread.median() * 100) if n else np.nan,
           "first_valid_date": str(valid.date.min()) if n else "",
           "last_valid_date": str(valid.date.max()) if n else "", "status": "estimated"}
    out.update({name: np.nan for name in ["beta_per_10pp", "intercept", "market_beta", "hc3_se", "hac3_se",
                                         "hac3_low", "hac3_high", "hc3_low", "hc3_high", "p_value_hac", "p_value_hc3", "r_squared", "bootstrap_low",
                                         "bootstrap_high", "bootstrap_valid_draws", "leave_largest_beta",
                                         "leave_largest_relative_change"]})
    out["largest_move_date"] = ""
    out["leave_largest_sign_stable"] = None
    if n < MIN_OBSERVATIONS:
        out["status"] = "insufficient_observations"
        return out
    if meaningful < 5 or valid.delta_p.std() < 0.0025:
        out["status"] = "insufficient_probability_variation"
        return out
    y, x = design(valid, conditioned)
    try:
        fitted = ols(y, x, valid.session_index.to_numpy())
    except ValueError:
        out["status"] = "rank_deficient"
        return out
    beta = fitted["coef"][1]
    out.update({"beta_per_10pp": float(beta), "intercept": float(fitted["coef"][0]),
                "market_beta": float(fitted["coef"][2]) if conditioned else np.nan,
                "hc3_se": float(fitted["hc3_se"][1]), "hac3_se": float(fitted["hac_se"][1]),
                "hac3_low": float(beta - fitted["critical"] * fitted["hac_se"][1]),
                "hac3_high": float(beta + fitted["critical"] * fitted["hac_se"][1]),
                "hc3_low": float(beta - fitted["critical"] * fitted["hc3_se"][1]),
                "hc3_high": float(beta + fitted["critical"] * fitted["hc3_se"][1]),
                "p_value_hac": fitted["p_value_hac"], "p_value_hc3": fitted["p_value_hc3"],
                "r_squared": fitted["r_squared"]})
    if bootstrap_draws:
        low, high, count = moving_block_interval(frame, conditioned, draws=bootstrap_draws)
        out.update({"bootstrap_low": low, "bootstrap_high": high, "bootstrap_valid_draws": count})
    largest = valid.delta_p.abs().idxmax()
    without = valid.drop(index=largest)
    try:
        y2, x2 = design(without, conditioned)
        removed_beta = ols(y2, x2, without.session_index.to_numpy())["coef"][1]
        out.update({"leave_largest_beta": float(removed_beta), "largest_move_date": str(valid.loc[largest, "date"]),
                    "leave_largest_sign_stable": bool(np.sign(beta) == np.sign(removed_beta)),
                    "leave_largest_relative_change": float(abs(removed_beta - beta) / abs(beta)) if abs(beta) > 1e-10 else np.nan})
    except ValueError:
        pass
    return out


def holdout(frame: pd.DataFrame, scenario: dict, basket: dict) -> list[dict]:
    """Exploratory fixed-calendar split: first 28 sessions train, later dates evaluate."""
    needed = ["basket_return", "delta_p_10pp", "market_return"]
    train = frame.iloc[:28].dropna(subset=needed)
    test = frame.iloc[28:].dropna(subset=needed)
    if len(train) < 20 or len(test) < 7 or train.delta_p.abs().ge(0.01 - 1e-10).sum() < 5:
        return []
    try:
        y, x = design(train, True)
        fit = ols(y, x, train.session_index.to_numpy())
    except ValueError:
        return []
    _, test_x = design(test, True)
    predicted = test_x @ fit["coef"]
    return [{"scenario_id": scenario["id"], "basket_id": basket["id"], "date": row.date,
             "training_first_date": train.date.min(), "training_last_date": train.date.max(),
             "n_training": len(train), "n_test": len(test), "actual_return": row.basket_return,
             "predicted_return": float(predicted[i]), "residual": row.basket_return - predicted[i],
             "training_beta_per_10pp": float(fit["coef"][1]),
             "interpretation": "Exploratory held-out discrepancy; not demonstrated alpha or political mispricing"}
            for i, row in enumerate(test.itertuples())]


def liquidity_validation(scenarios: list[dict], out: Path) -> pd.DataFrame:
    """Post-estimation tape screen; never changes the regression/test family.

    The available latest-28-day tape does not cover the entire regression window.
    Thresholds are analyst diagnostics, not empirically optimized trading rules.
    """
    summary_path, daily_path = out / "trade_summary.csv", out / "trade_daily.csv"
    summary = pd.read_csv(summary_path) if summary_path.exists() else pd.DataFrame()
    daily = pd.read_csv(daily_path) if daily_path.exists() else pd.DataFrame()
    rows = []
    for scenario in scenarios:
        matching = summary.loc[summary.scenario_id.eq(scenario["id"]) & summary.period.eq("all28")] if len(summary) else pd.DataFrame()
        day_rows = daily.loc[daily.scenario_id.eq(scenario["id"])] if len(daily) else pd.DataFrame()
        row = {"scenario_id": scenario["id"], "ticker": scenario["ticker"],
               "tape_window": "Latest 28 calendar days; shorter than full equity regression window",
               "executions": np.nan, "active_days": np.nan, "top_five_print_volume_share": np.nan,
               "liquidity_validation_status": "not_available", "liquidity_veto_reasons": "No completed matching public tape analysis"}
        if len(matching) == 1:
            item = matching.iloc[0]
            active = int(day_rows.loc[day_rows.executions.gt(0), "date"].nunique()) if len(day_rows) else 0
            executions = int(item.executions)
            concentration = float(item.top_five_print_volume_share)
            reasons = []
            if executions < 100:
                reasons.append("fewer_than_100_executions")
            if active < 10:
                reasons.append("fewer_than_10_active_days")
            if not np.isfinite(concentration) or concentration > 0.50:
                reasons.append("top_five_volume_share_above_50pct_or_unavailable")
            row.update({"executions": executions, "active_days": active,
                        "top_five_print_volume_share": concentration,
                        "liquidity_validation_status": "veto_sizing_use" if reasons else "passes_descriptive_tape_gate",
                        "liquidity_veto_reasons": "|".join(reasons)})
        rows.append(row)
    result = pd.DataFrame(rows)
    result.to_csv(out / "liquidity_validation.csv", index=False)
    return result


def robustness_summary(results: pd.DataFrame, liquidity: pd.DataFrame) -> pd.DataFrame:
    base = results.loc[results.primary_test].copy()
    for spread, label in [(0.05, "tight"), (0.15, "loose")]:
        comparison = results.loc[results.scenario_primary & results.model.eq("market_conditioned") & results.spread_gate.eq(spread),
                                 ["scenario_id", "basket_id", "status", "n_observations", "beta_per_10pp"]].copy()
        comparison = comparison.rename(columns={col: f"{label}_{col}" for col in comparison if col not in ["scenario_id", "basket_id"]})
        base = base.merge(comparison, on=["scenario_id", "basket_id"], validate="one_to_one")
    base = base.merge(liquidity, on=["scenario_id", "ticker"], validate="many_to_one")
    rows = []
    for _, row in base.iterrows():
        reasons = []
        if row.status != "estimated":
            reasons.append(row.status)
        if not np.isfinite(row.q_value_conservative_primary) or row.q_value_conservative_primary >= 0.10:
            reasons.append("conservative_q_not_below_10pct")
        if not row.bootstrap_excludes_zero:
            reasons.append("block_interval_includes_zero_or_unavailable")
        if row.tight_status != "estimated":
            reasons.append("tight_spread_estimate_unavailable")
        elif np.sign(row.tight_beta_per_10pp) != np.sign(row.beta_per_10pp):
            reasons.append("tight_spread_sign_reversal")
        if row.loose_status == "estimated" and np.sign(row.loose_beta_per_10pp) != np.sign(row.beta_per_10pp):
            reasons.append("loose_spread_sign_reversal")
        if pd.isna(row.leave_largest_sign_stable) or not bool(row.leave_largest_sign_stable):
            reasons.append("largest_move_removal_sign_unstable_or_unavailable")
        if row.meaningful_shock_days < 10:
            reasons.append("fewer_than_10_meaningful_days")
        if row.large_move_days < 3:
            reasons.append("fewer_than_3_moves_exceeding_quote_noise_rule")
        if row.liquidity_validation_status != "passes_descriptive_tape_gate":
            reasons.append("tape_liquidity_veto_or_unavailable")
        record = row.to_dict()
        record["robustness_screen_status"] = "passes_pilot_diagnostics" if not reasons else "not_ready_for_sizing"
        record["robustness_failures"] = "|".join(reasons)
        rows.append(record)
    return pd.DataFrame(rows)


def audit_coefficients(results: pd.DataFrame, quotes: pd.DataFrame, panel: pd.DataFrame) -> dict:
    """Independently verify slopes by covariance / FWL rather than normal equations.

    Uses the same validated alignment and exported test definitions; this numerical
    audit does not independently establish the correctness of source observations.
    """
    differences = []
    estimated = results.loc[results.status.eq("estimated")]
    for (ticker, gate), rows in estimated.groupby(["ticker", "spread_gate"]):
        aligned = align_probability(quotes, ticker, panel, float(gate))
        data = panel.merge(aligned.drop(columns="session_index"), on="date", validate="one_to_one")
        for row in rows.itertuples():
            conditioned = row.model == "market_conditioned"
            names = [row.basket_id, "delta_p_10pp"] + (["market_return"] if conditioned else [])
            observed = data.dropna(subset=names)
            x = observed.delta_p_10pp.to_numpy()
            y = observed[row.basket_id].to_numpy()
            if conditioned:
                controls = np.column_stack([np.ones(len(observed)), observed.market_return])
                xr = x - controls @ np.linalg.lstsq(controls, x, rcond=None)[0]
                yr = y - controls @ np.linalg.lstsq(controls, y, rcond=None)[0]
            else:
                xr, yr = x - x.mean(), y - y.mean()
            slope = float(xr @ yr / (xr @ xr))
            differences.append(abs(slope - row.beta_per_10pp))
    maximum = float(max(differences)) if differences else 0.0
    return {"status": "pass" if maximum < 1e-10 else "fail",
            "estimated_rows_independently_checked": len(differences),
            "method": "Frisch-Waugh-Lovell residualization and centered covariance, independently comparing exported coefficients; source alignment shared",
            "maximum_absolute_coefficient_difference": maximum}


def run(inputs: Path, out: Path) -> dict:
    inputs, out = Path(inputs), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    config_path = inputs.parent / "config" / "equity_baskets.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    scenarios_obj = json.loads((inputs / "scenarios.json").read_text(encoding="utf-8"))
    scenarios = scenarios_obj if isinstance(scenarios_obj, list) else scenarios_obj["scenarios"]
    quotes = pd.read_csv(inputs / "market_session_quotes.csv")
    quotes["date"] = pd.to_datetime(quotes["date"]).dt.strftime("%Y-%m-%d")
    panel, baskets = equity_returns(pd.read_csv(inputs / "equity_prices.csv"), config)
    baskets.to_csv(out / "basket_returns.csv", index=False)
    event_labels = {}
    if (inputs / "event_calendar.csv").exists():
        calendar = pd.read_csv(inputs / "event_calendar.csv")
        for date, group in calendar.groupby("date"):
            event_labels[str(date)] = " | ".join(group["label"].astype(str))
    matrix, event_moves, heldout, quote_diagnostics = [], [], [], []
    for scenario in scenarios:
        for spread_gate in SPREAD_GATES:
            aligned = align_probability(quotes, scenario["ticker"], panel, spread_gate)
            base = panel.merge(aligned.drop(columns="session_index"), on="date", validate="one_to_one")
            base["prior_session_date"] = base.date.shift()
            quote_diagnostics.append({"scenario_id": scenario["id"], "scenario_label": scenario["label"],
                                      "ticker": scenario["ticker"], "spread_gate": spread_gate,
                                      "n_session_quotes": len(aligned), "valid_quotes": int(aligned.quote_valid.sum()),
                                      "valid_adjacent_changes": int(aligned.delta_p.notna().sum()),
                                      "meaningful_shock_days": int(aligned.meaningful_move.sum()),
                                      "large_move_days": int(aligned.large_move.sum()),
                                      "first_valid_date": aligned.loc[aligned.quote_valid, "date"].min(),
                                      "last_valid_date": aligned.loc[aligned.quote_valid, "date"].max()})
            for basket in config["baskets"]:
                frame = base.rename(columns={basket["id"]: "basket_return"})
                for conditioned in (False, True):
                    fitted = fit_sensitivity(frame, conditioned, BOOTSTRAP_DRAWS if spread_gate == BASE_SPREAD else 0)
                    matrix.append({"scenario_id": scenario["id"], "scenario_label": scenario["label"],
                                   "ticker": scenario["ticker"], "issue_id": scenario.get("issue_id", ""),
                                   "basket_id": basket["id"], "basket_label": basket["label"], "basket_kind": basket["kind"],
                                   "model": "market_conditioned" if conditioned else "raw",
                                   "spread_gate": spread_gate, "scenario_primary": bool(scenario.get("primary", True)),
                                   "primary_test": conditioned and spread_gate == BASE_SPREAD and bool(scenario.get("primary", True)),
                                   **fitted})
                if spread_gate == BASE_SPREAD:
                    heldout.extend(holdout(frame, scenario, basket))
            if spread_gate == BASE_SPREAD:
                events = base.loc[base.delta_p.notna()].copy()
                events["abs_move"] = events.delta_p.abs()
                for _, row in events.nlargest(5, "abs_move").iterrows():
                    record = {"scenario_id": scenario["id"], "scenario_label": scenario["label"],
                              "ticker": scenario["ticker"], "date": row.date, "delta_p_pp": row.delta_p * 100,
                              "p": row.p, "endpoint_spread_sum_pp": row.endpoint_spread_sum * 100,
                              "large_move": row.large_move, "large_move_threshold_pp": row.large_move_threshold * 100,
                              "selection_basis": "Five largest observed absolute probability moves; not independently identified political news",
                              "prior_session_date": row.prior_session_date,
                              "overlapping_registered_events": " | ".join(f"{date}: {label}" for date, label in event_labels.items()
                                                                             if str(row.prior_session_date) <= date <= row.date)
                              or "Not reviewed / no dated registry match",
                              "event_timing_limitation": "Date-only matches include prior close date because intraday timing is mostly unavailable; context is not a verified shock",
                              "market_return": row.market_return}
                    record.update({basket["id"]: row[basket["id"]] for basket in config["baskets"]})
                    event_moves.append(record)
    results = pd.DataFrame(matrix)
    results["q_value_bh_primary"] = np.nan
    results["q_value_hc3_primary"] = np.nan
    results["q_value_conservative_primary"] = np.nan
    primary = results.primary_test & results.status.eq("estimated")
    if primary.any():
        results.loc[primary, "q_value_bh_primary"] = bh_adjust(results.loc[primary, "p_value_hac"].to_numpy())
        results.loc[primary, "q_value_hc3_primary"] = bh_adjust(results.loc[primary, "p_value_hc3"].to_numpy())
        conservative_p = results.loc[primary, ["p_value_hac", "p_value_hc3"]].max(axis=1).to_numpy()
        results.loc[primary, "q_value_conservative_primary"] = bh_adjust(conservative_p)
    # Every estimable primary combination enters the family, not only interesting rows.
    results["evidence_grade"] = np.where(results.status.eq("estimated"), "pilot_observational", "insufficient_evidence")
    if "bootstrap_low" in results:
        results["bootstrap_excludes_zero"] = (results.bootstrap_low.gt(0) | results.bootstrap_high.lt(0))
    results.to_csv(out / "sensitivity_matrix.csv", index=False)
    liquidity = liquidity_validation(scenarios, out)
    robustness = robustness_summary(results, liquidity)
    robustness.to_csv(out / "sensitivity_robustness.csv", index=False)
    pd.DataFrame(quote_diagnostics).to_csv(out / "sensitivity_diagnostics.csv", index=False)
    pd.DataFrame(event_moves).to_csv(out / "event_moves.csv", index=False)
    holdout_columns = ["scenario_id", "basket_id", "date", "training_first_date", "training_last_date",
                       "n_training", "n_test", "actual_return", "predicted_return", "residual",
                       "training_beta_per_10pp", "interpretation"]
    pd.DataFrame(heldout, columns=holdout_columns).to_csv(out / "holdout_residuals.csv", index=False)
    numerical_audit = audit_coefficients(results, quotes, panel)
    (out / "sensitivity_numerical_audit.json").write_text(json.dumps(numerical_audit, indent=2), encoding="utf-8")
    if numerical_audit["status"] != "pass":
        raise ValueError("Independent numerical coefficient audit failed")
    manifest = {"schema_version": 1, "status": "complete", "scenario_count": len(scenarios),
                "basket_count": len(config["baskets"]), "equity_session_count": len(panel),
                "first_equity_date": panel.date.min(), "last_equity_date": panel.date.max(),
                "matrix_rows": len(results), "primary_tests_specified": int(results.primary_test.sum()),
                "primary_tests_estimated": int(primary.sum()),
                "primary_bh_q_below_005": int(results.loc[primary, "q_value_bh_primary"].lt(0.05).sum()),
                "primary_bh_q_below_010": int(results.loc[primary, "q_value_bh_primary"].lt(0.10).sum()),
                "primary_conservative_q_below_010": int(results.loc[primary, "q_value_conservative_primary"].lt(0.10).sum()),
                "primary_passing_pilot_robustness_screen": int(robustness.robustness_screen_status.eq("passes_pilot_diagnostics").sum()),
                "status_counts": results.loc[results.primary_test, "status"].value_counts().to_dict(),
                "holdout_rows": len(heldout), "event_move_rows": len(event_moves),
                "maximum_quote_age_hours": MAX_AGE_HOURS, "baseline_maximum_spread": BASE_SPREAD,
                "robustness_spread_gates": list(SPREAD_GATES), "minimum_observations": MIN_OBSERVATIONS,
                "minimum_meaningful_shock_days": 5, "meaningful_shock_definition": "Absolute probability change >=1 percentage point; not a count of independent political events",
                "large_move_definition": "Absolute probability change >= max(3 percentage points, sum of two endpoint bid-ask spreads)",
                "beta_unit": "Decimal equity return per +10 percentage-point probability change",
                "uncertainty": "HC3 and HAC with three actual equity-session lags; 95% Student-t HAC intervals. Baseline also uses 500 five-session moving-block pairs-bootstrap draws with missingness retained.",
                "multiple_testing": "Benjamini-Hochberg across all estimable market-conditioned baseline tests. Descriptive dependent-family adjustment, not a guarantee of discovery control under arbitrary dependence.",
                "bootstrap_seed": RNG_SEED, "bootstrap_draws": BOOTSTRAP_DRAWS,
                "post_estimation_robustness_screen": "Separate from unchanged HAC/BH family: max(HC3,HAC) p-value BH q<10%; block interval excludes zero; tight-spread fit available with same sign; loose-spread sign stable; leave-largest sign stable; >=10 meaningful days and >=3 quote-noise-exceeding moves; latest28d tape >=100 executions, >=10 active days, top5prints <=50% volume. Analyst-specified diagnostic, not a validated trading rule.",
                "numerical_audit": numerical_audit,
                "limitations": ["All estimated sensitivities remain pilot observational associations; no causal identification.",
                                "Forty-three equity dates supply at most 42 adjacent returns, not millions of independent observations.",
                                "Contemporaneous market conditioning removes broad-market co-movement and may remove a policy transmission channel.",
                                "Probability endpoints and equities have different liquidity; quote quality gates cannot eliminate all measurement error.",
                                "No dollar-yield, oil-price, or company-news controls in these deliberately small baseline models.",
                                "Event-move table is selected on probability movements and is not an independently identified political-event study.",
                                "Holdout residuals are exploratory model discrepancies, not established alpha or mispricing.",
                                "A +10pp coefficient is a scale unit; inspect the observed probability range before applying a scenario.",
                                "Basket definitions are analyst-specified with knowledge of prior performance, not preregistered; historical membership is not point-in-time.",
                                "Three paired-return contrasts were added after reviewing prior performance and are included in the full primary multiple-testing family.",
                                "One-stock proxies are explicitly labelled. Contrast returns are arithmetic differences, not funded portfolio total returns."],
                "input_sha256": {name: _sha(inputs / name) for name in ["equity_prices.csv", "market_session_quotes.csv", "scenarios.json"]},
                "basket_config_sha256": _sha(config_path)}
    if (inputs / "event_calendar.csv").exists():
        manifest["input_sha256"]["event_calendar.csv"] = _sha(inputs / "event_calendar.csv")
    manifest["optional_tape_analysis_sha256"] = {name: _sha(out / name) for name in ["trade_summary.csv", "trade_daily.csv"] if (out / name).exists()}
    (out / "sensitivity_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
