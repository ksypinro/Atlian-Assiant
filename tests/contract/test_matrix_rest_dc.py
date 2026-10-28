"""Operation x rest_dc cells of design section 11. Test ids are the row slug with underscores so each MX.rest_dc.<slug> task selects its own tests."""
from __future__ import annotations

import pytest

from atlian_assiant import AtlassianConfig, BackendKind, ClientFactory
from atlian_assiant.backends.rest_dc.backend import PARTIAL_OPS, ROW_OVERRIDES
from atlian_assiant.capabilities import Deployment
from atlian_assiant.testing.dc_server import DcScenarioServer

from .matrix_harness import Ctx, cells_for, check_declared
from .matrix_rows_dc import ROWS

pytestmark = [pytest.mark.layer_05, pytest.mark.anyio]
CFG = dict(deployment=Deployment.DATA_CENTER, jira_dc_base_url="https://jira.corp.example", confluence_dc_base_url="https://wiki.corp.example", pat="pat-123456789")


def make_ctx() -> Ctx:
    server = DcScenarioServer()
    backend = ClientFactory().backend(BackendKind.REST_DC, AtlassianConfig(**CFG), http=server.client())
    return Ctx(backend, server)


SLUGS = cells_for("rest_dc")


@pytest.mark.parametrize("slug", [s.replace("-", "_") for s in SLUGS])
def test_rest_dc_declared_operations_are_supported(slug: str) -> None:
    real = next(s for s in SLUGS if s.replace("-", "_") == slug)
    check_declared(make_ctx(), "rest_dc", real, partial=PARTIAL_OPS, overrides=ROW_OVERRIDES)


@pytest.mark.parametrize("slug", [s.replace("-", "_") for s in SLUGS])
async def test_rest_dc_row_smoke(slug: str) -> None:
    real = next(s for s in SLUGS if s.replace("-", "_") == slug)
    assert real in ROWS, f"no rest_dc smoke scenario for row {real}"
    await ROWS[real](make_ctx())
