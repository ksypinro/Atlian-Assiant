"""A fake Data Center webhook REST API (Confluence /rest/api/webhooks and Jira /rest/webhooks/1.0/webhook)."""
from __future__ import annotations

import json
import re

import httpx


def dc_http(product: str):
    root = "/rest/api/webhooks" if product == "confluence" else "/rest/webhooks/1.0/webhook"
    state = {"hooks": [], "tests": [], "requests": [], "next": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        state["requests"].append((request.method, path, request.headers.get("authorization")))
        body = json.loads(request.content) if request.content else None
        if path == root and request.method == "GET":
            return httpx.Response(200, json={"results": state["hooks"]} if product == "confluence" else state["hooks"])
        if path == root and request.method == "POST":
            hook = {**body, "id": state["next"]}
            state["next"] += 1
            state["hooks"].append(hook)
            return httpx.Response(200, json=hook)
        if path == root + "/test" and request.method == "POST":
            state["tests"].append(body["url"])
            return httpx.Response(200, json={"statusCode": 200})
        m = re.match(re.escape(root) + r"/(\d+)$", path)
        if m and request.method == "PUT":
            hook = {**body, "id": int(m.group(1))}
            state["hooks"] = [hook if h["id"] == hook["id"] else h for h in state["hooks"]]
            return httpx.Response(200, json=hook)
        if m and request.method == "DELETE":
            state["hooks"] = [h for h in state["hooks"] if str(h["id"]) != m.group(1)]
            return httpx.Response(204)
        return httpx.Response(404, json={"message": "no route"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), state
