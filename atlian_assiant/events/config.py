"""Credentials and targets for every delivery method (design section 14.3)."""
from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class EventPlaneConfig(BaseSettings):
    """Credentials and targets for provisioning/consuming every delivery method (env prefix ``ATLASSIAN_EVENTS_``)."""

    model_config = SettingsConfigDict(env_prefix="ATLASSIAN_EVENTS_", env_file=".env", extra="ignore")

    probe_issue: str | None = None            # sandbox issue key for end-to-end probes
    probe_page: str | None = None             # sandbox Confluence page id
    store_url: str = "sqlite:///atlassian_events.db"
    public_callback_base: str | None = None   # https://hooks.example.com (DM-01..04, 09, 13, 15, 16)
    webhook_secret: SecretStr | None = None
    # AWS (DM-17, 18, 39)
    aws_region: str | None = None
    sqs_queue_url: str | None = None
    sns_topic_arn: str | None = None
    automation_aws_external_id: str | None = None
    # JEC (DM-19)
    jec_api_key: SecretStr | None = None
    jec_binary: str = "jec"
    # Email (DM-20, 25-29)
    imap_host: str | None = None
    imap_user: str | None = None
    imap_password: SecretStr | None = None
    imap_folder: str = "INBOX"
    # Slack (DM-21, 32, 33)
    slack_app_token: SecretStr | None = None  # xapp-... (Socket Mode)
    slack_bot_token: SecretStr | None = None  # xoxb-...
    slack_channels: list[str] = []
    # Teams (DM-22, 34)
    msgraph_tenant_id: str | None = None
    msgraph_client_id: str | None = None
    msgraph_client_secret: SecretStr | None = None
    teams_team_id: str | None = None
    teams_channel_id: str | None = None
    # Twilio (DM-23)
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None
    # Relays (DM-40..43)
    azure_service_bus_conn: SecretStr | None = None
    gcp_subscription: str | None = None
    cloudflare_account_id: str | None = None
    cloudflare_queue_id: str | None = None
    cloudflare_api_token: SecretStr | None = None
    broker_pull_url: str | None = None
    broker_token: SecretStr | None = None
    # Tunnels (DM-44)
    ngrok_authtoken: SecretStr | None = None
    cloudflared_binary: str = "cloudflared"
