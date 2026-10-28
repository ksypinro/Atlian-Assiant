"""Forge companion-app generator (design 14.6, EV sections 8-17): produces a deployable app for native / inbox / remote modes (+ realtime panel).

Everything the research says about limits and filters is enforced here: Async Events (<= 50 events and 200 KB per push, consumer timeout <= 900 s),
all four trigger filter options, minimal scopes, scoped Realtime channels by default. Items the research marks as unverified (VER-06, VER-07, VER-23,
VER-24) are isolated in clearly marked comments of the generated files.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from ...core.errors import InvalidRequestError, UnsupportedDeliveryPathError

Mode = Literal["native", "inbox", "remote"]
MAX_PUSH_EVENTS, MAX_PUSH_BYTES, MAX_TIMEOUT_SECONDS, MAX_DYNAMIC_MODULES = 50, 200 * 1024, 900, 100

#: (product -> noun -> verbs) for the event catalog the generator can subscribe to (EV sections 9-10)
EVENT_CATALOG: dict[str, dict[str, tuple[str, ...]]] = {
    "jira": {"issue": ("created", "updated", "deleted", "assigned", "viewed", "mentioned"), "comment": ("created", "updated", "deleted"),
             "attachment": ("created", "deleted"), "worklog": ("created", "updated", "deleted"), "issuelink": ("created", "deleted"),
             "project": ("created", "updated", "deleted"), "version": ("created", "updated", "released", "deleted"),
             "user": ("created", "updated", "deleted"), "filter": ("created", "updated", "deleted")},
    "jira-software": {"sprint": ("created", "started", "closed", "updated", "deleted"), "board": ("created", "updated", "deleted")},
    "jira-service-management": {"request": ("created", "updated")},
    "confluence": {"page": ("created", "updated", "deleted", "moved", "copied", "viewed"), "blogpost": ("created", "updated", "deleted"),
                   "whiteboard": ("created", "updated", "deleted"), "database": ("created", "updated", "deleted"), "folder": ("created", "updated", "deleted"),
                   "comment": ("created", "updated", "deleted"), "space": ("created", "updated", "deleted"), "attachment": ("created", "updated", "deleted"),
                   "label": ("added", "removed"), "user": ("created", "deleted"), "group": ("created", "deleted"), "search": ("performed",)},
}
DEFAULT_EVENTS = ("issue:created", "issue:updated", "issue:deleted", "comment:created", "comment:updated", "comment:deleted",
                  "page:created", "page:updated", "page:deleted", "page:moved", "blogpost:created", "blogpost:updated")
_PRODUCT_SCOPE = {"jira": "read:jira-work", "jira-software": "read:jira-work", "jira-service-management": "read:jira-work",
                  "confluence": "read:confluence-content.all"}


@dataclass(frozen=True)
class TriggerFilter:
    """The four trigger filter options (EV section 8.3)."""

    ignore_self: bool = True
    expression: str | None = None
    app_is_licensed: bool = False
    on_error: Literal["IGNORE", "RECEIVE", "RECEIVE_AND_LOG"] = "RECEIVE_AND_LOG"


@dataclass(frozen=True)
class ForgeAppSpec:
    mode: Mode = "inbox"
    products: tuple[str, ...] = ("jira", "confluence")
    events: tuple[str, ...] | Literal["default", "all"] = "default"
    filter: TriggerFilter = field(default_factory=TriggerFilter)
    realtime_panel: Literal["issue", "page"] | None = None
    realtime_global: bool = False
    app_name: str = "atlian-assiant-events"
    queue_key: str = "product-events"
    consumer_timeout_seconds: int = 600
    retention_hours: int = 72
    remote_endpoint: str | None = None          # https URL of the Python backend (mode="remote")

    def validate(self) -> None:
        unknown = [p for p in self.products if p not in EVENT_CATALOG]
        if unknown:
            raise InvalidRequestError(f"unknown products {unknown}", hint="Use: " + ", ".join(EVENT_CATALOG))
        if not 1 <= self.consumer_timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise InvalidRequestError(f"consumer timeoutSeconds must be 1..{MAX_TIMEOUT_SECONDS}", hint="Async Events consumers run for at most 900 seconds (EV section 13).")
        if self.mode == "remote" and not (self.remote_endpoint or "").startswith("https://"):
            raise InvalidRequestError("remote mode needs an https remote_endpoint", hint="Forge Remote calls your Python backend: it needs a public HTTPS URL (EV section 17); "
                                      "under a strict no-ingress policy use the inbox mode (DM-07).")
        if self.realtime_global:
            raise UnsupportedDeliveryPathError("global Realtime channels are not generated", hint="Global channels need an explicit authorization design (EV section 16); "
                                               "use the default scoped channel.")


def select_events(spec: ForgeAppSpec) -> dict[str, list[str]]:
    """Resolve ``spec.events`` to ``{product: [avi:... event names]}`` restricted to the selected products."""
    out: dict[str, list[str]] = {}
    for product in spec.products:
        catalog = EVENT_CATALOG[product]
        if spec.events == "all":
            wanted = [f"{n}:{v}" for n, vs in catalog.items() for v in vs]
        elif spec.events == "default":
            wanted = [e for e in DEFAULT_EVENTS if e.split(":")[0] in catalog and e.split(":")[1] in catalog[e.split(":")[0]]]
        else:
            wanted = [e for e in spec.events if e.split(":")[0] in catalog and e.split(":")[1] in catalog[e.split(":")[0]]]
        if wanted:
            out[product] = [f"avi:{product}:{e.split(':')[1]}:{e.split(':')[0]}" for e in wanted]
    unmatched = {e for e in (spec.events if isinstance(spec.events, tuple) else ()) if not any(e.split(":")[0] in EVENT_CATALOG[p] and
                 e.split(":")[1] in EVENT_CATALOG[p][e.split(":")[0]] for p in spec.products)}
    if unmatched:
        raise InvalidRequestError(f"events {sorted(unmatched)} are not available for products {list(spec.products)}", hint="Use noun:verb such as issue:updated or page:moved.")
    if not out:
        raise InvalidRequestError("no events selected", hint="Pick at least one event for the chosen products.")
    return out


def required_scopes(spec: ForgeAppSpec, selected: dict[str, list[str]]) -> list[str]:
    """The minimum scopes for the selected events (never broader)."""
    scopes = {_PRODUCT_SCOPE[p] for p in selected}
    if spec.mode == "inbox":
        scopes.add("storage:app")
    return sorted(scopes)


def _yaml_list(items: list[str], indent: int) -> str:
    pad = " " * indent
    return "[" + (",\n" + pad).join(items) + "]" if len(items) > 3 else "[" + ", ".join(items) + "]"


def manifest(spec: ForgeAppSpec) -> str:
    spec.validate()
    selected = select_events(spec)
    f = spec.filter
    filt = [f"ignoreSelf: {str(f.ignore_self).lower()}", f"onError: {f.on_error}", f"appIsLicensed: {str(f.app_is_licensed).lower()}"]
    if f.expression:
        filt.append(f"expression: {json.dumps(f.expression)}")
    lines = ["modules:", "  trigger:"]
    for product, events in selected.items():
        key = product.replace("-", "") + "-events"
        lines += [f"    - key: {key}", "      function: on-event", f"      events: {_yaml_list(events, 14)}", "      filter:"]
        lines += [f"        {x}" for x in filt]
    if spec.mode == "inbox":
        lines += ["  consumer:", "    - key: inbox-writer", f"      queue: {spec.queue_key}", "      function: write-inbox",
                  "  scheduledTrigger:", "    - key: purge-inbox", "      function: purge-inbox", "      interval: hour"]
    lines += ["  function:", "    - key: on-event", "      handler: src/events.onEvent"]
    if spec.mode == "inbox":
        lines += ["    - key: write-inbox", "      handler: src/inbox.write", f"      timeoutSeconds: {spec.consumer_timeout_seconds}",
                  "    - key: purge-inbox", "      handler: src/inbox.purge", "    - key: app-rest", "      handler: src/rest.handle"]
    if spec.realtime_panel:
        lines += ["  # realtime panel (DM-10): Custom UI subscribes to a SCOPED channel (EV section 16); package names unverified: VER-23",
                  f"  {'jira:issuePanel' if spec.realtime_panel == 'issue' else 'confluence:contentBylineItem'}:",
                  "    - key: live-panel", "      resource: panel", "      title: Live changes"]
    lines += ["app:", "  runtime:", "    name: nodejs22.x", f"  id: ari:cloud:ecosystem::app/REPLACE_WITH_forge_register_ID  # run `forge register` ({spec.app_name})"]
    if spec.mode == "remote":
        lines += ["remotes:", "  - key: python-backend", f"    baseUrl: {spec.remote_endpoint}"]
    scopes = required_scopes(spec, selected)
    lines += ["permissions:", "  scopes:"] + [f"    - {s}" for s in scopes]
    if spec.mode == "remote":
        lines += ["  external:", "    fetch:", "      backend:", f"        - {spec.remote_endpoint}"]
    return "\n".join(lines) + "\n"


def _events_js(spec: ForgeAppSpec) -> str:
    if spec.mode == "native":
        return """// native mode (DM-06): everything is processed INSIDE Forge; nothing leaves Atlassian.
