"""deploy/slack/*.json are what users paste into api.slack.com: they must equal socket_mode_manifest() (regenerate, never hand-edit)."""
from __future__ import annotations

import json
from pathlib import Path

from atlian_assiant.events.provision.slack import socket_mode_manifest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "slack"


def test_committed_manifests_match_the_generator_and_stay_read_only():
    public = json.loads((DEPLOY / "atlassian-events-manifest.json").read_text())
    private = json.loads((DEPLOY / "atlassian-events-manifest.private-channels.json").read_text())
    assert public == socket_mode_manifest("atlassian-events") and private == socket_mode_manifest("atlassian-events", private_channels=True)
    for m in (public, private):
        scopes = m["oauth_config"]["scopes"]["bot"]
        assert all(s.endswith((":history", ":read")) for s in scopes)                  # read-only: no chat:write, no admin scopes
        assert m["settings"]["socket_mode_enabled"] is True and "request_url" not in json.dumps(m)   # outbound WebSocket, no public URL
