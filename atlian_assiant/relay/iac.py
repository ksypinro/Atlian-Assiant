"""Infrastructure-as-code for each relay (design 14.8): short retention, secrets by reference, handler packaged next to the template."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RETENTION_SECONDS = 86400


@dataclass(frozen=True)
class RelayFiles:
    files: dict[str, str]
    deploy_command: tuple[str, ...]
    url_hint: str                     # regex group 1 captures the public URL from the deploy output


def aws_sam(name: str, retention: int = RETENTION_SECONDS) -> RelayFiles:
    tpl = f"""AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: Atlassian webhook relay (DM-39): API Gateway HTTP API -> Lambda -> SQS
Parameters:
  WebhookSecret: {{Type: String, NoEcho: true, Default: ''}}
  WebhookToken: {{Type: String, NoEcho: true, Default: ''}}
Resources:
  DeadLetter:
    Type: AWS::SQS::Queue
    Properties: {{MessageRetentionPeriod: {retention}, SqsManagedSseEnabled: true}}
  Events:
    Type: AWS::SQS::Queue
    Properties:
      MessageRetentionPeriod: {retention}
      SqsManagedSseEnabled: true
      ReceiveMessageWaitTimeSeconds: 20
      RedrivePolicy: {{deadLetterTargetArn: !GetAtt DeadLetter.Arn, maxReceiveCount: 5}}
  RelayFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: atlian_assiant.relay.aws_lambda.handler
      Runtime: python3.12
      CodeUri: .
      Timeout: 10
      Environment:
        Variables:
          QUEUE_URL: !Ref Events
          ATLASSIAN_EVENTS_WEBHOOK_SECRET: !Ref WebhookSecret
          ATLASSIAN_EVENTS_WEBHOOK_TOKEN: !Ref WebhookToken
      Policies:
        - SQSSendMessagePolicy: {{QueueName: !GetAtt Events.QueueName}}
      Events:
        Hook:
          Type: HttpApi
          Properties: {{Path: /hooks/jira, Method: POST}}
Outputs:
  RelayUrl: {{Value: !Sub 'https://${{ServerlessHttpApi}}.execute-api.${{AWS::Region}}.amazonaws.com/hooks/jira'}}
  QueueUrl: {{Value: !Ref Events}}
"""
    return RelayFiles({"template.yaml": tpl}, ("sam", "deploy", "--no-confirm-changeset", "--stack-name", name, "--capabilities", "CAPABILITY_IAM"),
                      r"RelayUrl\s+(\S+)")


def azure_bicep(name: str, retention: int = RETENTION_SECONDS) -> RelayFiles:
    ttl = f"PT{retention // 3600}H" if retention >= 3600 else f"PT{retention}S"
    tpl = f"""// Atlassian webhook relay (DM-40): HTTP-triggered Function -> Service Bus queue
param location string = resourceGroup().location
@secure()
param webhookSecret string = ''
resource bus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {{
  name: '{name}-bus'
  location: location
  sku: {{ name: 'Basic', tier: 'Basic' }}
}}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {{
  parent: bus
  name: 'atlassian-events'
  properties: {{ defaultMessageTimeToLive: '{ttl}', maxDeliveryCount: 5, deadLetteringOnMessageExpiration: true }}
}}
output serviceBus string = bus.name
"""
    return RelayFiles({"main.bicep": tpl}, ("az", "deployment", "group", "create", "--template-file", "main.bicep"), r"(https://\S+)")


def gcp_terraform(name: str, retention: int = RETENTION_SECONDS) -> RelayFiles:
    tpl = f"""# Atlassian webhook relay (DM-41): Cloud Run / Functions HTTP -> Pub/Sub
variable "project" {{ type = string }}
variable "region"  {{ type = string, default = "us-central1" }}
resource "google_pubsub_topic" "events" {{
  name = "{name}-events"
  message_retention_duration = "{retention}s"
}}
resource "google_pubsub_subscription" "pull" {{
  name  = "{name}-pull"
  topic = google_pubsub_topic.events.name
  message_retention_duration = "{retention}s"
  ack_deadline_seconds = 60
  expiration_policy {{ ttl = "" }}
}}
output "topic_path" {{ value = google_pubsub_topic.events.id }}
"""
    return RelayFiles({"main.tf": tpl}, ("terraform", "apply", "-auto-approve"), r"(https://\S+)")


def cloudflare() -> RelayFiles:
    root = Path(__file__).parent / "cloudflare_worker"
    return RelayFiles({"index.js": (root / "index.js").read_text(), "wrangler.toml": (root / "wrangler.toml").read_text()}, ("wrangler", "deploy"), r"(https://\S+\.workers\.dev)")
