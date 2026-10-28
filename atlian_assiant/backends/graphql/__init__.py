"""GRAPHQL backend: the Atlassian GraphQL gateway (design 10.4): the generic ``GraphQLExecutor`` plus named, versioned stored queries."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ...capabilities import BackendKind, Deployment
from ...config import AtlassianConfig
from ...core.errors import BackendUnavailableError
from ...core.http import RequestSpec
from ...core.services import CoreServices
from ...factory import Availability
from ...matrix import declared_operations
from ...protocols.base import BackendHealth
from ..cli.backend import CliJiraWrite
from ..graph_part import GraphPart
from ..optransport import Handler, OpBackend, OpTransport
from ..rest_cloud.agile import AgileRest
from ..rest_cloud.confluence import ConfluenceRest
from ..rest_cloud.confluence_write import ConfluenceWriteRest
from ..rest_cloud.jira import JiraRest
from ..rest_cloud.jsm import JsmRest
from .client import GraphQLClient, operation_type
from .documents import Document, load_documents
from .ops import BINDINGS, Binding

EXECUTE_OP = "graph.graphql.execute"

#: ◐ rows: the subset served by stored queries (design section 11); every other operation falls back to REST / MCP
PARTIAL_OPS: dict[str, tuple[str, ...]] = {
    "jira-issue-get": ("jira.issue.get",),
    "jira-issue-search": ("jira.issue.search", "jira.issue.count"),
    "jira-comments-list": ("jira.issue.comments.list",),
    "jira-worklogs": ("jira.issue.worklogs.list",),
    "jira-projects": ("jira.project.list", "jira.project.get"),
    "jira-users": ("jira.user.myself",),
    "conf-content-get": ("confluence.content.get",),                  # spaces: the gateway has no lookup by key
    "conf-cql-search": ("confluence.search.cql",),
    "conf-tree": ("confluence.content.ancestors",),
    "conf-comments-attachments-labels": ("confluence.comment.list", "confluence.label.list"),
}


class GraphQLExecutorPart:
    """``GraphQLExecutor``: arbitrary documents. READ access, so mutations are refused unless explicitly allowed (see ``GraphQLClient.execute``)."""

    def __init__(self, client: GraphQLClient) -> None:
        self.client = client

    async def execute(self, query: str, variables: Mapping[str, Any] | None = None, *, experimental_apis: Sequence[str] = (), operation_name: str | None = None) -> dict[str, Any]:
        return await self.client.execute(query, variables, experimental_apis=experimental_apis, operation_name=operation_name)


class GraphQLOpTransport(OpTransport):
    def __init__(self, client: GraphQLClient, *, site: str | None = None, ops: frozenset[str] | None = None) -> None:
        self.gql, self.documents = client, load_documents()
        self.bindings: dict[str, list[Binding]] = {}
        for bnd in BINDINGS:
            if ops is None or bnd.op in ops:
                self.bindings.setdefault(bnd.op, []).append(bnd)
        handlers: dict[str, Handler] = {op: self._handler(op) for op in self.bindings}
        super().__init__("graphql", handlers, site=site, hint="The GraphQL backend serves a subset through stored queries; the router falls back to REST or MCP.")

    def _handler(self, op: str) -> Handler:
        async def handle(spec: RequestSpec) -> Any:
            for bnd in self.bindings[op]:
                caps = bnd.captures(spec)
                if caps is None:
                    continue
                doc = self.documents[bnd.document]
                variables = bnd.variables(spec, caps, await self.gql.cloud_id())
                data = await self.gql.run(doc.text, variables, experimental_apis=doc.experimental, operation=op, operation_name=doc.name)
                return bnd.adapt(data, spec)
            from ...core.errors import UnsupportedOperationError

            raise UnsupportedOperationError(f"{op}: no stored query for {spec.path}", operation=op, backend="graphql", hint="The router falls back to REST or MCP.")
        return handle

    def drop(self, ops: set[str]) -> None:
        for op in ops:
            self.handlers.pop(op, None)
            self.bindings.pop(op, None)


class GraphQLBackend(OpBackend):
    kind: ClassVar[BackendKind] = BackendKind.GRAPHQL
    DIRECT: ClassVar[frozenset[str]] = frozenset({EXECUTE_OP})

    def __init__(self, client: GraphQLClient, *, site: str | None = None) -> None:
        declared = declared_operations(self.kind, PARTIAL_OPS)
        transport = GraphQLOpTransport(client, site=site, ops=declared)
        self.client, self.gql_transport = client, transport
        self.jira, self.agile, self.jsm, self.confluence = JiraRest(transport), AgileRest(transport), JsmRest(transport), ConfluenceRest(transport)
        self.jira_write, self.confluence_write = CliJiraWrite(transport, self.jira), ConfluenceWriteRest(transport, self.confluence)
        self.graph, self.executor = GraphPart(transport), GraphQLExecutorPart(client)
        super().__init__(transport, (self.jira, self.agile, self.jsm, self.confluence, self.jira_write, self.confluence_write, self.graph, self.executor), declared)
        self.invalid: dict[str, str] = {}

    @property
    def warnings(self) -> tuple[str, ...]:
        return ("stored queries use beta gateway fields (X-ExperimentalApi: JiraIssueSearch, JiraProject, confluence-agg-beta) that Atlassian may change",
                "the generic executor refuses mutations (READ access); experimental fields need experimental_apis")

    async def validate_schema(self) -> dict[str, str]:
        """Introspect once; every operation whose document needs a missing or deprecated ``Type.field`` is dropped (design 10.4)."""
        docs = load_documents()
        names = sorted({req.partition(".")[0] for bindings in self.gql_transport.bindings.values() for bnd in bindings for req in docs[bnd.document].requires})
        # only the types the stored queries need (the full live schema is ~30 MB), one __type per request: the gateway rejects more as bad-faith introspection
        schema: dict[str, dict[str, bool]] = {}
        for name in names:
            data = await self.client.run(f'query Introspect {{ __type(name: "{name}") {{ name fields(includeDeprecated: true) {{ name isDeprecated }} }} }}', {},
                                         operation="graph.graphql.execute", operation_name="Introspect")
            t = data.get("__type")
            if t:
                schema[t["name"]] = {f["name"]: bool(f.get("isDeprecated")) for f in (t.get("fields") or [])}
        invalid: dict[str, str] = {}
        for op, bindings in self.gql_transport.bindings.items():
            for bnd in bindings:
                doc: Document = docs[bnd.document]
                for req in doc.requires:
                    typ, _, fld = req.partition(".")
                    if fld not in schema.get(typ, {}):
                        invalid[op] = f"{req} is missing from the live schema"
                    elif schema[typ][fld]:
                        invalid[op] = f"{req} is deprecated in the live schema"
        self.gql_transport.drop(set(invalid))
        self._declared = frozenset(o for o in self._declared if o not in invalid)
        self.invalid = invalid
        return invalid

    async def health(self) -> BackendHealth:
        try:
            invalid = await self.validate_schema()
        except Exception as exc:                                       # health must never raise
            return BackendHealth("unavailable", f"{type(exc).__name__}: {exc}")
        if invalid:
            return BackendHealth("degraded", f"{len(invalid)} stored-query operations unsupported by the live schema: {', '.join(sorted(invalid))}")
        return BackendHealth("ready", f"{len(self._declared)} operations; stored queries validated against the live schema")

    async def aclose(self) -> None:
        await super().aclose()
        await self.client.transport.client.aclose()


class GraphQLBackendFactory:
    kind: ClassVar[BackendKind] = BackendKind.GRAPHQL
    deployments: ClassVar[frozenset[Deployment]] = frozenset({Deployment.CLOUD})
    extra: ClassVar[str | None] = None

    def availability(self, config: AtlassianConfig) -> Availability:
        has_basic = bool(config.email and config.api_token)
        has_oauth = bool(config.oauth_client_id and config.oauth_client_secret and config.oauth_refresh_token)
        if not (has_basic or has_oauth or config.service_account_key):
            return Availability(False, "no Cloud credentials: the GraphQL gateway needs ATLASSIAN_EMAIL + ATLASSIAN_API_TOKEN, OAuth settings or a service-account key")
        if not config.site and not config.cloud_id:
            return Availability(False, "ATLASSIAN_SITE (or ATLASSIAN_CLOUD_ID with OAuth) is not set: GraphQL calls need the cloudId")
        return Availability(True)

    def create(self, config: AtlassianConfig, core: CoreServices) -> GraphQLBackend:
        avail = self.availability(config)
        if not avail.ok:
            raise BackendUnavailableError(f"graphql unavailable: {avail.reason}", hint=avail.reason)
        transport = core.cloud_transport("graphql")
        resolver = transport._cloud_resolver
        assert resolver is not None
        return GraphQLBackend(GraphQLClient(transport, resolver.resolve), site=config.site)


__all__ = ["EXECUTE_OP", "PARTIAL_OPS", "GraphQLBackend", "GraphQLBackendFactory", "GraphQLClient", "GraphQLExecutorPart", "operation_type"]
