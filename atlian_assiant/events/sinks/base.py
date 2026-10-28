"""Downstream sinks (design 14.12): the streaming feed Atlassian does not offer (DM-49 replacement), plus re-notification of people."""
from __future__ import annotations

import ipaddress
import json
from typing import Any
from urllib.parse import urlsplit

from ...core.errors import ConfigurationError, InvalidRequestError
from ..model import ChangeEvent


def event_json(event: ChangeEvent, *, include_payload: bool = False) -> str:
    """Wire form of an event for sinks. The untrusted raw ``payload`` is dropped by default; enriched ``canonical`` state is included when serializable."""
    data = json.loads(event.model_dump_json(exclude={"payload", "canonical"} if not include_payload else {"canonical"}))
    canonical = event.canonical
    if canonical is not None and hasattr(canonical, "model_dump"):
        data["canonical"] = canonical.model_dump(mode="json", exclude={"raw", "source"})
    return json.dumps(data, sort_keys=True)


class KafkaSink:
    """Durable internal topic of normalized, enriched events. ``producer`` needs ``async send_and_wait(topic, value, key)`` (aiokafka's ``AIOKafkaProducer``)."""

    def __init__(self, producer: Any, topic: str) -> None:
        self.producer, self.topic = producer, topic

    @classmethod
    async def connect(cls, bootstrap: str, topic: str) -> KafkaSink:
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:
            raise ConfigurationError("aiokafka is not installed", hint="pip install 'atlian-assiant[kafka]'") from exc
        producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
        await producer.start()
        return cls(producer, topic)

    async def publish(self, event: ChangeEvent) -> None:
        await self.producer.send_and_wait(self.topic, event_json(event).encode(), key=event.dedup_key.encode())     # keyed: one entity's events stay ordered per partition


class RedisStreamSink:
    """Lightweight internal stream. ``redis`` needs ``async xadd(name, fields, maxlen=..., approximate=True)`` (redis.asyncio)."""

    def __init__(self, redis: Any, stream: str, *, maxlen: int = 100_000) -> None:
        self.redis, self.stream, self.maxlen = redis, stream, maxlen

    async def publish(self, event: ChangeEvent) -> None:
        await self.redis.xadd(self.stream, {"event": event_json(event), "id": event.dedup_key}, maxlen=self.maxlen, approximate=True)


class WebhookForwardSink:
    """Forward to internal services. LAN ONLY: refuses a public destination unless ``allow_public=True``, so enriched data never leaves by accident."""

    def __init__(self, http: Any, url: str, *, allow_public: bool = False, headers: dict[str, str] | None = None) -> None:
        host = urlsplit(url).hostname or ""
        if not allow_public and not _is_internal(host):
            raise InvalidRequestError(f"{host!r} is not an internal address", hint="WebhookForwardSink is for LAN services; pass allow_public=True only after a security review.")
        self.http, self.url, self.headers = http, url, headers or {}

    async def publish(self, event: ChangeEvent) -> None:
        resp = await self.http.post(self.url, content=event_json(event), headers={"Content-Type": "application/json", **self.headers})
        resp.raise_for_status()


def _is_internal(host: str) -> bool:
    if host in ("localhost",) or host.endswith((".local", ".internal", ".lan", ".corp")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host                       # a bare hostname (no dots) resolves inside the LAN
    return ip.is_private or ip.is_loopback or ip.is_link_local
