import base64
from decimal import Decimal

import pytest

from pmresearch.http import HttpClient
from pmresearch.kalshi import Kalshi, KalshiSigner, orderbook_to_yes_quotes


class FakeClient:
    def __init__(self, *pages):
        self.pages = iter(pages)
        self.calls = []

    def get(self, path, params=None, **kwargs):
        self.calls.append((path, params, kwargs))
        return next(self.pages)


def test_cursor_walk_retains_filters_and_uses_event_limit():
    fake = FakeClient({"events": [{"event_ticker": "ONE"}], "cursor": "next"},
                      {"events": [{"event_ticker": "TWO"}], "cursor": ""})
    rows = list(Kalshi(fake).iter_events(series_ticker="S", with_nested_markets=True))
    assert [r["event_ticker"] for r in rows] == ["ONE", "TWO"]
    assert fake.calls[1][1] == {"series_ticker": "S", "with_nested_markets": "true", "limit": 200, "cursor": "next"}
    assert fake.calls[0][2]["authenticated"] is False


def test_empty_page_with_cursor_does_not_skip_later_results():
    fake = FakeClient({"trades": [], "cursor": "x"}, {"trades": [{"trade_id": "t"}], "cursor": ""})
    assert list(Kalshi(fake).iter_trades()) == [{"trade_id": "t"}]


def test_repeated_cursor_raises_before_duplicate_page_is_yielded():
    fake = FakeClient({"markets": [{"ticker": "A"}], "cursor": "x"},
                      {"markets": [{"ticker": "A"}], "cursor": "x"})
    rows = Kalshi(fake).iter_markets()
    assert next(rows) == {"ticker": "A"}
    with pytest.raises(RuntimeError, match="repeated"):
        next(rows)


def test_bounded_pagination_warns_only_when_more_data_exists():
    with pytest.warns(RuntimeWarning, match="incomplete"):
        assert list(Kalshi(FakeClient({"markets": [], "cursor": "next"})).iter_markets(max_pages=1)) == []
    assert list(Kalshi(FakeClient({"markets": [], "cursor": ""})).iter_markets(max_pages=1)) == []


def test_schema_change_is_not_silently_an_empty_dataset():
    with pytest.raises(ValueError, match="missing expected field"):
        list(Kalshi(FakeClient({"renamed_markets": []})).iter_markets())


def test_incentives_use_next_cursor_and_correct_collection_name():
    fake = FakeClient({"incentive_programs": [{"id": "a"}], "next_cursor": "x"},
                      {"incentive_programs": [], "next_cursor": ""})
    assert list(Kalshi(fake).iter_incentive_programs()) == [{"id": "a"}]
    assert fake.calls[1][1]["cursor"] == "x"


def test_orderbook_uses_complement_and_retains_subcent_fractional_precision():
    book = {"orderbook_fp": {"yes_dollars": [["0.3000", "1.55"], ["0.4025", "2.01"]],
                             "no_dollars": [["0.5900", "0.25"], ["0.4000", "3.00"]]}}
    result = orderbook_to_yes_quotes(book)
    assert result["best_bid"] == "0.4025"
    assert result["best_ask"] == "0.4100"
    assert result["midpoint"] == "0.40625"
    assert result["yes_asks"][0] == {"price": "0.4100", "size": "0.25"}
    assert result["spread"] == "0.0075"
    assert result["crossed"] is False


def test_orderbook_empty_side_has_no_midpoint_or_invented_ask():
    result = orderbook_to_yes_quotes({"orderbook_fp": {"yes_dollars": [["0.3", "1"]], "no_dollars": None}})
    assert result["best_ask"] is None
    assert result["midpoint"] is None
    assert result["yes_asks"] == []


def test_orderbook_legacy_cents_and_bad_values():
    result = orderbook_to_yes_quotes({"orderbook": {"yes": [[40, 5]], "no": [[55, 2]]}})
    assert Decimal(result["best_ask"]) == Decimal("0.45")
    with pytest.raises(ValueError):
        orderbook_to_yes_quotes({"yes_dollars": [["NaN", "1"]]})
    with pytest.raises(ValueError):
        orderbook_to_yes_quotes({"no_dollars": [["1.1", "1"]]})


