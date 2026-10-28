"""A stateful fake of the Forge companion app's App REST API (inbox, ack, subscriptions) and of the `forge` CLI."""
from __future__ import annotations

from atlian_assiant.backends.forge.cli import CliResult


class FakeInboxApp:
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.acked: list[str] = []
        self.subs: list[dict] = []
        self.fail_list = False

    def add(self, n: int, event_type: str = "avi:jira:updated:issue", entity: dict | None = None, ts: int = 1788264000000) -> dict:
        rec = {"id": f"id{n}", "key": f"evt#{ts + n:015d}#id{n}", "source": event_type.split(":")[1], "eventType": event_type,
               "entity": entity or {"id": str(10000 + n), "key": f"PROJ-{n}"}, "receivedAt": ts + n, "processed": False}
        self.records.append(rec)
        return rec

    async def events(self, *, cursor=None, limit: int = 100) -> dict:
        pending = [r for r in self.records if r["key"] not in self.acked]
        pos = int(cursor) if cursor else 0
        chunk = pending[pos:pos + limit]
        nxt = str(pos + len(chunk)) if pos + len(chunk) < len(pending) else None
        return {"events": chunk, "nextCursor": nxt}

    async def ack(self, keys: list[str]) -> None:
        self.acked += keys

    async def health(self) -> dict:
        return {"ok": True}

    async def list_subscriptions(self) -> list[dict]:
        if self.fail_list:
            from atlian_assiant.core.errors import ServerError
            raise ServerError("503")
        return list(self.subs)

    async def add_subscription(self, module: dict) -> dict:
        self.subs.append(module)
        return module

    async def remove_subscription(self, key: str) -> None:
        self.subs = [m for m in self.subs if m.get("key") != key]


class FakeForgeCli:
    """Records CLI calls; `logs` returns whatever the test sets."""

    def __init__(self, logs: str = "") -> None:
        self.calls: list[list[str]] = []
        self.logs_text = logs
        self.fail_on: str | None = None

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if self.fail_on and self.fail_on in argv:
            return CliResult(1, "", "boom")
        return CliResult(0, self.logs_text if "logs" in argv else "done")
