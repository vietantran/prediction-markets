"""Small synchronous JSON transport with bounded retries and auditable response traces."""
from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class APIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class PaginationError(APIError):
    pass


def clean_params(params: dict | None) -> dict:
    return {k: (str(v).lower() if isinstance(v, bool) else v)
            for k, v in (params or {}).items() if v is not None}


class HttpClient:
    def __init__(self, base_url: str, *, headers=None, signer=None, timeout=30,
                 retries=4, min_interval=0.15, transport=None, recorder: Callable | None = None):
        self.base_url = base_url.rstrip("/")
        self.signer = signer
        self.retries = retries
        self.min_interval = min_interval
        self.recorder = recorder
        self.records: list[dict] = []
        self._last_request = 0.0
        self._client = httpx.Client(timeout=timeout, transport=transport,
                                   headers={"User-Agent": "pmresearch/0.1 (research)", **(headers or {})})

    def _record(self, record: dict) -> None:
        # Headers are deliberately never logged. Callback streams raw records to disk.
        if self.recorder:
            self.recorder(record)
        else:
            self.records.append(record)

    def request(self, method: str, path: str, params=None, json=None, headers=None,
                authenticated=False) -> Any:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("Research transport supports GET and read-only POST only")
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ValueError("Use an API-relative path starting with one slash")
        url = self.base_url + path
        if authenticated and self.signer is None:
            raise APIError("This read requires configured API credentials")
        query = clean_params(params)
        for attempt in range(self.retries + 1):
            time.sleep(max(0.0, self.min_interval - (time.monotonic() - self._last_request)))
            request_headers = dict(headers or {})
            if authenticated:
                request_headers.update(self.signer(method, url))
            self._last_request = time.monotonic()
            observed_at = utc_now()
            try:
                response = self._client.request(method, url, params=query, json=json, headers=request_headers)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    self._record({"observed_at": observed_at, "method": method, "url": url,
                                  "params": query, "error": type(exc).__name__, "attempt": attempt + 1})
                    raise APIError(f"Transport failure at {urlsplit(url).netloc}{path}: {type(exc).__name__}") from exc
                time.sleep(min(30, 2 ** attempt + random.uniform(0, 0.25)))
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = {"non_json_response": response.text[:2000]}
            self._record({"observed_at": observed_at, "method": method, "url": url, "params": query,
                          "request_body": json, "status_code": response.status_code,
                          "attempt": attempt + 1, "data": payload})
            if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < self.retries:
                delay = min(30.0, 2 ** attempt + random.uniform(0, 0.25))
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(0.0, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (ValueError, TypeError, OverflowError):
                            pass
                # Do not hammer an API with a long server-requested pause.
                if delay > 60:
                    raise APIError(f"Server requested Retry-After {delay:.0f}s; retry later", status_code=response.status_code)
                time.sleep(delay)
                continue
            if not response.is_success:
                raise APIError(f"HTTP {response.status_code} on {method} {urlsplit(url).netloc}{path}; "
                               "see raw response for details", status_code=response.status_code)
            if isinstance(payload, dict) and "non_json_response" in payload:
                raise APIError(f"Expected JSON from {urlsplit(url).netloc}{path}", status_code=response.status_code)
            return payload
        raise APIError("Retry loop exhausted")

    def get(self, path, params=None, **kwargs):
        return self.request("GET", path, params=params, **kwargs)

    def post(self, path, json=None, **kwargs):
        return self.request("POST", path, json=json, **kwargs)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