import api, { route } from '@forge/api';

export async function onEvent(event, context) {
  console.log(JSON.stringify({ kind: 'atlian-assiant-event', eventType: event.eventType, id: context && context.installContext }));
  // Add your handler here. Use api.asApp().requestJira(route`/rest/api/3/issue/${event.issue.key}`) for fresh state.
}
"""
    if spec.mode == "remote":
        return """// remote mode (DM-09): invoke the Python backend with a Forge Invocation Token (verify it there: ForgeInvocationTokenVerifier).
import { invokeRemote } from '@forge/api';

export async function onEvent(event, context) {
  const res = await invokeRemote('python-backend', { path: '/hooks/forge', method: 'POST', headers: { 'content-type': 'application/json' },
                                                      body: JSON.stringify({ eventType: event.eventType, event, installContext: context.installContext }) });
  if (!res.ok) throw new Error('remote returned ' + res.status);   // let Forge retry
}
"""
    return f"""// inbox mode (DM-07): compact signal -> Async Queue (<= {MAX_PUSH_EVENTS} events and {MAX_PUSH_BYTES // 1024} KB per push, EV section 13).
import {{ Queue }} from '@forge/events';

const queue = new Queue({{ key: '{spec.queue_key}' }});
const MAX_EVENTS = {MAX_PUSH_EVENTS};
const MAX_BYTES = {MAX_PUSH_BYTES};

