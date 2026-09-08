import gzip
import json

import pytest

from pmresearch.issue_research import (
    CLASSIFICATION_VERSION, DAY, analyze, classify, inventory, inventory_fingerprints,
    inventory_is_current, iso, load_candles, load_config,
    mention_summaries, normalize_word, quote, select_quote,
)


@pytest.fixture
def cfg():
    return load_config()


def test_mentions_use_target_not_generic_boilerplate(cfg):
    series = {"ticker": "KXTRUMPMENTION", "title": "What will Trump say about inflation?", "category": "Mentions", "tags": ["Politicians"]}
    m = {"ticker": "T", "title": "What will Trump say about inflation?", "yes_sub_title": "Golf", "rules_primary": "Generic inflation and tariffs wording"}
    assert classify(m, series, cfg)[0] is None
    m["custom_strike"] = {"Word": "Tariff / Tariffs"}
    tag, reason = classify(m, series, cfg)
    assert reason is None
    assert tag["issue_ids"] == "trade_tariffs"
    assert tag["classification_field"] == "custom_strike.Word"
    assert "inflation" not in tag["classification_text"]


def test_election_scope_and_foreign_macro(cfg):
    series = {"ticker": "GOVPARTYMI", "category": "Elections", "title": "Michigan Governor"}
    assert classify({"ticker": "GOVPARTYMI-26-D", "rules_primary": "2026 election"}, series, cfg)[0]["channel"] == "election"
    assert classify({"ticker": "GOVPARTYMI-28-D", "rules_primary": "2028 election"}, series, cfg)[0] is None
    assert classify({"ticker": "KXCPIEU-X", "title": "Euro area inflation"}, {"ticker": "KXCPIEU", "category": "Economics"}, cfg)[1] == "foreign_only_macro_or_policy"
    assert classify({"ticker": "HOUSELENGTH-26", "title": "US house average sale length 2026"}, {"ticker": "HOUSELENGTH", "category": "Economics"}, cfg)[0] is None


def test_verified_post_midterm_control_contracts_are_not_generic_2027_elections(cfg):
    series = {"ticker": "KXBALANCEPOWERCOMBO", "category": "Elections", "title": "Congress balance of power combo"}
    common = {"event_ticker": "KXBALANCEPOWERCOMBO-27FEB", "title": "House and Senate control for Feb 2027?",
              "rules_primary": "If ALL of the following occur: House Control: Republican, Senate Control: Republican, then the market resolves to Yes."}
    for suffix in ["RR", "RD", "DR", "DD"]:
        tag, reason = classify(dict(common, ticker="KXBALANCEPOWERCOMBO-27FEB-" + suffix), series, cfg)
        assert reason is None
        assert tag["channel"] == "election"
        assert tag["scope"] == "US_post_2026_organizational_control_Feb2027_not_election_win"
    assert classify(dict(common, ticker="KXBALANCEPOWERCOMBO-29FEB-RR"), series, cfg)[0] is None


