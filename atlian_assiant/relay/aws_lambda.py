"""AWS relay (DM-39): API Gateway HTTP API -> Lambda -> SQS. Same verifier chain as a local receiver; boto3 is imported lazily."""
from __future__ import annotations

import os
from typing import Any

from .core import build_receiver, decode_body, process

_sqs_client: Any = None
_receiver: Any = None


def _sqs() -> Any:
    global _sqs_client
    if _sqs_client is None:
        import boto3
        _sqs_client = boto3.client("sqs")
    return _sqs_client


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    global _receiver
    if _receiver is None:
        _receiver = build_receiver()
    http = event.get("requestContext", {}).get("http", {})
    status, message = process(_receiver, http.get("method", "POST"), event.get("rawPath", "/"), event.get("headers") or {},
                              decode_body(event.get("body"), bool(event.get("isBase64Encoded"))), http.get("sourceIp"))
    if message is None:
        return {"statusCode": status}                                    # 401 forged / 400 malformed: not retried by Atlassian
    _sqs().send_message(QueueUrl=os.environ["QUEUE_URL"], MessageBody=message)
    return {"statusCode": 202}                                           # an SQS failure raises -> Lambda returns 5xx -> Atlassian retries