def test_batch_encoding_uses_repeated_books_but_comma_candles():
    import httpx

    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    with Kalshi(client=HttpClient("https://example.test/trade-api/v2", min_interval=0,
                                 transport=httpx.MockTransport(handler))) as api:
        api.get_orderbooks(["A", "B"])
        api.get_batch_candlesticks(["A", "B"], 0, 3600)
        api.get_forecast_percentile_history("S", "E", [1000, 5000, 9000], 0, 3600)
    assert requests[0].url.params.get_list("tickers") == ["A", "B"]
    assert requests[1].url.params["market_tickers"] == "A,B"
    assert requests[2].url.params.get_list("percentiles") == ["1000", "5000", "9000"]


def test_batch_limit_and_oversized_time_range_are_rejected_before_request():
    api = Kalshi(FakeClient())
    with pytest.raises(ValueError, match="1–100"):
        api.get_orderbooks(["T"] * 101)
    with pytest.raises(ValueError, match="10,000"):
        api.get_batch_candlesticks(["T"] * 100, 0, 86_400, period_interval=1)
    with pytest.raises(ValueError, match="period_interval"):
        api.get_candlesticks("S", "M", 0, 60, period_interval=5)


def test_history_routes_and_candle_windows_do_not_overlap():
    fake = FakeClient({"candlesticks": [{"end_period_ts": 60}]},
                      {"candlesticks": [{"end_period_ts": 120}]},
                      {"candlesticks": []})
    rows = list(Kalshi(fake).iter_candlesticks("S", "M", 0, 120, 1,
                                            historical=True, bars_per_window=1))
    assert rows == [{"end_period_ts": 60}, {"end_period_ts": 120}]
    assert all(call[0] == "/historical/markets/M/candlesticks" for call in fake.calls)
    assert [(c[1]["start_ts"], c[1]["end_ts"]) for c in fake.calls] == [(0, 59), (60, 119), (120, 120)]


def test_portfolio_requires_auth_and_full_positions_page_preserves_both_types():
    raw = {"market_positions": [{"position_fp": "1.55"}], "event_positions": [{"event_ticker": "E"}]}
    fake = FakeClient(raw)
    assert Kalshi(fake).get_positions() == raw
    assert fake.calls[0][2]["authenticated"] is True


def test_signer_signature_matches_official_rsa_pss_digest_and_query_exclusion(monkeypatch):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr("pmresearch.kalshi.time.time_ns", lambda: 1_700_000_000_123_000_000)
    headers = KalshiSigner("test-key", key)("get", "https://external-api.kalshi.com/trade-api/v2/portfolio/orders?limit=5")
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1700000000123"
    key.public_key().verify(
        base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]),
        b"1700000000123GET/trade-api/v2/portfolio/orders",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256(),
    )


def test_public_constructor_does_not_read_environment_keys(monkeypatch):
    monkeypatch.setattr(KalshiSigner, "from_env", lambda: pytest.fail("Unexpected credential read"))
    with Kalshi(min_interval=0):
        pass


@pytest.mark.parametrize("path", ["https://example.com", "//example.com", "/markets?limit=1"])
def test_get_rejects_absolute_and_embedded_query_paths(path):
    with pytest.raises(ValueError):
        Kalshi(FakeClient()).get(path)


def test_collection_fetches_archived_market_details_without_false_failure(tmp_path):
    from pmresearch.http import APIError
    from pmresearch.pipeline import collect
    from pmresearch.storage import RunStore

    class ArchivedApi:
        def get_historical_cutoff(self):
            return {"market_settled_ts": "2026-07-01T00:00:00Z"}

        def get_market(self, ticker):
            raise APIError("Archived", status_code=404)

        def get_historical_market(self, ticker):
            return {"market": {"ticker": ticker, "open_interest_fp": "0.00", "status": "settled"}}

    store = RunStore(tmp_path)
    try:
        results = collect(None, ArchivedApi(), store, [{"platform": "kalshi", "market_id": "OLD"}],
                          datasets=["open_interest"], start=1, end=2)
        assert results[0]["market_details"]["market"]["status"] == "settled"
        assert not store.errors
    finally:
        store.close()


def test_collection_reports_unavailable_kalshi_public_datasets(tmp_path):
    from pmresearch.pipeline import collect
    from pmresearch.storage import RunStore

    store = RunStore(tmp_path)
    try:
        collect(None, Kalshi(FakeClient({})), store, [{"platform": "kalshi", "market_id": "M"}],
                datasets=["holders", "comments"], start=1, end=2)
        assert any("comments, holders" in warning for warning in store.warnings)
    finally:
        store.close()


