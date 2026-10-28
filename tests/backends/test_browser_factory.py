"""BROWSER backend: factory availability, health, supported operations (design 10.8, 12.1)."""
import pytest

from atlian_assiant import AtlassianConfig, BackendKind
from atlian_assiant.backends.browser import PARTIAL_OPS, BrowserBackend, BrowserBackendFactory
from atlian_assiant.capabilities import Deployment
from atlian_assiant.core.errors import BackendUnavailableError
from atlian_assiant.factory import default_registry
from atlian_assiant.matrix import declared_operations
from atlian_assiant.testing.browsersim import FakeBrowserSession
from atlian_assiant.testing.server import ScenarioServer


def factory(**kw):
    return BrowserBackendFactory(lambda cfg: FakeBrowserSession(ScenarioServer()), **kw)


def test_registered_for_cloud_and_data_center_with_the_browser_extra():
    f = default_registry().get(BackendKind.BROWSER)
    assert isinstance(f, BrowserBackendFactory) and f.extra == "browser" and f.deployments == frozenset({Deployment.CLOUD, Deployment.DATA_CENTER})


def test_availability_names_the_missing_piece_precisely(tmp_path):
    no_pw = BrowserBackendFactory(has_playwright=lambda: False)
    assert "atlian-assiant[browser]" in no_pw.availability(AtlassianConfig(site="x.atlassian.net", browser_profile_dir=tmp_path)).reason
    f = BrowserBackendFactory(has_playwright=lambda: True)
    assert "BROWSER_PROFILE_DIR" in f.availability(AtlassianConfig(site="x.atlassian.net")).reason and "human" in f.availability(AtlassianConfig(site="x.atlassian.net")).reason
    assert "ATLASSIAN_SITE" in f.availability(AtlassianConfig(browser_profile_dir=tmp_path)).reason
    assert f.availability(AtlassianConfig(site="x.atlassian.net", browser_profile_dir=tmp_path)).ok


def test_create_sends_nothing_and_honours_the_write_opt_in(tmp_path):
    cfg = AtlassianConfig(site="example.atlassian.net", browser_profile_dir=tmp_path)
    ro = factory().create(cfg, core=None)
    assert isinstance(ro, BrowserBackend) and not ro.allow_writes and ro.session.calls == []
    rw = factory().create(AtlassianConfig(site="example.atlassian.net", browser_profile_dir=tmp_path, allow_browser_writes=True), core=None)
    assert rw.allow_writes and rw.supported_operations() > ro.supported_operations()
    assert ro.supported_operations() == declared_operations(BackendKind.BROWSER, PARTIAL_OPS) - {o for o in declared_operations(BackendKind.BROWSER, PARTIAL_OPS)
                                                                                                if o in {"jira.issue.comment.add", "jira.issue.transition", "jira.issue.edit",
                                                                                                         "jira.issue.link.create", "jira.issue.attachment.upload",
                                                                                                         "jira.agile.issue.move_to_sprint", "jsm.request.comment.add", "confluence.content.update"}}


def test_create_without_the_prerequisites_raises_backend_unavailable():
    with pytest.raises(BackendUnavailableError) as exc:
        BrowserBackendFactory(has_playwright=lambda: False).create(AtlassianConfig(site="x.atlassian.net"), core=None)
    assert exc.value.hint


@pytest.mark.anyio
async def test_health_opens_one_canonical_page_and_reports_login_state_without_raising(tmp_path):
    server = ScenarioServer()
    ok = BrowserBackend(FakeBrowserSession(server), site="example.atlassian.net")
    h = await ok.health()
    assert h.status == "ready" and "selectors v" in h.detail
    out = BrowserBackend(FakeBrowserSession(server, signed_in=False), site="example.atlassian.net")
    h2 = await out.health()
    assert h2.status == "unavailable" and "AuthenticationError" in h2.detail
    assert any("lowest priority" in w for w in ok.warnings) and any("allow_browser_writes" in w for w in ok.warnings)
