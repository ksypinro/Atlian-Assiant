"""Live-test regression: an empty user-agent suffix produced 'atlian-assiant/0 ' (trailing space), which httpx refuses to send."""
import httpx

from atlian_assiant import AtlassianConfig
from atlian_assiant.core.services import CoreServices, user_agent


def test_user_agent_has_no_trailing_whitespace_and_keeps_the_suffix():
    assert user_agent(AtlassianConfig()) == f"atlian-assiant/{__import__('atlian_assiant').__version__}"
    assert user_agent(AtlassianConfig(user_agent_suffix="  my-bot/1.0 ")).endswith(" my-bot/1.0")


def test_the_default_http_client_can_actually_build_a_request():
    core = CoreServices.create(AtlassianConfig())
    request = core.http.build_request("GET", "https://example.atlassian.net/rest/api/3/myself")
    httpx.Request("GET", "https://x", headers=request.headers)                 # would raise LocalProtocolError on an illegal value
    assert not request.headers["user-agent"].endswith(" ")