def test_inventory_cache_invalidates_on_taxonomy_or_census_change(cfg, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    manifest_path = source / "discovery_manifest.json"
    manifest_path.write_text('{"status":"complete","markets":1}')
    selection = {"status": "offline_inventory_complete", "classification_version": CLASSIFICATION_VERSION,
                 "asof_ts": 100, "source_root": str(source.resolve()), **inventory_fingerprints(source, cfg)}
    assert inventory_is_current(selection, source, cfg, 100)
    changed_cfg = dict(cfg, min_events_per_period=cfg["min_events_per_period"] + 1)
    assert not inventory_is_current(selection, source, changed_cfg, 100)
    manifest_path.write_text('{"status":"complete","markets":2}')
    assert not inventory_is_current(selection, source, cfg, 100)
    manifest_path.write_text('{"status":"complete","markets":1}')
    assert inventory_is_current(selection, source, cfg, 100)
    assert not inventory_is_current(selection, source, cfg, 101)
    assert not inventory_is_current(dict(selection, status="partial_metadata_errors"), source, cfg, 100)


def test_price_suffix_precedence_historical_dollars_and_bad_quotes(cfg):
    q = quote({"yes_bid": {"close_dollars": "0.42"}, "yes_ask": {"close_dollars": "0.44"}, "_price_unit": "cents"}, 1, "live", cfg)
    assert q["mid_probability"] == pytest.approx(.43)
    q = quote({"yes_bid": {"close": "0.42"}, "yes_ask": {"close": "0.44"}}, 1, "historical", cfg)
    assert q["mid_probability"] == pytest.approx(.43)
    q = quote({"yes_bid": {"close": 42}, "yes_ask": {"close": 44}}, 1, "live", cfg)
    assert q["mid_probability"] == pytest.approx(.43)
    assert not quote({"yes_bid": {"close_dollars": "0.9"}, "yes_ask": {"close_dollars": "0.1"}}, 1, "live", cfg)["usable_quote"]
    assert quote({"yes_bid": {"close_dollars": "0"}, "yes_ask": {"close_dollars": "0.02"}}, 1, "live", cfg)["usable_quote"]


def test_quote_selection_lead_staleness_and_settlement(cfg):
    r = {"end_ts": 100, "usable_quote": True, "spread_probability": .02, "mid_probability": .5,
         "bid_usd": .49, "ask_usd": .51, "open_interest_contracts": 20}
    assert select_quote([r], 100, cfg)["quote_status"] == "usable"
    assert select_quote([r], 99, cfg)["quote_status"] == "missing_usable_quote"
    assert select_quote([r], 100 + 2 * DAY, cfg)["quote_status"] == "stale_observation"
    assert select_quote([r], 100, cfg, cutoff=100)["quote_status"] == "missing_usable_quote"
    assert select_quote([r], 100, dict(cfg, max_spread_probability=.01))["quote_status"] == "missing_usable_quote"


def test_matched_cohorts_are_event_deduplicated_and_not_new_words(cfg):
    cfg = dict(cfg, min_events_per_period=2, min_matched_cohorts=1)
    observations = []
    for period, value in [("prior28d", .3), ("latest14d", .6)]:
        for event in range(2):
            for duplicate in range(3):
                observations.append({"issue_ids": "trade_tariffs", "period": period, "quote_status": "usable", "series_ticker": "TRUMP",
                    "subject": "donald trump", "word_normalized": "tariff", "event_ticker": period + str(event), "mid_probability": value})
    observations.append(dict(observations[-1], word_normalized="trade deal", mid_probability=.99))
    cohorts, summaries = mention_summaries(observations, cfg)
    matched = [r for r in cohorts if r["matched_supported"]]
    assert len(matched) == 1
    assert matched[0]["latest14d_events"] == 2
    assert matched[0]["change_pp"] == pytest.approx(30)
    summary = next(r for r in summaries if r["issue_id"] == "trade_tariffs")
    assert summary["matched_change_pp"] == pytest.approx(30)
    assert normalize_word("AI / Jobs") != normalize_word("AI")


def write_gz(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f)


def test_inventory_asof_anchor_and_full_offtopic_outcome(cfg, tmp_path):
    source, out = tmp_path / "source", tmp_path / "out"
    source.mkdir()
    out.mkdir()
    (source / "run_config.json").write_text(json.dumps({"asof_ts": 20 * DAY}))
    common = {"event_ticker": "S-E", "created_time": iso(DAY), "open_time": iso(DAY), "title": "What will Trump say?"}
    ms = [dict(common, ticker="S-E-A", close_time=iso(8 * DAY), yes_sub_title="Inflation"),
          dict(common, ticker="S-E-B", close_time=iso(7 * DAY), yes_sub_title="Golf"),
          dict(common, ticker="S-E-C", created_time=iso(21 * DAY), yes_sub_title="Jobs")]
    write_gz(source / "discovery" / "S.json.gz", {"series": {"ticker": "S", "category": "Mentions", "tags": ["Politicians"]}, "markets": ms})
    markets, manifest = inventory(source, out, cfg)
    assert len(markets) == 1
    assert markets[0]["event_anchor_ts"] == 7 * DAY
    assert manifest["counts"]["excluded:created_after_frozen_asof"] == 1
    assert (out / "excluded_markets.csv").exists()


def test_candle_merge_does_not_use_future_or_settled_prices(cfg, tmp_path):
    m = {"ticker": "X", "face_usd": 1, "source_tier": "live", "settlement_ts": iso(300), "open_time": iso(50)}
    def row(t, p):
        return {"end_period_ts": t, "yes_bid": {"close_dollars": str(p)}, "yes_ask": {"close_dollars": str(p + .02)}, "volume_fp": "0", "open_interest_fp": "0"}
    write_gz(tmp_path / "candles" / "X.json.gz", {"status": "complete", "hourly": [row(100, .4), row(200, .5), row(300, 0), row(400, 0)]})
    rows, _, _ = load_candles(m, [tmp_path], 250, cfg)
    assert [r["end_ts"] for r in rows] == [100, 200]
    assert rows[0]["volume_contracts"] == 0
    assert rows[0]["open_interest_contracts"] == 0


def test_presettlement_extensions_require_explicit_opt_in(cfg, tmp_path):
    m = {"ticker": "X", "face_usd": 1, "source_tier": "live", "open_time": iso(50)}
    def row(t):
        return {"end_period_ts": t, "yes_bid": {"close_dollars": ".4"},
                "yes_ask": {"close_dollars": ".42"}}
    write_gz(tmp_path / "candles" / "X.json.gz", {"status": "complete", "hourly": [row(200)]})
    write_gz(tmp_path / "candles_presettlement" / "X.json.gz", {"status": "complete", "hourly": [row(100)]})
    rows, artifacts, errors = load_candles(m, [tmp_path], 250, cfg)
    assert [r["end_ts"] for r in rows] == [200]
    assert len(artifacts) == 1
    assert not errors
    rows, artifacts, _ = load_candles(m, [tmp_path], 250, cfg, include_presettlement=True)
    assert [r["end_ts"] for r in rows] == [100, 200]
    assert len(artifacts) == 2
    # An extension alone must not appear to cover the requested common window.
    m["ticker"] = "Y"
    write_gz(tmp_path / "candles_presettlement" / "Y.json.gz", {"status": "complete", "hourly": [row(100)]})
    assert load_candles(m, [tmp_path], 250, cfg) == ([], [], [])


@pytest.mark.parametrize("asof", [4 * DAY + 100, 64 * DAY + 100])
def test_daily_oi_is_last_stock_and_full_calendar_window_is_preserved(cfg, tmp_path, asof):
    source, out = tmp_path / "source", tmp_path / "out"
    out.mkdir()
    m = {"ticker": "X", "series_ticker": "KXCPI", "event_ticker": "X-E", "channel": "macro", "issue_ids": "affordability_inflation",
         "face_usd": 1, "source_tier": "live", "created_time": iso(DAY), "open_time": iso(DAY), "close_time": iso(20 * DAY),
         "settlement_ts": "", "title": "US CPI", "yes_sub_title": "Above", "status": "active", "scope": "US"}
    rows = [{"end_period_ts": t, "yes_bid": {"close_dollars": ".4"}, "yes_ask": {"close_dollars": ".42"},
             "volume_fp": "2", "open_interest_fp": oi} for t, oi in [(3 * DAY - 3600, "10"), (3 * DAY, "12")]]
    write_gz(source / "candles" / "X.json.gz", {"status": "complete", "hourly": rows})
    manifest = analyze([m], source, out, cfg, {"asof_ts": asof, "asof_utc": iso(asof), "hourly_start_ts": DAY}, {"status": "test"})
    assert manifest["common_window_start_utc"] == iso(DAY)
    assert manifest["presettlement_extensions_included"] is False
    import csv
    daily = list(csv.DictReader((out / "daily_issue_series.csv").open(encoding="utf-8-sig")))
    assert len(daily) == 2
    assert daily[0]["utc_date"] == "1970-01-02"
    assert daily[0]["newly_listed_events"] == "1"
    assert daily[0]["volume_observed_contracts"] == ""
    assert daily[1]["utc_date"] == "1970-01-03"
    assert float(daily[1]["oi_sum_latest_observed_per_contract"]) == 12
    assert float(daily[1]["volume_observed_contracts"]) == 4
