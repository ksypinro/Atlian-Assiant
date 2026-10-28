"""DM-49: Kafka / AMQP / SSE feed is absent upstream: guard + downstream sinks (KafkaSink, RedisStreamSink, SseBroadcaster, WebSocketBroadcaster)."""
from __future__ import annotations

import json

import anyio
import httpx
import pytest

from atlian_assiant.core.errors import UnsupportedDeliveryPathError
from atlian_assiant.events import guards
from atlian_assiant.events.catalog import MethodKind, method
from atlian_assiant.events.pipeline import EventPipeline
from atlian_assiant.events.sinks import KafkaSink, RedisStreamSink, SseBroadcaster, WebSocketBroadcaster
from atlian_assiant.events.stores import MemoryDedupStore

from ..conftest import ListSource, event

pytestmark = [pytest.mark.layer_13, pytest.mark.anyio]


def test_dm_49_catalog_entry_is_a_guard_whose_downstream_replacement_is_the_sinks():
    m = method("DM-49")
    assert m.kind is MethodKind.GUARD and m.consumer.endswith("KafkaSink") and m.provisioner is None


def test_dm_49_refuses_an_upstream_feed_and_points_at_the_sinks():
    for kind in ("Kafka", "AMQP", "SSE"):
        with pytest.raises(UnsupportedDeliveryPathError) as exc:
            guards.upstream_event_feed(kind)
        assert kind in exc.value.message and "KafkaSink" in exc.value.hint and "SseBroadcaster" in exc.value.hint


async def test_dm_49_verifier_confirms_the_guard_refuses_with_a_hint():
    assert (await guards.verify_dm_49()).ok


class FakeProducer:
    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, value, key=None):
        self.sent.append((topic, json.loads(value), key))


class FakeRedis:
    def __init__(self):
        self.added = []

    async def xadd(self, name, fields, maxlen=None, approximate=None):
        self.added.append((name, fields, maxlen, approximate))


async def test_dm_49_kafka_and_redis_sinks_publish_normalized_events_without_the_untrusted_payload():
    producer, redis = FakeProducer(), FakeRedis()
    ev = event("a").model_copy(update={"payload": {"issue": {"fields": {"summary": "SECRET RAW"}}}})
    await KafkaSink(producer, "atlassian.events").publish(ev)
    await RedisStreamSink(redis, "atl:events", maxlen=500).publish(ev)
    topic, data, key = producer.sent[0]
    assert topic == "atlassian.events" and key == b"jira_webhook:a" and data["entity_key"] == "PROJ-1" and "payload" not in data and "SECRET RAW" not in json.dumps(data)
    name, fields, maxlen, approx = redis.added[0]
    assert name == "atl:events" and maxlen == 500 and approx is True and fields["id"] == "jira_webhook:a" and "SECRET RAW" not in fields["event"]


async def test_dm_49_sinks_receive_events_after_the_handlers_succeed_and_a_sink_outage_does_not_rerun_handlers():
    class Down:
        async def publish(self, ev):
            raise OSError("kafka down")
    producer = FakeProducer()
    pipe = EventPipeline([ListSource([event("a")])], dedup=MemoryDedupStore(), sinks=[Down(), KafkaSink(producer, "t")])
    runs = []

    async def handle(ev):
        runs.append(ev.event_id)
    pipe.on()(handle)
    await pipe.run()
    assert runs == ["a"] and len(producer.sent) == 1 and pipe.stats.sink_errors == 1


async def test_dm_49_missing_kafka_library_is_reported_with_the_extra():
    from atlian_assiant.core.errors import ConfigurationError
    with pytest.raises(ConfigurationError, match="atlian-assiant\\[kafka\\]"):
        await KafkaSink.connect("localhost:9092", "t")


async def test_dm_49_sse_streams_events_to_authorized_subscribers_and_drops_slow_ones():
    sse = SseBroadcaster(token="s3cret", max_buffer=2)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sse.asgi_app), base_url="http://x") as http:
        assert (await http.get("/events/stream")).status_code == 401 and (await http.get("/elsewhere")).status_code == 404
    received: list[str] = []

    async def client_task(scope_ready: anyio.Event):
        sent = []

        async def send(msg):
            sent.append(msg)
            if msg["type"] == "http.response.body" and msg.get("more_body"):
                received.append(msg["body"].decode())
                if len(received) == 2:
                    raise anyio.get_cancelled_exc_class()()
        scope_ready.set()
        await sse.asgi_app({"type": "http", "path": "/events/stream", "headers": [(b"authorization", b"Bearer s3cret")]}, None, send)
    ready = anyio.Event()
    with anyio.move_on_after(2):
        async with anyio.create_task_group() as tg:
            tg.start_soon(client_task, ready)
            await ready.wait()
            await anyio.sleep(0.02)
            await sse.publish(event("a"))
            await sse.publish(event("b"))
    assert len(received) == 2 and received[0].startswith("id: jira_webhook:a\nevent: jira:issue_updated\ndata: {") and received[0].endswith("\n\n")
    assert json.loads(received[1].split("data: ", 1)[1])["event_id"] == "b"
    slow = sse.subscribe()
    for i in range(5):
        await sse.publish(event(f"x{i}"))
    assert sse.dropped >= 1 and slow is not None


async def test_dm_49_websocket_broadcaster_authenticates_streams_and_stops_on_disconnect():
    ws = WebSocketBroadcaster(token="s3cret")
    closed = []

    async def deny_send(msg):
        closed.append(msg)
    async def deny_recv():
        return {"type": "websocket.connect"}
    await ws.asgi_app({"type": "websocket", "path": "/events/ws", "headers": []}, deny_recv, deny_send)
    assert closed[0]["type"] == "websocket.close" and closed[0]["code"] == 1008
    out: list[dict] = []
    inbox: anyio.abc.ObjectSendStream = None            # type: ignore[assignment]
    send_in, recv_in = anyio.create_memory_object_stream[dict](10)
    await send_in.send({"type": "websocket.connect"})

    async def send(msg):
        out.append(msg)

    async def receive():
        return await recv_in.receive()
    async with anyio.create_task_group() as tg:
        tg.start_soon(ws.asgi_app, {"type": "websocket", "path": "/events/ws", "headers": [(b"authorization", b"Bearer s3cret")]}, receive, send)
        await anyio.sleep(0.05)
        await ws.publish(event("w1"))
        await anyio.sleep(0.05)
        await send_in.send({"type": "websocket.disconnect"})
    assert out[0] == {"type": "websocket.accept"} and json.loads(out[1]["text"])["event_id"] == "w1" and inbox is None