export function compact(event) {{
  const entity = event.issue || event.content || event.page || event.comment || event.project || event.version || event.sprint || {{}};
  return {{ id: event.id || `${{event.eventType}}#${{entity.id || entity.key || Date.now()}}#${{Date.now()}}`, source: event.eventType.split(':')[1],
           eventType: event.eventType, entity: {{ id: entity.id, key: entity.key }}, receivedAt: Date.now() }};
}}

export function batches(signals) {{            // never exceed the documented push limits
  const out = []; let cur = []; let size = 0;
  for (const s of signals) {{
    const n = JSON.stringify(s).length;
    if (cur.length >= MAX_EVENTS || size + n > MAX_BYTES) {{ out.push(cur); cur = []; size = 0; }}
    cur.push(s); size += n;
  }}
  if (cur.length) out.push(cur);
  return out;
}}

export async function onEvent(event, context) {{
  for (const batch of batches([compact(event)])) await queue.push(batch);
}}
"""


_INBOX_JS = """// Storage inbox (EV section 14): keys evt#<receivedAt>#<id> sort by arrival; a prefix query returns them in order.
import { storage, startsWith } from '@forge/api';

const TTL_MS = __RETENTION__ * 3600 * 1000;
const pad = (n) => String(n).padStart(15, '0');

export async function write(event) {
  const items = Array.isArray(event.payload) ? event.payload : [event.payload];
  for (const s of items) {
    await storage.set(`evt#${pad(s.receivedAt)}#${s.id}`, { source: s.source, eventType: s.eventType, entity: s.entity, receivedAt: s.receivedAt, processed: false, id: s.id });
    if (s.eventType && s.eventType.endsWith(':deleted:page')) {          // cascade note (EV section 11): the Python side invalidates descendants
      await storage.set(`del#${s.entity.id}`, { receivedAt: s.receivedAt });
    }
  }
}

