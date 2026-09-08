"""Dated public evidence, policy diffusion and institutional election scenarios.

No polling observations are pooled across question/population definitions, and
policy stages are not converted to invented enactment probabilities.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


CONFIG = Path(__file__).resolve().parents[2] / "config" / "policy_cases.json"


def eligible_record(record: dict, as_of: pd.Timestamp) -> bool:
    if record.get("as_of_eligible") is not True:
        return False
    if record.get("published_at"):
        return pd.Timestamp(record["published_at"]) <= as_of
    if record.get("date"):
        # Date-only observations on the cutoff date have unknown availability.
        return str(record["date"])[:10] < as_of.date().isoformat()
    return record.get("publication_precision") == "undated_standing_background"


def observations_table(records: list[dict]) -> pd.DataFrame:
    rows = []
    for record in records:
        for observation in record.get("observations", []):
            value = observation.get("value")
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            rows.append(
                {
                    "record_id": record["id"],
                    "topic": record["topic"],
                    "jurisdiction": record["jurisdiction"],
                    "publication_date": record.get("date"),
                    "fieldwork_start": (record.get("fieldwork") or {}).get("start"),
                    "fieldwork_end": (record.get("fieldwork") or {}).get("end"),
                    "observation_date": observation.get("date"),
                    "metric": observation["metric"],
                    "numeric_value": value if numeric else None,
                    "text_value": "" if numeric else json.dumps(value, ensure_ascii=False),
                    "unit": observation.get("unit"),
                    "population": observation.get("population"),
                    "signal_type": record["salience_signal_type"],
                    "response": observation.get("response"),
                    "question_paraphrase": record.get("question"),
                    "source_url": record["source_url"],
                    "caveats": " | ".join(record.get("uncertainty", [])),
                }
            )
    return pd.DataFrame(rows)


def cross_party_table(observations: pd.DataFrame) -> pd.DataFrame:
    """Only like-for-like party subgroups for the same question and wave."""
    selected = observations.loc[
        observations["record_id"].isin(["P05", "P06"])
        & observations["metric"].eq("data_centers_costs_outweigh_benefits")
        & observations["population"].fillna("").str.contains("Republican|Democratic|independent")
    ].copy()
    selected["party"] = selected["population"].str.split("_").str[0]
    selected["interpretation"] = (
        "Agreement on negative cost-benefit assessment; does not establish agreement on the same remedy or vote choice."
    )
    return selected


def institutional_table(config: dict, quotes: pd.DataFrame | None, as_of: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for case in config["institutional_scenarios"]:
        row = {
            **case,
            "mid_probability": None,
            "bid_usd": None,
            "ask_usd": None,
            "quote_status": "missing_snapshot",
            "quote_observed_utc": None,
        }
        if quotes is not None:
            found = quotes.loc[quotes["ticker"].eq(case["ticker"])]
            if not found.empty:
                quote = found.iloc[-1]
                observed = pd.to_datetime(quote.get("quote_observed_utc"), utc=True, errors="coerce")
                if pd.notna(observed) and observed <= as_of and quote.get("quote_status") == "usable":
                    for name in [
                        "mid_probability",
                        "bid_usd",
                        "ask_usd",
                        "quote_status",
                        "quote_observed_utc",
                    ]:
                        row[name] = quote.get(name)
        row["quote_interpretation"] = (
            "Observed joint organizational-control contract for February 2027; midpoint is not normalized and is not a policy-enactment probability."
        )
        row["fed_path"] = (
            "FOMC responds to its mandate and economic conditions; congressional control does not mechanically determine rate decisions."
        )
        rows.append(row)
    return pd.DataFrame(rows)


def run(inputs: Path, out: Path) -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    packet = json.loads((inputs / "public_evidence.json").read_text(encoding="utf-8"))
    as_of = pd.Timestamp(config["as_of"])
    if pd.Timestamp(packet["as_of"]) != as_of:
        raise ValueError("Evidence and scenario freeze differ")
    records = [record for record in packet["records"] if eligible_record(record, as_of)]
    record_index = {record["id"]: record for record in records}
    out.mkdir(parents=True, exist_ok=True)
    observations = observations_table(records)
    observations.to_csv(out / "electoral_evidence.csv", index=False)
    cross_party = cross_party_table(observations)
    cross_party.to_csv(out / "cross_party_evidence.csv", index=False)
    trends = []
    for trend in packet["trend_comparisons"]:
        if trend["record_id"] not in record_index:
            continue
        if abs(trend["latest"] - trend["baseline"] - trend["delta_pp"]) > 1e-9:
            raise ValueError(f"Incorrect poll arithmetic: {trend['id']}")
        source = record_index[trend["record_id"]]
        trends.append(
            {
                **trend,
                "topic": source["topic"],
                "source_url": source["source_url"],
                "statistical_significance": "Not estimated: prior-wave sample and covariance unavailable in packet",
            }
        )
    pd.DataFrame(trends).to_csv(out / "public_opinion_trends.csv", index=False)
    state_rows = []
    for case in config["states"]:
        missing = set(case["evidence_ids"]) - set(record_index)
        if missing:
            raise ValueError(f"Missing eligible policy evidence: {missing}")
        state_rows.append(
            {
                **case,
                "evidence_ids": "|".join(case["evidence_ids"]),
                "source_urls": " | ".join(record_index[key]["source_url"] for key in case["evidence_ids"]),
            }
        )
    pd.DataFrame(state_rows).to_csv(out / "state_policy_diffusion.csv", index=False)
    quote_path = inputs / "election_snapshot.csv"
    quotes = pd.read_csv(quote_path) if quote_path.exists() else None
    institutions = institutional_table(config, quotes, as_of)
    institutions.to_csv(out / "institutional_scenarios.csv", index=False)
    sources = [
        {
            key: record.get(key)
            for key in [
                "id",
                "date",
                "source_url",
                "source_title",
                "claim",
                "stage",
                "jurisdiction",
                "salience_signal_type",
                "trend_status",
            ]
        }
        for record in records
    ]
    pd.DataFrame(sources).to_csv(out / "political_sources.csv", index=False)
    eligible_quotes = institutions["mid_probability"].dropna()
    minimum_party = cross_party.groupby("record_id")["numeric_value"].min().to_dict()
    summary = {
        "as_of": config["as_of"],
        "status": "complete",
        "eligible_primary_registry_records": len(records),
        "numeric_observations": int(observations["numeric_value"].notna().sum()),
        "comparable_trends": len(trends),
        "selected_state_cases": len(state_rows),
        "dated_records": sum(record.get("date") is not None for record in records),
        "standing_background_records": sum(record.get("date") is None for record in records),
        "excluded_registry_records": sorted(
            set(record["id"] for record in packet["records"]) - set(record_index)
        ),
        "minimum_partisan_negative_cost_benefit_percent": minimum_party,
        "joint_contracts_with_usable_quotes": len(eligible_quotes),
        "joint_midpoint_sum": float(eligible_quotes.sum()) if len(eligible_quotes) == 4 else None,
        "joint_normalized": False,
        "inferred_independence": False,
        "case_moratorium_count": None,
        "case_scope_warning": "Purposive cases are not a moratorium census. No nationwide count or geographic diffusion rate is inferred from their number.",
        "key_findings": [
            "Gasoline concern has the largest January-to-July increase among the comparable named household-cost series; concern is not electoral-priority causality.",
            "Data-center cost-benefit opposition rose nationally and in Wisconsin; all three measured partisan groups exceed 50%, while agreement on the same remedy is unproven.",
            "Wisconsin's current-issue priority and turnout-screen results caution against assigning its race repricing to data centers.",
            "State tariff, permitting, advisory and AI-compliance responses are economically distinct; federal gridlock does not eliminate them.",
            "Healthcare concern remains high and remedies differ; no broad accelerating electoral-salience claim follows from this packet.",
        ],
        "fed_background": config["fed_background"],
        "constraints": config["common_constraints"],
        "input_sha256": hashlib.sha256((inputs / "public_evidence.json").read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
    }
    (out / "political_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
