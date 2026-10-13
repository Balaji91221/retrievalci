"""Explicit completeness envelopes and opt-in JSON/HTTP retrieval integration."""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .execution import TransientRetrievalError
from .models import Hit, Snapshot, require


@dataclass(frozen=True)
class RetrievalBatch:
    hits: tuple[Hit, ...]
    snapshot_id: str
    index_complete: bool
    index_id: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class HTTPRetriever:
    """POST query/k/snapshot_id; never uploads corpus text. Only explicit endpoints.

    Provider response: schema_version, snapshot_id, index_complete, index_id, hits.
    hits may reference snapshot evidence IDs or mapped chunk IDs. Bearer tokens are
    loaded from an environment variable inside the worker and never persisted.
    """
    endpoint: str
    version: str
    timeout_s: float = 3.0
    token_env: str | None = None
    name: str = "http-json"

    def __post_init__(self) -> None:
        url = urllib.parse.urlsplit(self.endpoint)
        require(url.scheme == "https" or (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}),
                "HTTP providers require HTTPS except loopback test servers")
        require(bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment,
                "Provider credentials/query parameters must not be embedded in endpoint URLs")
        require(isinstance(self.version, str) and bool(self.version.strip()) and type(self.timeout_s) in {int, float}
                and math.isfinite(self.timeout_s) and 0 < self.timeout_s <= 3600, "Provider version/timeout required")

    @property
    def configuration(self) -> dict[str, Any]:
        return {"endpoint": self.endpoint, "timeout_s": self.timeout_s, "token_env": self.token_env}

    def retrieve(self, query: str, k: int, snapshot: Snapshot) -> RetrievalBatch:
        self.__post_init__()
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token_env:
            token = os.environ.get(self.token_env)
            require(bool(token), "Provider token environment variable is missing")
            headers["Authorization"] = "Bearer " + token
        body = json.dumps({"schema_version": 1, "query": query, "k": k, "snapshot_id": snapshot.id}).encode("utf-8")
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            # Ignore ambient proxy configuration; explicit host only, redirects off.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            with opener.open(request, timeout=self.timeout_s) as response:
                content = response.read(1_000_001)
                require(len(content) <= 1_000_000, "Provider response exceeds 1 MB")
        except urllib.error.HTTPError as exc:
            if exc.code in {408, 429, 500, 502, 503, 504}:
                raise TransientRetrievalError(f"Provider HTTP {exc.code}") from None
            raise ValueError(f"Provider HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TransientRetrievalError("Provider connection failed or timed out") from None
        raw = json.loads(content)
        require(isinstance(raw, dict) and raw.get("schema_version") == 1, "Unsupported provider response")
        require(type(raw.get("index_complete")) is bool and isinstance(raw.get("index_id"), str)
                and bool(raw["index_id"]), "Provider must declare index completeness and identity")
        require(isinstance(raw.get("hits"), list), "Provider hits must be a list")
        hits = []
        for row in raw["hits"]:
            require(isinstance(row, dict), "Invalid provider hit")
            hits.append(Hit(row.get("evidence_id"), row.get("score")))
        return RetrievalBatch(tuple(hits), raw.get("snapshot_id"), raw["index_complete"], raw["index_id"])
