import json

import httpx
import pytest

from pmresearch.http import APIError, HttpClient, PaginationError
from pmresearch.polymarket import (
    BridgeAPI, ClobAPI, CombosAPI, DataAPI, GammaAPI, PaginationWarning,
    PolymarketL2Signer, SubgraphAPI,
)


class FakeClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []
        self.records = []

    def get(self, path, params=None, **kwargs):
        self.calls.append(("GET", path, params, kwargs))
        return next(self.responses)

    def post(self, path, json=None, **kwargs):
        self.calls.append(("POST", path, json, kwargs))
        return next(self.responses)


def test_gamma_keyset_uses_opaque_cursor_and_keeps_filters():
    client = FakeClient({"events": [{"id": "1"}], "next_cursor": "a+b/c="},
                        {"events": [{"id": "2"}]})
    rows = list(GammaAPI(client).iter_events(page_size=1, max_pages=3, closed=False, tag_id=[2, 4]))
    assert [row["id"] for row in rows] == ["1", "2"]
    assert client.calls[0][1] == "/events/keyset"
    assert client.calls[1][2] == {"closed": False, "tag_id": [2, 4], "limit": 1, "after_cursor": "a+b/c="}


def test_gamma_repeated_cursor_and_page_are_errors():
    api = GammaAPI(FakeClient({"markets": [{"id": "1"}], "next_cursor": "x"},
                             {"markets": [{"id": "2"}], "next_cursor": "x"}))
    with pytest.raises(PaginationError, match="cursor"):
        list(api.iter_markets(page_size=1))
    api = GammaAPI(FakeClient({"events": [{"id": "1"}], "next_cursor": "x"},
                             {"events": [{"id": "1"}], "next_cursor": "y"}))
    with pytest.raises(PaginationError, match="page"):
        list(api.iter_events(page_size=1))


def test_pagination_cap_is_visible_and_bad_shape_is_not_empty_success():
    api = GammaAPI(FakeClient({"events": [{"id": "1"}], "next_cursor": "x"}))
    with pytest.warns(PaginationWarning, match="max_pages"):
        assert len(list(api.iter_events(max_pages=1))) == 1
    with pytest.raises(PaginationError, match="list"):
        list(GammaAPI(FakeClient({"error": "bad response"})).iter_events())
    with pytest.raises(ValueError, match="offset"):
        list(GammaAPI(FakeClient()).iter_markets(offset=10))
    with pytest.raises(ValueError, match="<= 100"):
        list(GammaAPI(FakeClient()).iter_markets(page_size=101))


def test_search_uses_page_not_offset_and_returns_events():
    client = FakeClient({"events": [{"id": "1"}], "pagination": {"hasMore": True}},
                        {"events": [{"id": "2"}], "pagination": {"hasMore": False}})
    assert len(list(GammaAPI(client).iter_search("data center", limit_per_type=2))) == 2
    assert client.calls[1][2] == {"q": "data center", "page": 2, "limit_per_type": 2}


def test_data_arrays_serialize_csv_gamma_arrays_stay_repeated():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[])
    client = HttpClient("https://example.test", transport=httpx.MockTransport(handle), min_interval=0)
    DataAPI(client).trades(market=["a", "b"], takerOnly=False)
    GammaAPI(client).markets(condition_ids=["a", "b"], closed=False)
    assert requests[0].url.params["market"] == "a,b"
    assert requests[0].url.params["takerOnly"] == "false"
    assert requests[1].url.params.get_list("condition_ids") == ["a", "b"]
    assert requests[1].url.params["closed"] == "false"
    client.close()


def test_data_filter_exclusivity_and_offset_cap():
    with pytest.raises(ValueError, match="mutually exclusive"):
        DataAPI(FakeClient()).trades(market=["a"], eventId=[1])
    client = FakeClient([{"id": str(i)} for i in range(1000)])
    with pytest.warns(PaginationWarning, match="offset cap"):
        assert len(list(DataAPI(client).iter_trades(offset=10000))) == 1000
    assert len(client.calls) == 1


def test_window_bisection_does_not_duplicate_probe_records():
    all_rows = [{"timestamp": 1, "id": str(i)} for i in range(3)] + [
        {"timestamp": 2, "id": str(i)} for i in range(3, 6)]
    calls = []
    def fetch(**p):
        calls.append(p)
        rows = [r for r in all_rows if p["start"] <= r["timestamp"] <= p["end"]]
        return rows[p["offset"]:p["offset"] + p["limit"]]
    # Artificial small offset ceiling reproduces a deep-history split cheaply.
    result = list(DataAPI._windowed(fetch, 1, 2, 2, 2, 2, 20, {}))
    assert result == all_rows
    assert {(p["start"], p["end"]) for p in calls} == {(1, 2), (1, 1), (2, 2)}
    assert all(p["offset"] <= 2 for p in calls)


