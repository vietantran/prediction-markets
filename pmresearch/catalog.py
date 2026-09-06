"""Refreshable inventory of official API schemas, including nonresearch operations."""
from __future__ import annotations

import hashlib

import httpx
import yaml

from .http import utc_now

SPECS = {
    "polymarket-gamma": "https://docs.polymarket.com/api-spec/gamma-openapi.yaml",
    "polymarket-clob": "https://docs.polymarket.com/api-spec/clob-openapi.yaml",
    "polymarket-data": "https://docs.polymarket.com/api-spec/data-openapi.yaml",
    "polymarket-bridge": "https://docs.polymarket.com/api-spec/bridge-openapi.yaml",
    "polymarket-relayer": "https://docs.polymarket.com/api-spec/relayer-openapi.yaml",
    "polymarket-combos": "https://docs.polymarket.com/api-spec/combos-rfq-openapi.yaml",
    "polymarket-perps": "https://docs.polymarket.com/api-spec/perps-openapi.json",
    "kalshi": "https://docs.kalshi.com/openapi.yaml",
}


def build_catalog(apis=None):
    result = {"retrieved_at": utc_now(), "sources": [], "operations": [], "errors": []}
    with httpx.Client(timeout=45, follow_redirects=True) as client:
        for name in apis or SPECS:
            url = SPECS[name]
            try:
                response = client.get(url)
                response.raise_for_status()
                schema = yaml.safe_load(response.text)
                if not isinstance(schema, dict) or "paths" not in schema:
                    raise ValueError("No OpenAPI paths in response")
                result["sources"].append({"api": name, "url": url, "sha256": hashlib.sha256(response.content).hexdigest(),
                                          "version": schema.get("info", {}).get("version"), "servers": schema.get("servers")})
                for path, methods in schema["paths"].items():
                    for method, operation in methods.items():
                        if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                            continue
                        result["operations"].append({"api": name, "method": method.upper(), "path": path,
                            "summary": operation.get("summary"), "operation_id": operation.get("operationId"),
                            "tags": operation.get("tags"), "security": operation.get("security", schema.get("security", [])),
                            "parameters": methods.get("parameters", []) + operation.get("parameters", []),
                            "deprecated": operation.get("deprecated", False),
                            "research_use": "GET extraction candidate" if method == "get" else "review; most are state-changing"})
            except (httpx.HTTPError, ValueError, yaml.YAMLError) as exc:
                result["errors"].append({"api": name, "url": url, "error": str(exc)})
    return result
