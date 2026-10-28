"""A tiny in-memory OpenSearch for tests: just the REST calls ``OpenSearchMirrorStore`` makes (index, doc get/put/update, filtered + sorted search, simple_query_string)."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

import httpx


class FakeOpenSearch:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str]] = []
        self.index_created = False

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    @staticmethod
    def _match(doc: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
        return all(doc.get(next(iter(f["term"]))) == next(iter(f["term"].values())) for f in filters)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        self.requests.append((request.method, path))
        m = re.match(r"^/([^/]+)(/.*)?$", path)
        rest = (m.group(2) or "") if m else ""
        body = json.loads(request.content) if request.content else {}
        if request.method == "PUT" and not rest:
            if self.index_created:
                return httpx.Response(400, json={"error": "resource_already_exists_exception"})
            self.index_created = True
            return httpx.Response(200, json={"acknowledged": True})
        if rest.startswith("/_doc/"):
            ident = rest.removeprefix("/_doc/")
            if request.method == "GET":
                return httpx.Response(200, json={"_source": self.docs[ident]}) if ident in self.docs else httpx.Response(404, json={"found": False})
            self.docs[ident] = body
            return httpx.Response(201, json={"result": "created"})
        if rest.startswith("/_update/"):
            ident = rest.removeprefix("/_update/")
            if ident not in self.docs:
                return httpx.Response(404, json={"error": "document_missing_exception"})
            self.docs[ident].update(body["doc"])
            return httpx.Response(200, json={"result": "updated"})
        if rest == "/_search":
            bool_q = body["query"]["bool"]
            hits = [d for d in self.docs.values() if self._match(d, bool_q.get("filter", []))]
            if "must" in bool_q:
                terms = re.findall(r"\w+", bool_q["must"][0]["simple_query_string"]["query"].lower())
                hits = [d for d in hits if all(t in json.dumps(d["document"]).lower() for t in terms)]
            hits.sort(key=lambda d: d["id"])
            after = body.get("search_after")
            if after:
                hits = [d for d in hits if d["id"] > after[0]]
            return httpx.Response(200, json={"hits": {"hits": [{"_source": d} for d in hits[: body.get("size", 10)]]}})
        return httpx.Response(404, json={"error": f"unsupported {request.method} {path}"})
