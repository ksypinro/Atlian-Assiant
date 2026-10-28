"""Listen to Slack channels where the Jira / Confluence Cloud apps post, and fetch the real issue for every key seen (DM-32 / DM-33).

    pip install -e '.[slack]'        # slack_sdk
    python deploy/slack/listen.py    # reads .env: ATLASSIAN_EVENTS_SLACK_APP_TOKEN / _BOT_TOKEN / _CHANNELS + the normal Atlassian settings

Socket Mode is an OUTBOUND WebSocket: no public URL is needed. The Slack message is only a hint; the script always reads the issue through REST,
because Slack text can be grouped, shortened or late. Stop with Ctrl+C.
"""
from __future__ import annotations

import asyncio

from atlian_assiant import AtlassianConfig, ClientFactory
from atlian_assiant.events.config import EventPlaneConfig
from atlian_assiant.events.sources.slack import SlackSocketModeSource


async def main() -> None:
    ev = EventPlaneConfig()
    if not (ev.slack_app_token and ev.slack_channels):
        raise SystemExit("Set ATLASSIAN_EVENTS_SLACK_APP_TOKEN and ATLASSIAN_EVENTS_SLACK_CHANNELS in .env (see deploy/slack/README.md)")
    client = ClientFactory().build_async(AtlassianConfig(), preset="service")
    source = SlackSocketModeSource.from_tokens(ev.slack_app_token.get_secret_value(), channels=ev.slack_channels)
    print(f"listening on {len(ev.slack_channels)} channel(s); Ctrl+C to stop")
    try:
        async for event in source.events():
            line = f"{event.received_at:%H:%M:%S} {event.product} {event.entity_key or event.entity_id}"
            if event.product == "jira" and event.entity_key:
                try:
                    issue = await client.jira.issues.get(event.entity_key)          # the canonical state, not the Slack text
                    line += f"  ->  {issue.summary!r} [{issue.status.name if issue.status else '?'}]"
                except Exception as exc:                                          # permission / deleted issue: report, keep listening
                    line += f"  ->  could not read: {type(exc).__name__}"
            print(line)
    finally:
        await source.aclose()
        await client.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
