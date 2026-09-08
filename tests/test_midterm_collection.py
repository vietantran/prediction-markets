"""Frozen subset reuse must not silently mix runs or leave a broader stale cohort."""
import csv
import json

import pytest

from scripts.collect_midterm_research import prepare_subset, read_json, write_json
from scripts.select_midterm_cohort import select


CONFIG = {"asof_ts": 2000, "asof_utc": "1970-01-01T00:33:20Z", "hourly_start_ts": 1000,
          "eligibility_start_ts": 500, "public_api_only": True}


def fixture_source(tmp_path, tickers=("A",), settled=None):
    source = tmp_path / "source"
    write_json(source / "run_config.json", CONFIG)
    for ticker in tickers:
        write_json(source / "discovery" / f"SERIES_{ticker}.json.gz",
                   {"status": "complete", "series_ticker": f"SERIES_{ticker}",
                    "markets": [{"ticker": ticker, "settlement_ts": settled}]})
    inventory = tmp_path / "cohort.csv"
    write_inventory(inventory, tickers)
    return source, inventory, tmp_path / "supplement"


def write_inventory(path, tickers):
    path.write_text("ticker\n" + "\n".join(tickers) + "\n", encoding="utf-8")


@pytest.mark.parametrize("asof, label", [(1999, "common"), (2000, "presettlement")])
def test_complete_artifact_from_wrong_cutoff_or_window_is_not_reused(tmp_path, asof, label):
    source, inventory, out = fixture_source(tmp_path)
    write_json(source / "candles" / "A.json.gz",
               {"status": "complete", "schema_version": 1, "asof_ts": asof, "window_label": label})
    manifest = prepare_subset(source, inventory, out)
    assert manifest["reused_complete_markets"] == 0
    assert manifest["markets_requiring_collection"] == 1


def test_existing_subset_rejects_frozen_configuration_change_before_mutating(tmp_path):
    source, inventory, out = fixture_source(tmp_path)
    prepare_subset(source, inventory, out)
    old = (out / "run_config.json").read_bytes()
    write_json(source / "run_config.json", {**CONFIG, "hourly_start_ts": 900})
    with pytest.raises(ValueError, match="(?i)(config|frozen|incompat|mismatch|hash)"):
        prepare_subset(source, inventory, out)
    assert (out / "run_config.json").read_bytes() == old


def test_common_only_reuses_old_settlement_but_optional_extension_requires_its_artifact(tmp_path):
    source, inventory, out = fixture_source(tmp_path, settled=800)
    write_json(source / "candles" / "A.json.gz",
               {"status": "complete", "schema_version": 1, "asof_ts": 2000, "window_label": "common",
                "market_ticker": "A", "requested_start_ts": 1000, "requested_end_ts": 2000,
                "hourly": [], "daily": [], "errors": [],
                "windows": [{"period_interval": period, "status": "no_overlap", "requested_start_ts": 1000,
                              "requested_end_ts": 2000, "effective_start_ts": 1000, "effective_end_ts": 800}
                             for period in [60, 1440]]})
    default = prepare_subset(source, inventory, out)
    assert default["reused_complete_markets"] == 1
    extended = prepare_subset(source, inventory, tmp_path / "extended", presettlement=True)
    assert extended["reused_complete_markets"] == 0
    assert extended["markets_requiring_collection"] == 1


def test_narrower_cohort_is_rejected_or_removes_stale_discovery_members(tmp_path):
    source, inventory, out = fixture_source(tmp_path, ("A", "B"))
    prepare_subset(source, inventory, out)
    original = (out / "discovery_manifest.json").read_bytes()
    write_inventory(inventory, ["A"])
    try:
        manifest = prepare_subset(source, inventory, out)
    except ValueError:
        assert (out / "discovery_manifest.json").read_bytes() == original
    else:
        actual = {m["ticker"] for path in (out / "discovery").glob("*.json.gz")
                  for m in read_json(path).get("markets", [])}
        assert actual == {"A"}
        assert manifest["requested_markets"] == 1


def test_selector_applies_frozen_creation_cutoff_and_named_macro_scope(tmp_path):
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"include_channels": ["election", "policy", "mention"], "macro_series": ["CPI"]}))
    index = tmp_path / "all.csv"
    fields = ["ticker", "channel", "series_ticker", "created_time", "open_time"]
    with index.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows([{"ticker": "PA", "channel": "election", "series_ticker": "PA"},
                          {"ticker": "CPI", "channel": "macro", "series_ticker": "CPI"},
                          {"ticker": "OILCLOCK", "channel": "macro", "series_ticker": "OILCLOCK"},
                          {"ticker": "FUTURE", "channel": "mention", "series_ticker": "TALK", "created_time": "2026-09-08T16:00:00Z"}])
    out = tmp_path / "cohort.csv"
    manifest = select(index, scope, out, "2026-09-08T15:07:00Z")
    with out.open(encoding="utf-8-sig") as handle:
        assert {r["ticker"] for r in csv.DictReader(handle)} == {"PA", "CPI"}
    assert manifest["research_cohort_markets"] == 2
    assert manifest["inventory_sha256"]
    assert manifest["scope_sha256"]
