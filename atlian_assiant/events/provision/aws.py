"""AWS provisioners (design 14.8-14.9): SNS -> SQS bridge (DM-17, DM-39) and SSM command documents (DM-18). boto3 clients are injected (lazy import otherwise)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar

from ...core.errors import ConfigurationError, InvalidRequestError
from ...policy import ConfirmationHandler
from .base import ProvisioningPlan, ProvisioningStep, ProvisionMode, VerificationReport, confirm_plan, pick_mode

#: The principal Jira Automation assumes the role from. UNVERIFIED (VER-16): pass the value shown in the Automation "AWS connection" dialog.
AUTOMATION_PRINCIPAL_PLACEHOLDER = "arn:aws:iam::<AUTOMATION-ACCOUNT-FROM-THE-AUTOMATION-UI>:root"


@dataclass
class AwsClients:
    sns: Any
    sqs: Any
    iam: Any
    ssm: Any = None
    sts: Any = None

    @classmethod
    def from_boto3(cls, region: str | None = None) -> AwsClients:
        try:
            import boto3
        except ImportError as exc:
            raise ConfigurationError("boto3 is not installed", hint="pip install 'atlian-assiant[aws]'") from exc
        s = boto3.session.Session(region_name=region)
        return cls(sns=s.client("sns"), sqs=s.client("sqs"), iam=s.client("iam"), ssm=s.client("ssm"), sts=s.client("sts"))


def queue_policy(queue_arn: str, topic_arn: str) -> str:
    """Only the SNS topic may send to the queue (bound to the topic ARN, EV section 23)."""
    return json.dumps({"Version": "2012-10-17", "Statement": [{"Sid": "AllowSnsTopicOnly", "Effect": "Allow", "Principal": {"Service": "sns.amazonaws.com"},
                                                               "Action": "sqs:SendMessage", "Resource": queue_arn, "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}}}]})


def automation_trust_policy(principal: str, external_id: str) -> str:
    """Trust policy for the role Automation assumes: needs the external ID shown in the Automation UI (confused-deputy protection)."""
    return json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": principal}, "Action": "sts:AssumeRole",
                                                               "Condition": {"StringEquals": {"sts:ExternalId": external_id}}}]})


def publish_only_policy(topic_arn: str) -> str:
    return json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sns:Publish", "Resource": topic_arn}]})


class AwsQueueProvisioner:
    """DM-17 / DM-39: SNS topic, SQS queue + DLQ (redrive ``maxReceiveCount``), raw-delivery subscription, queue policy bound to the topic ARN,
    optional SSE-KMS, and the IAM role for Automation's AWS connection (trust policy + external ID). Outputs the topic ARN for the Automation template."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-17", "DM-39"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})

    def __init__(self, clients: AwsClients | None, *, name: str = "atlassian-events", external_id: str | None = None, kms_key_id: str | None = None,
                 max_receive_count: int = 5, automation_principal: str = AUTOMATION_PRINCIPAL_PLACEHOLDER, probe: Any = None, region: str | None = None) -> None:
        self.clients, self.name, self.external_id, self.kms, self.max_receive = clients, name, external_id, kms_key_id, max_receive_count
        self.principal, self._probe, self.region = automation_principal, probe, region
        self.created: dict[str, str] = {}

    def _supported(self) -> frozenset[ProvisionMode]:
        return frozenset({ProvisionMode.GUIDED} | ({ProvisionMode.API} if self.clients is not None else set()))

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        chosen = pick_mode(mode, self._supported(), (ProvisionMode.API, ProvisionMode.GUIDED))
        if not self.external_id:
            raise InvalidRequestError("the Automation external ID is required", hint="Copy it from the Automation 'AWS connection' dialog into automation_aws_external_id (VER-16).",
                                      operation="events.provision")
        warnings = [f"role trust uses the principal {self.principal!r}: confirm it against the Automation UI (VER-16)"]
        if self.principal == AUTOMATION_PRINCIPAL_PLACEHOLDER:
            warnings.append("the Automation principal is still the placeholder: the role will not be assumable until you set it")
        if not self.kms:
            warnings.append("no KMS key: messages are not encrypted with a customer-managed key (SSE-SQS only)")
        names = [f"SNS topic {self.name}", f"SQS dead-letter queue {self.name}-dlq", f"SQS queue {self.name} (redrive after {self.max_receive} receives)",
                 "SNS -> SQS subscription with raw message delivery", "queue policy: only this topic may send", "IAM role for Automation (external ID) with sns:Publish on this topic only"]
        steps = tuple(ProvisioningStep(n, chosen, True, True) for n in names)
        return ProvisioningPlan("DM-17", steps, {"mode": chosen.value, "name": self.name}, tuple(warnings))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.register", target=self.name)
        if ProvisionMode(plan.outputs["mode"]) is ProvisionMode.GUIDED or self.clients is None:
            return {**plan.outputs, "instructions": "\n".join(f"{i}. {s.description}" for i, s in enumerate(plan.steps, 1)),
                    "queue_policy": queue_policy("<queue-arn>", "<topic-arn>"), "trust_policy": automation_trust_policy(self.principal, self.external_id or "")}
        c = self.clients
        assert self.external_id
        topic_arn = c.sns.create_topic(Name=self.name)["TopicArn"]
        dlq_url = c.sqs.create_queue(QueueName=f"{self.name}-dlq")["QueueUrl"]
        dlq_arn = c.sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        attrs = {"RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": str(self.max_receive)}), "ReceiveMessageWaitTimeSeconds": "20"}
        attrs.update({"KmsMasterKeyId": self.kms} if self.kms else {"SqsManagedSseEnabled": "true"})
        queue_url = c.sqs.create_queue(QueueName=self.name, Attributes=attrs)["QueueUrl"]
        queue_arn = c.sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        c.sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={"Policy": queue_policy(queue_arn, topic_arn)})
        sub = c.sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn, Attributes={"RawMessageDelivery": "true"}, ReturnSubscriptionArn=True)
        role_name = f"{self.name}-automation"
        role_arn = c.iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=automation_trust_policy(self.principal, self.external_id))["Role"]["Arn"]
        c.iam.put_role_policy(RoleName=role_name, PolicyName="publish-only", PolicyDocument=publish_only_policy(topic_arn))
        self.created = {"topic_arn": topic_arn, "queue_url": queue_url, "queue_arn": queue_arn, "dlq_url": dlq_url, "subscription_arn": sub["SubscriptionArn"],
                        "role_arn": role_arn, "role_name": role_name}
        return {**plan.outputs, **self.created}

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport("DM-17", False, None, "no end-to-end probe configured: pass probe=verify_dm_17 bound to your inbox")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        if not self.created or self.clients is None:
            return
        plan = ProvisioningPlan("DM-17", (ProvisioningStep("Delete the SNS subscription, topic, queues and the Automation role", ProvisionMode.API, False, True),), dict(self.created))
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=self.name)
        c, made = self.clients, self.created
        c.sns.unsubscribe(SubscriptionArn=made["subscription_arn"])
        c.sns.delete_topic(TopicArn=made["topic_arn"])
        c.sqs.delete_queue(QueueUrl=made["queue_url"])
        c.sqs.delete_queue(QueueUrl=made["dlq_url"])
        c.iam.delete_role_policy(RoleName=made["role_name"], PolicyName="publish-only")
        c.iam.delete_role(RoleName=made["role_name"])
        self.created = {}


