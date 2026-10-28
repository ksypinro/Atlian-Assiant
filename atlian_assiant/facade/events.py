"""Event-plane facade namespace: explain, choose and check delivery methods (design sections 14.2, 14.16, 16.3)."""
from __future__ import annotations

from typing import Any

from ..capabilities import Deployment, Product
from ..core.errors import InvalidRequestError
from ..events import catalog
from ..events.catalog import Ingress
from ..events.config import EventPlaneConfig
from ..events.info import METHOD_SETTINGS, DeliveryMethodInfo, EventPlanSummary, MethodCheck, MethodCheckReport
from ..events.planner import HUMAN_CHANNEL_METHODS, RELAY_BY_CLOUD, EventPlaneFactory, EventRequirements
from ..events.receivers.preflight import WebhookUrlPreflight
from ..events.references import links_for
from ._namespace import Namespace

_List = list

def _info(m: catalog.DeliveryMethod) -> DeliveryMethodInfo:
    return DeliveryMethodInfo(id=m.id, name=m.name, kind=m.kind.value, ingress=m.ingress.value, phase=m.phase,
                              status="implemented" if m.implemented else "planned", products=sorted(p.value for p in m.products),
                              research_refs=list(m.research_refs), preview=m.preview, legacy=m.legacy, entitlements=list(m.entitlements),
                              docs=list(links_for(m.id)))


