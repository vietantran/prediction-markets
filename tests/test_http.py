import httpx
import pytest

from pmresearch.http import APIError, HttpClient


def test_retry_refreshes_auth_and_records_raw_without_headers(monkeypatch):
    monkeypatch.setattr("pmresearch.http.time.sleep", lambda _: None)
    calls = []
    def signer(method, url):
        calls.append((method, url))
        return {"Secret": "hidden", "Nonce": str(len(calls))}
    def handler(request):
        assert request.url.params["active"] == "true"
        assert "missing" not in request.url.params
        assert request.headers["nonce"] == str(len(calls))
        return httpx.Response(429 if len(calls) == 1 else 200, json={"ok": True}, headers={"Retry-After": "0"})
    with HttpClient("https://example.test/v2", signer=signer, transport=httpx.MockTransport(handler)) as client:
        assert client.get("/markets", {"active": True, "missing": None}, authenticated=True) == {"ok": True}
        assert len(calls) == 2
        assert len(client.records) == 2
        assert "hidden" not in str(client.records)


def test_terminal_error_and_invalid_paths():
    with HttpClient("https://example.test", transport=httpx.MockTransport(lambda _: httpx.Response(404, json={"error": "missing"}))) as client:
        with pytest.raises(APIError, match="404"):
            client.get("/missing")
        with pytest.raises(ValueError):
            client.get("https://other.test")
        with pytest.raises(APIError, match="credentials"):
            client.get("/private", authenticated=True)