def ssm_document(name: str, handler: str) -> dict[str, Any]:
    """An SSM Command document whose step runs the Python handler with the Automation payload as a parameter (EV section 24)."""
    return {"schemaVersion": "2.2", "description": f"{name}: run an atlian-assiant handler for a Jira Automation event",
            "parameters": {"payload": {"type": "String", "description": "JSON envelope from Jira Automation", "maxChars": 20000}},
            "mainSteps": [{"action": "aws:runShellScript", "name": "handle", "inputs": {"runCommand": [
                f"python -m atlian_assiant.events.ssm_handler --handler {handler} --payload '{{{{ payload }}}}'"]}}]}


def ssm_send_command_policy(region: str, account: str, document: str, tag_key: str, tag_value: str) -> str:
    """Allows Automation ``ssm:SendCommand`` on THIS document and only on instances carrying the tag."""
    return json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "ssm:SendCommand", "Resource": f"arn:aws:ssm:{region}:{account}:document/{document}"},
        {"Effect": "Allow", "Action": "ssm:SendCommand", "Resource": f"arn:aws:ec2:{region}:{account}:instance/*",
         "Condition": {"StringEquals": {f"ssm:resourceTag/{tag_key}": tag_value}}},
        {"Effect": "Allow", "Action": "ssm:SendCommand", "Resource": f"arn:aws:ssm:{region}:{account}:managed-instance/*",
         "Condition": {"StringEquals": {f"ssm:resourceTag/{tag_key}": tag_value}}}]})


