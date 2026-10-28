"""How to build a context (backend + scenario server) for each matrix column, and which ◐ subset each column declares."""
from __future__ import annotations

from collections.abc import Callable

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.acli import PARTIAL_OPS as ACLI_PARTIAL
from atlian_assiant.backends.acli import AcliBackend
from atlian_assiant.backends.browser import PARTIAL_OPS as BROWSER_PARTIAL
from atlian_assiant.backends.data_lake import PARTIAL_OPS as LAKE_PARTIAL
from atlian_assiant.backends.graphql import PARTIAL_OPS as GRAPHQL_PARTIAL
from atlian_assiant.backends.jira_cli import PARTIAL_OPS as JIRA_CLI_PARTIAL
from atlian_assiant.backends.jira_cli import JiraCliBackend
from atlian_assiant.backends.twg import PARTIAL_OPS as TWG_PARTIAL
from atlian_assiant.backends.twg import TwgBackend
from atlian_assiant.testing.clisim import FakeCli
from atlian_assiant.testing.server import ScenarioServer

from .matrix_harness import Ctx


def rest_ctx() -> Ctx:
    server = ScenarioServer()
    cfg = AtlassianConfig(site="example.atlassian.net", email="ann@example.com", api_token="secret-token-value")
    return Ctx(ClientFactory().backend(BackendKind.REST, cfg, http=server.client()), server)


def _cli(backend_class: type) -> Callable[[], Ctx]:
    def make() -> Ctx:
        server = ScenarioServer()
        fake = FakeCli(backend_class.cmap, server)
        backend = backend_class(fake.runner())
        ctx = Ctx(backend, server)
        ctx.fake = fake                                                    # type: ignore[attr-defined]
        return ctx
    return make


def graphql_ctx() -> Ctx:
    from atlian_assiant.backends.graphql import GraphQLBackendFactory
    from atlian_assiant.core.services import CoreServices
    from atlian_assiant.testing.gqlsim import FakeGraphQL

    server = ScenarioServer()
    fake = FakeGraphQL(server)
    cfg = AtlassianConfig(site="example.atlassian.net", email="ann@example.com", api_token="secret-token-value", cloud_id="cid-1")
    ctx = Ctx(GraphQLBackendFactory().create(cfg, CoreServices.create(cfg, fake.client())), server)
    ctx.fake = fake                                                      # type: ignore[attr-defined]
    return ctx


def browser_ctx() -> Ctx:
    from atlian_assiant.backends.browser import BrowserBackend
    from atlian_assiant.testing.browsersim import FakeBrowserSession

    server = ScenarioServer()
    session = FakeBrowserSession(server)
    ctx = Ctx(BrowserBackend(session, site="example.atlassian.net", allow_writes=True), server)
    ctx.fake = session                                                   # type: ignore[attr-defined]
    return ctx


def lake_ctx() -> Ctx:
    from atlian_assiant.backends.data_lake import DataLakeBackend
    from atlian_assiant.backends.data_lake.engine import SqliteEngine
    from atlian_assiant.testing.lakesim import fake_share

    share = fake_share()
    ctx = Ctx(DataLakeBackend(share, engine=SqliteEngine()), None)
    ctx.fake = share                                                     # type: ignore[attr-defined]
    return ctx


def mcp_ctx() -> Ctx:
    from .mcp_helpers import make_mcp

    backend, transport = make_mcp()
    return Ctx(backend, transport)


#: backend -> (context factory, ◐ subset per row, rows proven here or None for all of its cells). Cloud REST / MCP rows other than the exports have their own suites.
BACKENDS: dict[str, tuple[Callable[[], Ctx], dict[str, tuple[str, ...]], frozenset[str] | None]] = {
    "rest": (rest_ctx, {"export-jira-csv": ("export.jira.csv",), "export-conf-page": ("export.confluence.page",)}, frozenset({"export-jira-csv", "export-conf-page"})),
    "mcp": (mcp_ctx, {}, frozenset({"export-conf-page"})),
    "graphql": (graphql_ctx, GRAPHQL_PARTIAL, None),
    "browser": (browser_ctx, BROWSER_PARTIAL, None),
    "data_lake": (lake_ctx, LAKE_PARTIAL, None),
    "acli": (_cli(AcliBackend), ACLI_PARTIAL, None),
    "twg": (_cli(TwgBackend), TWG_PARTIAL, None),
    "jira_cli": (_cli(JiraCliBackend), JIRA_CLI_PARTIAL, None),
}
