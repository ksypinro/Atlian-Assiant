"""RssFeedProvisioner (DM-30 Jira, DM-31 Confluence): build authenticated feed URLs. Credentials are NEVER put in the URL: the source sends them as Basic auth."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlencode

from ...core.errors import InvalidRequestError
from .base import ProvisionMode
from .human import HumanProvisioner


@dataclass(frozen=True)
class FeedSpec:
    url: str
    method_id: str
    auth: str = "basic (email + API token), sent as a header"


class RssFeedProvisioner(HumanProvisioner):
    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-30", "DM-31"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})

    def __init__(self, site: str, *, probe: object = None) -> None:
        super().__init__(probe=probe)
        self.site = site.removeprefix("https://").rstrip("/")
        self.feed: FeedSpec | None = None

    @classmethod
    def jira_filter(cls, site: str, filter_id: str | int, *, max_results: int = 100) -> RssFeedProvisioner:
        p = cls(site)
        p.feed = FeedSpec(f"https://{p.site}/sr/jira.issueviews:searchrequest-rss/{filter_id}/SearchRequest-{filter_id}.xml?tempMax={max_results}", "DM-30")
        return p

    @classmethod
    def confluence(cls, site: str, *, spaces: list[str] | None = None, types: tuple[str, ...] = ("page", "blogpost"), max_results: int = 50, time_span_days: int = 1) -> RssFeedProvisioner:
        p = cls(site)
        params: list[tuple[str, str]] = [("types", t) for t in types] + [("spaces", s) for s in spaces or []] + [
            ("sort", "modified"), ("maxResults", str(max_results)), ("timeSpan", str(time_span_days)), ("showContent", "false"), ("publicFeed", "false")]
        p.feed = FeedSpec(f"https://{p.site}/wiki/createrssfeed.action?{urlencode(params)}", "DM-31")
        return p

    @property
    def method_id(self) -> str:
        return self.feed.method_id if self.feed else "DM-30"

    def _check(self) -> FeedSpec:
        if self.feed is None:
            raise InvalidRequestError("no feed configured", hint="Use RssFeedProvisioner.jira_filter(...) or .confluence(...).")
        return self.feed

    def api_steps(self) -> list[str]:
        return [f"Feed URL: {self._check().url}"]

    def guided_steps(self) -> list[str]:
        feed = self._check()
        return [f"Feed URL: {feed.url}", "Authenticate with Basic auth (email + API token) as a header; never paste the token into the URL",
                "Share the underlying filter with the account that polls the feed" if feed.method_id == "DM-30" else "The account that polls must be able to view the spaces"]

    def warnings(self) -> tuple[str, ...]:
        return ("feed URL formats are unverified (VER-20): open the URL once in a browser session to confirm", "RSS is lightweight: enrichment supplies canonical state")

    def outputs(self) -> dict[str, str]:
        return {"feed_url": self._check().url, "auth": self._check().auth}

    async def _apply_api(self) -> dict[str, str]:
        return {"feed_url": self._check().url}
