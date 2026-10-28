"""In-memory SNS / SQS / IAM / SSM fakes with the behaviours the provisioners and SqsSource rely on (redrive, visibility, queue policy, raw delivery)."""
from __future__ import annotations

import itertools
import json


class FakeSqs:
    def __init__(self) -> None:
        self.queues: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self.visibility_changes: list[tuple[str, int]] = []
        self.now = 0.0

    def create_queue(self, QueueName: str, Attributes: dict | None = None) -> dict:
        url = f"https://sqs.us-east-1.amazonaws.com/000000000000/{QueueName}"
        q = self.queues.setdefault(url, {"name": QueueName, "attrs": {}, "messages": []})
        q["attrs"].update(Attributes or {})
        q["attrs"]["QueueArn"] = f"arn:aws:sqs:us-east-1:000000000000:{QueueName}"
        return {"QueueUrl": url}

    def get_queue_attributes(self, QueueUrl: str, AttributeNames: list[str]) -> dict:
        return {"Attributes": {k: self.queues[QueueUrl]["attrs"][k] for k in AttributeNames}}

    def set_queue_attributes(self, QueueUrl: str, Attributes: dict) -> None:
        self.queues[QueueUrl]["attrs"].update(Attributes)

    def delete_queue(self, QueueUrl: str) -> None:
        self.queues.pop(QueueUrl, None)

    def _put(self, url: str, body: str, source_arn: str | None = None) -> bool:
        q = self.queues[url]
        policy = json.loads(q["attrs"].get("Policy", "{}"))
        if source_arn is not None and policy:
            allowed = any(s["Condition"]["ArnEquals"]["aws:SourceArn"] == source_arn for s in policy["Statement"])
            if not allowed:
                return False                                   # the queue policy refuses other topics
        q["messages"].append({"MessageId": f"m{next(self._ids)}", "Body": body, "receipts": 0, "hidden_until": 0.0, "handle": None})
        return True

    def send_message(self, QueueUrl: str, MessageBody: str) -> None:
        self._put(QueueUrl, MessageBody)

    def receive_message(self, QueueUrl: str, MaxNumberOfMessages: int = 1, WaitTimeSeconds: int = 0, VisibilityTimeout: int = 30, AttributeNames=None) -> dict:
        q = self.queues[QueueUrl]
        redrive = json.loads(q["attrs"].get("RedrivePolicy", "{}"))
        out = []
        for m in list(q["messages"]):
            if m["hidden_until"] > self.now:
                continue
            if redrive and m["receipts"] >= int(redrive["maxReceiveCount"]):      # too many receives: move to the dead-letter queue
                q["messages"].remove(m)
                dlq = next(u for u, d in self.queues.items() if d["attrs"]["QueueArn"] == redrive["deadLetterTargetArn"])
                self.queues[dlq]["messages"].append({**m, "receipts": 0, "hidden_until": 0.0})
                continue
            if len(out) >= MaxNumberOfMessages:
                break
            m["receipts"] += 1
            m["hidden_until"] = self.now + VisibilityTimeout
            m["handle"] = f"rh-{m['MessageId']}-{m['receipts']}"
            out.append({"MessageId": m["MessageId"], "Body": m["Body"], "ReceiptHandle": m["handle"], "Attributes": {"ApproximateReceiveCount": str(m["receipts"])}})
        return {"Messages": out}

    def delete_message(self, QueueUrl: str, ReceiptHandle: str) -> None:
        q = self.queues[QueueUrl]
        q["messages"] = [m for m in q["messages"] if m["handle"] != ReceiptHandle]

    def change_message_visibility(self, QueueUrl: str, ReceiptHandle: str, VisibilityTimeout: int) -> None:
        self.visibility_changes.append((ReceiptHandle, VisibilityTimeout))
        for m in self.queues[QueueUrl]["messages"]:
            if m["handle"] == ReceiptHandle:
                m["hidden_until"] = self.now + VisibilityTimeout

    def depth(self, url: str) -> int:
        return len(self.queues[url]["messages"])


class FakeSns:
    def __init__(self, sqs: FakeSqs) -> None:
        self.sqs, self.topics, self.subs = sqs, {}, {}

    def create_topic(self, Name: str) -> dict:
        arn = f"arn:aws:sns:us-east-1:000000000000:{Name}"
        self.topics[arn] = Name
        return {"TopicArn": arn}

    def subscribe(self, TopicArn: str, Protocol: str, Endpoint: str, Attributes: dict, ReturnSubscriptionArn: bool = True) -> dict:
        sub_arn = f"{TopicArn}:sub{len(self.subs) + 1}"
        self.subs[sub_arn] = {"topic": TopicArn, "queue_arn": Endpoint, "raw": Attributes.get("RawMessageDelivery") == "true"}
        return {"SubscriptionArn": sub_arn}

    def unsubscribe(self, SubscriptionArn: str) -> None:
        self.subs.pop(SubscriptionArn, None)

    def delete_topic(self, TopicArn: str) -> None:
        self.topics.pop(TopicArn, None)

    def publish(self, TopicArn: str, Message: str) -> int:
        delivered = 0
        for sub in self.subs.values():
            if sub["topic"] != TopicArn:
                continue
            url = next(u for u, d in self.sqs.queues.items() if d["attrs"]["QueueArn"] == sub["queue_arn"])
            body = Message if sub["raw"] else json.dumps({"Type": "Notification", "MessageId": "n1", "TopicArn": TopicArn, "Message": Message})
            delivered += self.sqs._put(url, body, source_arn=TopicArn)
        return delivered


class FakeIam:
    def __init__(self) -> None:
        self.roles, self.policies = {}, {}

    def create_role(self, RoleName: str, AssumeRolePolicyDocument: str) -> dict:
        self.roles[RoleName] = json.loads(AssumeRolePolicyDocument)
        return {"Role": {"Arn": f"arn:aws:iam::000000000000:role/{RoleName}"}}

    def put_role_policy(self, RoleName: str, PolicyName: str, PolicyDocument: str) -> None:
        self.policies[(RoleName, PolicyName)] = json.loads(PolicyDocument)

    def delete_role_policy(self, RoleName: str, PolicyName: str) -> None:
        self.policies.pop((RoleName, PolicyName), None)

    def delete_role(self, RoleName: str) -> None:
        self.roles.pop(RoleName, None)


class FakeSsm:
    def __init__(self) -> None:
        self.documents, self.activations, self.invocations = {}, [], {}

    def create_document(self, Content: str, Name: str, DocumentType: str, DocumentFormat: str) -> None:
        self.documents[Name] = json.loads(Content)

    def delete_document(self, Name: str) -> None:
        self.documents.pop(Name, None)

    def create_activation(self, DefaultInstanceName: str, IamRole: str, RegistrationLimit: int) -> dict:
        self.activations.append(DefaultInstanceName)
        return {"ActivationId": f"act-{len(self.activations)}", "ActivationCode": "SECRETCODE"}

    def get_command_invocation(self, CommandId: str, InstanceId: str) -> dict:
        return self.invocations[(CommandId, InstanceId)]


def aws():
    from atlian_assiant.events.provision.aws import AwsClients
    sqs = FakeSqs()
    return AwsClients(sns=FakeSns(sqs), sqs=sqs, iam=FakeIam(), ssm=FakeSsm())
