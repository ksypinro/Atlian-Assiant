"""GCP relay (DM-41): Cloud Run / Functions HTTP -> Pub/Sub. The Pub/Sub client is imported lazily."""
from __future__ import annotations

import os
from typing import Any

from .core import build_receiver, process

_receiver: Any = None
_publisher: Any = None


def _publish(message: str) -> None:
    global _publisher
    if _publisher is None:
        from google.cloud import pubsub_v1
        _publisher = pubsub_v1.PublisherClient()
    _publisher.publish(os.environ["TOPIC_PATH"], message.encode()).result(timeout=10)


def handler(request: Any) -> tuple[str, int]:
    """functions-framework / Flask-style request object."""
    global _receiver
    if _receiver is None:
        _receiver = build_receiver()
    status, message = process(_receiver, request.method, request.path, dict(request.headers), request.get_data(), request.remote_addr)
    if message is not None:
        _publish(message)
    return "", status