export async function purge() {
  const cutoff = Date.now() - TTL_MS;
  const page = await storage.query().where('key', startsWith('evt#')).limit(100).getMany();
  for (const r of page.results) if (r.value.processed && r.value.receivedAt < cutoff) await storage.delete(r.key);
}
"""

_REST_JS = """// App REST API (Preview, EV section 15). The manifest declaration of this handler is UNVERIFIED (VER-06): adjust to the current Preview docs.
import { storage, startsWith } from '@forge/api';

const json = (status, body) => ({ statusCode: status, headers: { 'Content-Type': ['application/json'] }, body: JSON.stringify(body) });

export async function handle(request) {
  const url = new URL(request.path, 'https://app.invalid');
  const route = `${request.method} ${url.pathname}`;
  if (route === 'GET /events') {
    const limit = Math.min(Number(url.searchParams.get('limit') || 100), 500);
    let q = storage.query().where('key', startsWith('evt#')).limit(limit);
    const cursor = url.searchParams.get('cursor');
    if (cursor) q = q.cursor(cursor);
    const page = await q.getMany();
    return json(200, { events: page.results.map((r) => ({ ...r.value, key: r.key })), nextCursor: page.nextCursor || null });
  }
  if (route === 'POST /events/ack') {
    const { ids = [] } = JSON.parse(request.body || '{}');
    for (const key of ids) { const v = await storage.get(key); if (v) await storage.set(key, { ...v, processed: true }); }
    return json(200, { acked: ids.length });
  }
  const ack = url.pathname.match(/^\\/events\\/(.+)\\/ack$/);
  if (request.method === 'POST' && ack) {
    const v = await storage.get(ack[1]); if (!v) return json(404, { error: 'not found' });
    await storage.set(ack[1], { ...v, processed: true }); return json(200, { acked: 1 });
  }
  if (route === 'GET /health') return json(200, { ok: true });
  if (route === 'GET /stats') {
    const page = await storage.query().where('key', startsWith('evt#')).limit(1).getMany();
    return json(200, { oldestReceivedAt: page.results[0] ? page.results[0].value.receivedAt : null });
  }
  // Dynamic Triggers (DM-08, Preview: VER-07): /subscriptions -> /forge/installation/v2/dynamic/module, as the app. Max __MAX_DYNAMIC__ per installation.
  if (url.pathname === '/subscriptions') return json(501, { error: 'wire the Dynamic Modules API here (VER-07)' });
  return json(404, { error: 'unknown route' });
}
"""

_PANEL_JS = """// Realtime panel (DM-10): Custom UI subscribes to a SCOPED channel. The @forge/realtime API names are unverified (VER-23).
import { events } from '@forge/bridge';

export async function subscribe(contextId, onChange) {
  return events.subscribe(`changes-${contextId}`, onChange);        // scoped by context id: no global channel
}
"""


def generate(spec: ForgeAppSpec) -> dict[str, str]:
    """All files of the companion app, keyed by relative path."""
    spec.validate()
    files = {"manifest.yml": manifest(spec), "src/events.js": _events_js(spec),
             "package.json": json.dumps({"name": spec.app_name, "version": "0.1.0", "private": True, "type": "module",
                                         "dependencies": {"@forge/api": "^6.0.0", **({"@forge/events": "^2.0.0"} if spec.mode == "inbox" else {}),
                                                          **({"@forge/bridge": "^4.0.0"} if spec.realtime_panel else {})}}, indent=2) + "\n",
             "README.md": f"# {spec.app_name}\n\nGenerated by atlian-assiant (mode `{spec.mode}`). Run `forge register`, then `forge deploy -e production` and "
                          "`forge install`.\nDo not edit by hand: regenerate instead.\n"}
    if spec.mode == "inbox":
        files["src/inbox.js"] = _INBOX_JS.replace("__RETENTION__", str(spec.retention_hours))
        files["src/rest.js"] = _REST_JS.replace("__MAX_DYNAMIC__", str(MAX_DYNAMIC_MODULES))
    if spec.realtime_panel:
        files["src/panel.js"] = _PANEL_JS
    return files