class SsmProvisioner:
    """DM-18: SSM Command document, tag-based targets, a hybrid activation for on-prem nodes, and a least-privilege IAM policy."""

    method_ids: ClassVar[frozenset[str]] = frozenset({"DM-18"})
    modes: ClassVar[frozenset[ProvisionMode]] = frozenset({ProvisionMode.API, ProvisionMode.GUIDED})

    def __init__(self, clients: AwsClients | None, *, handler: str, document_name: str = "AtlassianClientHandler", region: str = "us-east-1", account_id: str = "000000000000",
                 tag_key: str = "atlassian-handler", tag_value: str = "true", on_prem: bool = False, external_id: str | None = None,
                 automation_principal: str = AUTOMATION_PRINCIPAL_PLACEHOLDER, probe: Any = None) -> None:
        self.clients, self.handler, self.doc, self.region, self.account = clients, handler, document_name, region, account_id
        self.tag_key, self.tag_value, self.on_prem, self.external_id, self.principal, self._probe = tag_key, tag_value, on_prem, external_id, automation_principal, probe
        self.created: dict[str, str] = {}

    async def plan(self, *, mode: ProvisionMode | None = None) -> ProvisioningPlan:
        supported = frozenset({ProvisionMode.GUIDED} | ({ProvisionMode.API} if self.clients is not None else set()))
        chosen = pick_mode(mode, supported, (ProvisionMode.API, ProvisionMode.GUIDED))
        names = [f"SSM Command document {self.doc} running {self.handler}", f"target nodes tagged {self.tag_key}={self.tag_value}", "IAM policy: ssm:SendCommand on this document only"]
        if self.on_prem:
            names.append("hybrid activation for on-prem nodes (`ssm create-activation`): LAN machines take commands through AWS's management channel")
        return ProvisioningPlan("DM-18", tuple(ProvisioningStep(n, chosen, True, True) for n in names), {"mode": chosen.value, "document": self.doc},
                                (f"role trust principal {self.principal!r} is unverified (VER-16)",))

    async def apply(self, plan: ProvisioningPlan, *, confirm: ConfirmationHandler) -> Mapping[str, str]:
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.register", target=self.doc)
        policy = ssm_send_command_policy(self.region, self.account, self.doc, self.tag_key, self.tag_value)
        if ProvisionMode(plan.outputs["mode"]) is ProvisionMode.GUIDED or self.clients is None:
            return {**plan.outputs, "document_json": json.dumps(ssm_document(self.doc, self.handler)), "policy": policy}
        c = self.clients
        c.ssm.create_document(Content=json.dumps(ssm_document(self.doc, self.handler)), Name=self.doc, DocumentType="Command", DocumentFormat="JSON")
        out = {**plan.outputs, "policy": policy, "tag": f"{self.tag_key}={self.tag_value}"}
        if self.on_prem:
            act = c.ssm.create_activation(DefaultInstanceName="atlassian-handler", IamRole="SSMServiceRole", RegistrationLimit=10)
            out.update({"activation_id": act["ActivationId"], "activation_code": "<shown once: store it as a secret>"})
        if self.external_id and c.iam is not None:
            role = f"{self.doc}-automation"
            out["role_arn"] = c.iam.create_role(RoleName=role, AssumeRolePolicyDocument=automation_trust_policy(self.principal, self.external_id))["Role"]["Arn"]
            c.iam.put_role_policy(RoleName=role, PolicyName="send-command", PolicyDocument=policy)
        self.created = out
        return out

    async def verify(self, *, timeout: timedelta = timedelta(minutes=5)) -> VerificationReport:
        if self._probe is None:
            return VerificationReport("DM-18", False, None, "no end-to-end probe configured: pass probe=verify_dm_18 bound to GetCommandInvocation")
        report: VerificationReport = await self._probe(timeout)
        return report

    async def teardown(self, *, confirm: ConfirmationHandler) -> None:
        if self.clients is None or not self.created:
            return
        plan = ProvisioningPlan("DM-18", (ProvisioningStep(f"Delete the SSM document {self.doc}", ProvisionMode.API, False, True),), {"document": self.doc})
        await confirm_plan(plan, confirm, operation_id="webhook.jira.dynamic.delete", target=self.doc)
        self.clients.ssm.delete_document(Name=self.doc)
        self.created = {}