class EventsClient(Namespace):
    """Describe the event plane. These methods read the local catalog only: they never call Atlassian and change nothing."""

    async def list_methods(self, *, phase: str | None = None, kind: str | None = None, max_ingress: str | None = None, product: str | None = None,
                           implemented_only: bool = False) -> _List[DeliveryMethodInfo]:
        """List the numbered delivery methods (DM-01 ... DM-49): how Jira/Confluence changes and notifications can reach you.

        When to use:
            Choosing how to be notified of changes, or checking what is implemented and what each method needs (ingress, entitlements).

        Args:
            phase: Only methods of this roadmap phase, e.g. "P3".
            kind: One of machine_push, machine_pull, in_atlassian, live_ui, human, transport, guard.
            max_ingress: Hide methods needing more exposure than this: none, lan_only, managed, tunnel, required.
            product: jira, jira_software, jsm, confluence or platform.
            implemented_only: True hides methods whose phase has not landed yet.

        Returns:
            One entry per method: id, name, kind, ingress, phase, status, products, research references, docs links.

        Raises:
            InvalidRequestError: An unknown kind, ingress or product was given.

        Access: READ

        Example:
            >>> [m.id for m in await client.events.list_methods(max_ingress="none", implemented_only=True)]
        """
        if kind is not None and kind not in {k.value for k in catalog.MethodKind}:
            raise InvalidRequestError(f"unknown kind {kind!r}", hint="Use one of: " + ", ".join(k.value for k in catalog.MethodKind))
        if max_ingress is not None and max_ingress not in {i.value for i in Ingress}:
            raise InvalidRequestError(f"unknown ingress {max_ingress!r}", hint="Use one of: " + ", ".join(i.value for i in Ingress))
        if product is not None and product not in {p.value for p in Product}:
            raise InvalidRequestError(f"unknown product {product!r}", hint="Use one of: " + ", ".join(p.value for p in Product))
        out: _List[DeliveryMethodInfo] = []
        for m in catalog.DELIVERY_METHODS:
            if phase and m.phase != phase or kind and m.kind.value != kind or implemented_only and not m.implemented:
                continue
            if max_ingress and m.ingress is not Ingress.NOT_APPLICABLE and catalog.INGRESS_RANK[m.ingress] > catalog.INGRESS_RANK[Ingress(max_ingress)]:
                continue
            if product and Product(product) not in m.products:
                continue
            out.append(_info(m))
        return out

    async def recommend_plan(self, products: _List[str], *, max_ingress: str = "none", must_process_in_lan: bool = True,
                             processing_inside_atlassian_ok: bool = False, forge_allowed: bool = True, preview_features_allowed: bool = False,
                             jsm_premium_or_enterprise: bool = False, clouds: _List[str] | None = None, mcp_only_credentials: bool = False,
                             need_audit_events: bool = False, audit_webhook_entitled: bool = False, human_channels: _List[str] | None = None,
                             live_forge_ui: bool = False, data_center: bool = False) -> EventPlanSummary:
        """Recommend delivery methods for stated requirements, following the research decision guide (EV section 60).

        When to use:
            Before setting anything up: state your network constraints and get the fast path plus the reconciliation path.

        Args:
            products: jira, jira_software, jsm and/or confluence.
            max_ingress: Most exposure you accept: none (outbound-only), lan_only, managed, tunnel or required (public HTTPS receiver).
            must_process_in_lan: Event handling must happen inside your network.
            processing_inside_atlassian_ok: Handling inside Atlassian (Forge) is acceptable.
            forge_allowed: Forge apps may be installed.
            preview_features_allowed: Preview APIs (Forge App REST) are acceptable.
            jsm_premium_or_enterprise: The site has JSM Premium or Enterprise (enables the Jira Edge Connector).
            clouds: Clouds available for relays: aws, azure, gcp, cloudflare, broker.
            mcp_only_credentials: Only MCP credentials exist (then polling goes through MCP).
            need_audit_events: Organization audit/security events are required.
            audit_webhook_entitled: The org may use audit-log webhooks.
            human_channels: Also watch human notifications: email, slack, teams, sms, rss, on_call.
            live_forge_ui: A live Forge UI panel is wanted.
            data_center: The deployment is Data Center rather than Cloud.

        Returns:
            Blueprints, fast-path methods, reconciliation methods, rationale, warnings and which recommended methods are not implemented yet.

        Raises:
            InvalidRequestError: An unknown product, ingress, cloud or channel was given.

        Access: READ

        Example:
            >>> plan = await client.events.recommend_plan(["jira"], max_ingress="none")
        """
        try:
            prods = frozenset(Product(p) for p in products)
            ingress = Ingress(max_ingress)
        except ValueError as exc:
            raise InvalidRequestError(str(exc), hint="products: " + ", ".join(p.value for p in Product) + "; max_ingress: " + ", ".join(i.value for i in Ingress)) from exc
        bad = [c for c in (clouds or []) if c not in RELAY_BY_CLOUD] + [c for c in (human_channels or []) if c not in HUMAN_CHANNEL_METHODS]
        if bad:
            raise InvalidRequestError(f"unknown cloud/channel {bad}", hint=f"clouds: {sorted(RELAY_BY_CLOUD)}; channels: {sorted(HUMAN_CHANNEL_METHODS)}")
        req = EventRequirements(products=prods, deployment=Deployment.DATA_CENTER if data_center else Deployment.CLOUD, max_ingress=ingress,
                                must_process_in_lan=must_process_in_lan, processing_inside_atlassian_ok=processing_inside_atlassian_ok,
                                forge_allowed=forge_allowed, preview_features_allowed=preview_features_allowed,
                                jsm_premium_or_enterprise=jsm_premium_or_enterprise, clouds=frozenset(clouds or ()),
                                mcp_only_credentials=mcp_only_credentials, need_audit_events=need_audit_events,
                                audit_webhook_entitled=audit_webhook_entitled, human_channels=frozenset(human_channels or ()),
                                live_forge_ui=live_forge_ui)
        plan = EventPlaneFactory.recommend(req)
        every = tuple(dict.fromkeys((*plan.methods, *plan.reconciliation)))
        pending = [i for i in every if not catalog.method(i).implemented]
        return EventPlanSummary(blueprints=[b.value for b in plan.blueprints], methods=list(plan.methods), reconciliation=list(plan.reconciliation),
                                rationale=list(plan.rationale), warnings=list(plan.warnings), not_yet_implemented=pending)

    async def verify_method(self, method_id: str) -> MethodCheckReport:
        """Check, read-only, whether a delivery method is ready to set up: catalog entry, components, required settings, callback URL.

        When to use:
            After choosing a method and before provisioning it. It does NOT run the end-to-end probe (that changes a sandbox object and
            needs write access and confirmation: run the method's provisioner `verify()` for that).

        Args:
            method_id: A delivery method id such as "DM-03".

        Returns:
            The checks that passed or failed and the next steps.

        Raises:
            InvalidRequestError: The id is not in the catalog.

        Access: READ

        Example:
            >>> report = await client.events.verify_method("DM-03")
        """
        try:
            m = catalog.method(method_id.upper())
        except KeyError as exc:
            raise InvalidRequestError(f"unknown delivery method {method_id!r}", hint="Ids run DM-01 .. DM-49; call list_methods.") from exc
        checks: _List[MethodCheck] = [MethodCheck(name="catalog entry", ok=True, detail=f"{m.id} {m.name} (phase {m.phase})")]
        steps: _List[str] = []
        if not m.implemented:
            checks.append(MethodCheck(name="implemented", ok=False, detail=f"phase {m.phase} has not landed yet"))
            steps.append("Pick an implemented method (list_methods with implemented_only=true) or wait for this phase.")
            return MethodCheckReport(method_id=m.id, ready=False, checks=checks, next_steps=steps)
        for path in (m.provisioner, m.consumer, m.verifier):
            if path:
                try:
                    catalog.resolve(path)
                    checks.append(MethodCheck(name="component", ok=True, detail=path))
                except (ImportError, AttributeError) as exc:
                    checks.append(MethodCheck(name="component", ok=False, detail=f"{path}: {exc}"))
        cfg = EventPlaneConfig()
        for field_name, why in METHOD_SETTINGS.get(m.id, ()):
            value: Any = getattr(cfg, field_name)
            checks.append(MethodCheck(name=f"setting {field_name}", ok=bool(value), detail="configured" if value else f"missing: {why}"))
            if not value:
                steps.append(f"Set ATLASSIAN_EVENTS_{field_name.upper()} ({why}).")
        if cfg.public_callback_base and m.ingress is Ingress.REQUIRED:
            report = await WebhookUrlPreflight(network=False).check(cfg.public_callback_base)
            checks.append(MethodCheck(name="callback URL", ok=report.ok, detail="ok" if report.ok else "; ".join(report.problems)))
            if not report.ok:
                steps.append("Fix the callback URL, or use a relay/tunnel/pull method (see recommend_plan).")
        return MethodCheckReport(method_id=m.id, ready=all(c.ok for c in checks), checks=checks, next_steps=steps)