def test_midterm_discovery_admits_undated_series_but_verifies_event_cycle(tmp_path):
    from pmresearch.pipeline import discover
    from pmresearch.storage import RunStore
    from pmresearch.topics import TopicClassifier

    class ElectionApi:
        requested = []

        def get_series_list(self, **params):
            assert params["include_volume"] is True
            return {"series": [
                {"ticker": "LEGACY", "title": "US Senate Control", "category": "Politics", "volume_fp": "0.00"},
                {"ticker": "RACES", "title": "House Race Winner?", "category": "Elections", "tags": ["US Elections"], "volume_fp": "1000.00"},
                {"ticker": "S", "title": "Senate winner", "category": "Elections", "tags": ["US Elections"], "volume_fp": "2000.00"},
                {"ticker": "H", "title": "House winner", "category": "Elections", "tags": ["US Elections"], "volume_fp": "3000.00"},
            ]}

        def iter_events(self, series_ticker, **params):
            self.requested.append(series_ticker)
            years = [2026, 2028] if series_ticker == "RACES" else [2026]
            for year in years:
                if series_ticker == "RACES" and year == 2026:
                    yield {"event_ticker": "RACES-IA02-26", "title": "IA-02 House winner?", "markets": [
                        {"ticker": "RACES-IA02-26-D", "title": "Will Democratic win the House race for IA-02?",
                         "status": "active", "rules_primary":
                         "If the House member sworn in for IA-02 for the term beginning in 2027 is a member of the Democratic Party, then the market resolves to Yes."}]}
                    continue
                yield {"event_ticker": f"{series_ticker}-{year}", "title": f"US House election {year}",
                       "markets": [{"ticker": f"{series_ticker}-{year}-D", "status": "active",
                                    "title": f"Will Democrats win the House election in {year}?"}]}

    store = RunStore(tmp_path)
    api = ElectionApi()
    try:
        rows, coverage = discover(None, api, TopicClassifier(), store, platform="kalshi",
                                  topics=["midterms_2026"], max_series=3)
        assert set(api.requested[:2]) == {"H", "S"}
        assert api.requested[2] == "RACES"
        assert len(rows) == 3
        assert all("2028" not in row["title"] for row in rows)
        assert any(row["market_id"] == "RACES-IA02-26-D" and "2026" not in row["title"] for row in rows)
        assert coverage["unclassified_markets"] == 1
        assert coverage["kalshi_selected_series_tickers"] == api.requested
    finally:
        store.close()


def test_bls_source_does_not_fabricate_federal_reserve_exposure():
    from pmresearch.pipeline import _series_context
    from pmresearch.topics import TopicClassifier

    context = _series_context({"title": "CPI inflation", "settlement_sources": [
        {"name": "Bureau of Labor Statistics", "url": "https://www.bls.gov/cpi/"}]})
    assert "Federal Reserve" not in context
    classification = TopicClassifier().classify(context)
    assert "us_macro" in classification["topics"]
    assert "fed_monetary_policy" not in classification["topics"]


def test_race_priority_uses_narrow_quotes_not_large_unquoted_volume():
    from pmresearch.pipeline import _kalshi_event_priority

    close_race = {"markets": [{"yes_bid_dollars": "0.48", "yes_ask_dollars": "0.52", "volume_24h_fp": "1.00"}]}
    clear_race = {"markets": [{"yes_bid_dollars": "0.9", "yes_ask_dollars": "0.95", "volume_24h_fp": "10000.00"}]}
    empty_race = {"markets": [{"yes_bid_dollars": "0.49", "yes_ask_dollars": "0.51", "yes_bid_size_fp": "0.00"}]}
    assert _kalshi_event_priority(close_race) < _kalshi_event_priority(clear_race) < _kalshi_event_priority(empty_race)


def test_midterm_cycle_inference_requires_ticker_and_congressional_term_rule():
    from pmresearch.pipeline import _kalshi_cycle_evidence

    event = {"event_ticker": "KXHOUSERACE-IA02-26", "markets": [{"rules_primary":
        "If the House member sworn in for IA-02 for the term beginning in 2027 is a member of the Democratic Party, then the market resolves to Yes."}]}
    assert _kalshi_cycle_evidence(event)["year"] == 2026
    assert _kalshi_cycle_evidence({**event, "event_ticker": "KXHOUSERACE-IA02-28"}) is None
    assert _kalshi_cycle_evidence({**event, "markets": [{"close_time": "2026-11-03T00:00:00Z"}]}) is None
