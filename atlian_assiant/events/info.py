"""Read-only descriptions of the event plane for people and agents: methods, plans and readiness checks."""
from __future__ import annotations

from pydantic import Field

from ..models.common import AtlassianModel


class DeliveryMethodInfo(AtlassianModel):
    id: str
    name: str
    kind: str
    ingress: str
    phase: str
    status: str                                   # implemented | planned
    products: list[str] = Field(default_factory=list)
    research_refs: list[str] = Field(default_factory=list)
    preview: bool = False
    legacy: bool = False
    entitlements: list[str] = Field(default_factory=list)
    docs: list[str] = Field(default_factory=list)

    def to_agent_text(self, max_chars: int = 4000) -> str:
        flags = ", ".join(f for f, on in (("preview", self.preview), ("legacy", self.legacy)) if on)
        line = (f"**{self.id}** {self.name} - {self.kind}, ingress={self.ingress}, phase {self.phase} ({self.status})"
                + (f" [{flags}]" if flags else "") + (f"; needs: {', '.join(self.entitlements)}" if self.entitlements else ""))
        return line[:max_chars]


class EventPlanSummary(AtlassianModel):
    blueprints: list[str]
    methods: list[str]
    reconciliation: list[str]
    rationale: list[str]
    warnings: list[str] = Field(default_factory=list)
    not_yet_implemented: list[str] = Field(default_factory=list)   # recommended ids whose phase has not landed

    def to_agent_text(self, max_chars: int = 4000) -> str:
        lines = ["**Event plan**", f"- blueprints: {', '.join(self.blueprints) or 'none'}", f"- fast path: {', '.join(self.methods) or 'none'}",
                 f"- correctness path (reconciliation): {', '.join(self.reconciliation)}"]
        lines += [f"- why: {r}" for r in self.rationale] + [f"- WARNING: {w}" for w in self.warnings]
        if self.not_yet_implemented:
            lines.append(f"- not implemented yet: {', '.join(self.not_yet_implemented)} (use events_list_methods for status)")
        return "\n".join(lines)[:max_chars]


class MethodCheck(AtlassianModel):
    name: str
    ok: bool
    detail: str


class MethodCheckReport(AtlassianModel):
    method_id: str
    ready: bool
    checks: list[MethodCheck]
    next_steps: list[str] = Field(default_factory=list)

    def to_agent_text(self, max_chars: int = 4000) -> str:
        lines = [f"**{self.method_id}** {'READY' if self.ready else 'NOT READY'} (read-only checks; no end-to-end probe was run)"]
        lines += [f"- [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in self.checks]
        lines += [f"- next: {s}" for s in self.next_steps]
        return "\n".join(lines)[:max_chars]


#: what each implemented method needs configured before its end-to-end probe can run: (EventPlaneConfig field, why)
METHOD_SETTINGS: dict[str, tuple[tuple[str, str], ...]] = {
    "DM-01": (("public_callback_base", "public HTTPS receiver URL"), ("probe_issue", "sandbox issue for the probe")),
    "DM-02": (("public_callback_base", "public HTTPS receiver URL"), ("probe_issue", "sandbox issue for the probe")),
    "DM-03": (("public_callback_base", "public HTTPS receiver URL"), ("probe_issue", "sandbox issue for the probe")),
    "DM-05": (),
    "DM-15": (("public_callback_base", "public HTTPS receiver URL"), ("probe_issue", "sandbox issue for the probe")),
    "DM-16": (("public_callback_base", "public HTTPS receiver URL"), ("probe_page", "sandbox page for the probe")),
    "DM-36": (("probe_issue", "sandbox issue for the probe"),),
    "DM-37": (("probe_page", "sandbox page for the probe"),),
    "DM-38": (),
}

AUTOMATION_WARNING = ("Automation rules that send issue or page fields to external domains (DM-15/16/24) must be locked down: limit who can edit rules, "
                      "send only the standard envelope, and review the destination URLs regularly (DA section 20).")


def events_summary() -> dict[str, object]:
    """The ``events`` block of ``describe()``: which methods exist, which have landed, which already have their settings."""
    from .catalog import DELIVERY_METHODS
    from .config import EventPlaneConfig

    cfg = EventPlaneConfig()
    implemented = [m.id for m in DELIVERY_METHODS if m.implemented]
    configured = [i for i in implemented if METHOD_SETTINGS.get(i) and all(getattr(cfg, f) for f, _ in METHOD_SETTINGS[i])]
    return {"implemented_methods": implemented, "planned_methods": len(DELIVERY_METHODS) - len(implemented), "configured_methods": configured,
            "note": "use events_list_methods / events_recommend_plan / events_verify_method (read-only)"}
