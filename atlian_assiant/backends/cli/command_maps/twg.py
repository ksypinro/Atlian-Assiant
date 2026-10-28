"""Teamwork Graph CLI (``twg``) command map, checked against ``twg 1.3.3 --help`` and live output on a Cloud site (VER-04, DCR-006).

Scope (the user's decision): twg serves only what the other ways of working do not cover well - Confluence reads, Rovo search and Teamwork Graph
context. Jira and JSM go through REST / acli / MCP. Live facts:
- Every command takes ``-o json --output-summary none`` after the subcommand and prints an envelope ``{apiVersion, command, request, data, meta}``;
  ``CommandMap(envelope="data")`` unwraps it.
- ``confluence content get --detail full --format html`` returns the MCP-v2 shape (``body: {format, value}``, version under ``metadata``);
  ``wrap="v2content"`` turns it into the REST v2 shape the shared normalizer reads.
- ``confluence content list`` requires ``--space``, so page listing stays on REST; ``confluence search query`` returns the REST v1 search body (fetched up to 100 hits, paged
  locally); ``confluence space list|get`` return REST v2 spaces.
- ``rovo search`` and ``context get`` are marked [Enriched] and consume Rovo credits; their output was not captured live, so their adapters accept
  the documented field names and common variants.
"""
from __future__ import annotations

from ..commandmap import Command as C
from ..commandmap import CommandMap

_O = ("-o", "json", "--output-summary", "none")

TWG_MAP = CommandMap("twg", [
    # ---- Confluence read
    C("confluence.content.get", ("confluence", "content", "get", "{content_id}", "--detail", "full", "--format", "html", *_O), path="/pages/{content_id}",
      surface="conf_v2", wrap="v2content", requires=("--detail", "--format"), fixed=(("body-format", "storage"),)),
    C("confluence.space.list", ("confluence", "space", "list", ("--keys", "{keys}"), ("--type", "{type}"), "--limit", "250", *_O), path="/spaces",
      surface="conf_v2", wrap="link:results", requires=("--limit",)),
    C("confluence.space.get", ("confluence", "space", "get", "{key_or_id}", *_O), path="/spaces/{key_or_id}", surface="conf_v2"),
    C("confluence.search.cql", ("confluence", "search", "query", "--cql", "{cql}", "--limit", "100", "--excerpt", *_O), path="/search", surface="conf_v1",
      wrap="cql:results", requires=("--cql", "--limit")),
    # ---- Teamwork Graph / Rovo (consume credits; gated by PolicyGuard)
    C("graph.context", ("context", "get", "{ari}", *_O), "POST", "/graph/context", surface="jira", wrap="twgcontext",
      jvars=(("ari", "ariOrUrl"), ("max_items", "maxItems"))),
    C("graph.search.semantic", ("rovo", "search", "{query}", ("--app", "{products}"), ("--limit", "{limit}"), *_O), "POST", "/graph/search", surface="jira",
      wrap="twgsearch", requires=("--app", "--limit"), jvars=(("query", "query"), ("products", "products[]"), ("limit", "limit"))),
], envelope="data")