def test_window_saturation_and_budget_do_not_claim_completeness():
    def fetch(**p):
        return [{"id": str(i)} for i in range(p["offset"], p["offset"] + p["limit"])]
    with pytest.raises(PaginationError, match="second 1"):
        list(DataAPI._windowed(fetch, 1, 1, 2, 2, 2, 10, {}))
    with pytest.warns(PaginationWarning, match="max_requests"):
        assert list(DataAPI._windowed(fetch, 1, 2, 2, 2, 2, 1, {})) == []


def test_history_identifier_and_batch_casing_are_distinct():
    client = FakeClient({"history": []}, {"history": {}})
    api = ClobAPI(client)
    api.price_history("TOKEN", start_ts=10, end_ts=20, fidelity=5)
    api.batch_price_history(["TOKEN"], startTs=10, endTs=20, fidelity=5)
    assert client.calls[0][2] == {"market": "TOKEN", "startTs": 10, "endTs": 20, "fidelity": 5}
    assert client.calls[1][2] == {"markets": ["TOKEN"], "start_ts": 10, "end_ts": 20, "fidelity": 5}
    with pytest.raises(ValueError, match="1..20"):
        api.batch_price_history([str(i) for i in range(21)])


def test_batch_books_are_read_only_posts_and_lte_cursor_terminates():
    client = FakeClient([], {"data": [{"condition_id": "a"}], "next_cursor": "LTE="})
    api = ClobAPI(client)
    api.books(["1", "2"])
    assert client.calls[0][:3] == ("POST", "/books", [{"token_id": "1"}, {"token_id": "2"}])
    assert len(list(api.iter_markets())) == 1
    assert len(client.calls) == 2


def test_no_trading_routes_and_account_reads_require_credentials():
    with pytest.raises(ValueError, match="allowlist"):
        ClobAPI(FakeClient()).get("/balance-allowance/update")
    with pytest.raises(ValueError, match="allowlist"):
        ClobAPI(FakeClient()).get("/order")
    client = HttpClient("https://clob.polymarket.com", min_interval=0,
                        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(APIError, match="credentials"):
        ClobAPI(client).account_trades("0x" + "0" * 40)
    client.close()


def test_l2_signer_signs_path_not_query_refuses_posts_and_refreshes_time():
    signer = PolymarketL2Signer("address", "key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                                "passphrase", clock=lambda: 1000000)
    first = signer("GET", "https://clob.polymarket.com/data/trades?market=a")
    second = signer("GET", "https://clob.polymarket.com/data/trades?market=b")
    assert first == second
    assert first["POLY_TIMESTAMP"] == "1000000"
    assert first["POLY_SIGNATURE"].endswith("=")
    signer.clock = lambda: 1000001
    assert signer("GET", "https://clob.polymarket.com/data/trades") != first
    with pytest.raises(ValueError, match="GET"):
        signer("POST", "https://clob.polymarket.com/order")


def test_bridge_follows_cursor_even_on_empty_or_short_page():
    client = FakeClient({"transactions": [], "nextCursor": "continue"},
                        {"transactions": [{"status": "COMPLETED"}], "nextCursor": None})
    assert len(list(BridgeAPI(client).iter_status("address"))) == 1
    assert client.calls[1][2]["cursor"] == "continue"


def test_combo_market_catalog_uses_cursor_parameter():
    client = FakeClient({"markets": [{"id": "1"}], "next_cursor": "next"},
                        {"markets": [], "next_cursor": None})
    assert len(list(CombosAPI(client).iter_markets())) == 1
    assert client.calls[1][2]["cursor"] == "next"


def test_subgraph_rejects_mutations_and_propagates_graphql_errors():
    pytest.importorskip("graphql")
    client = FakeClient({"errors": [{"message": "unknown field"}]})
    api = SubgraphAPI("https://example.test/subgraph", client=client)
    with pytest.raises(ValueError, match="query operations"):
        api.query("query Read { markets { id } } mutation Write { deleteAll }")
    assert client.calls == []
    with pytest.raises(RuntimeError, match="unknown field"):
        api.query("query Read { markets { id } }")
    assert client.calls[0][1] == "/subgraph"


def test_errors_preserve_raw_response_without_logging_auth_headers():
    def handle(request):
        assert request.headers["POLY_API_KEY"] == "key"
        return httpx.Response(403, json={"error": "unavailable"})
    signer = PolymarketL2Signer("address", "key", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "secret-passphrase")
    client = HttpClient("https://clob.polymarket.com", signer=signer, min_interval=0,
                        transport=httpx.MockTransport(handle))
    with pytest.raises(APIError):
        ClobAPI(client).account_trades("maker")
    serialized = json.dumps(client.records)
    assert "secret-passphrase" not in serialized
    assert "POLY_API_KEY" not in serialized
    assert client.records[0]["data"] == {"error": "unavailable"}
    client.close()
