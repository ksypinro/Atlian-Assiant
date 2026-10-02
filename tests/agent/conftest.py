from __future__ import annotations

import pytest

from atlassian_client import AtlassianConfig, BackendKind, ClientFactory
from atlassian_client.policy import AccessPolicy
from atlassian_client.testing import FakeBackend


def build(policy: AccessPolicy | None = None, **kw: object):
    return ClientFactory().build_async(AtlassianConfig(site="example.atlassian.net"), backends=[], preset="agent",
                                       policy=policy or AccessPolicy(allow_credit_consuming=True),
                                       extra_backends={BackendKind.REST: FakeBackend(), BackendKind.MCP: FakeBackend(BackendKind.MCP)}, **kw)


@pytest.fixture()
def client():
    return build()
