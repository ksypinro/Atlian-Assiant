"""Parse the design document's tables into task definitions (`track seed`)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .model import DESIGN, Store, Task, TaskType


# ---------------------------------------------------------------- markdown helpers
def read_design(path: Path = DESIGN) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, start_pat: str, end_pat: str | None = None) -> str:
    m = re.search(start_pat, text, re.M)
    if not m:
        raise ValueError(f"design section not found: {start_pat}")
    rest = text[m.start():]
    if end_pat:
        e = re.search(end_pat, rest[1:], re.M)
        if e:
            rest = rest[: e.start() + 1]
    return rest


def table_rows(block: str) -> list[list[str]]:
    """First markdown table in `block`: list of rows (header excluded), cells stripped."""
    rows: list[list[str]] = []
    started = False
    for line in block.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not started:
                started = True
                continue  # header
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue
            rows.append(cells)
        elif started:
            break
    return rows


def strip_md(s: str) -> str:
    return re.sub(r"[`*]", "", s).strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def design_headings(text: str) -> set[str]:
    refs = set()
    for m in re.finditer(r"^(#{2,3}) (\d+(?:\.\d+)?)[. ]", text, re.M):
        refs.add(f"§{m.group(2)}")
    return refs


def hints(store: Store) -> dict:
    return yaml.safe_load((store.defs / "seed_hints.yaml").read_text(encoding="utf-8"))


def _first_match(pairs: list[list[str]], text: str, default: str) -> str:
    for kw, phase in pairs:
        if kw.lower() in text.lower():
            return phase
    return default


# ---------------------------------------------------------------- generators
@dataclass
class SeedResult:
    tasks: list[Task]


def seed_backends(text: str, h: dict) -> list[Task]:
    out = []
    for m in re.finditer(r"^### (10\.\d+) `([A-Z_]+)` — (.+)$", text, re.M):
        ref, kind, title = m.groups()
        phase, ws = h["backends"][kind]
        low = kind.lower()
        out.append(Task(
            id=f"BE.{low}", title=f"Backend {kind}: skeleton, factory, availability(), health(), supported_operations()",
            phase=phase, workstream=ws, design_refs=[f"§{ref}", "§12.1"],
            deliverables=[f"atlassian_client/backends/{low}/"],
            acceptance=[f"{kind} BackendFactory registered in the BackendRegistry (§12.1)",
                        "availability() reports a precise reason when dependencies/credentials are missing",
                        "supported_operations() reflects what is actually implemented (feeds the §11 matrix)",
                        f"Unavailable backend raises BackendUnavailableError without sending anything ({title.strip()})"],
            checks=[f"pytest tests/backends/test_{low}_factory.py -q"],
            depends_on=["P0.CORE.014", "P0.CORE.015"] if kind != "REST" else ["P0.CORE.003", "P0.CORE.014", "P0.CORE.015"],
            source="design:§10",
        ))
    return out


def seed_protocols(text: str, h: dict) -> list[Task]:
    sec = section(text, r"^## 9\. ", r"^## 10\. ")
    names = [n for n in re.findall(r"^class (\w+)\(Protocol\):", sec, re.M) if n != "Backend"]
    out = []
    for n in names:
        phase = h["protocol_phases"][n]
        out.append(Task(
            id=f"PROTO.{n}", title=f"Contract suite for {n}", type=TaskType.TEST, phase=phase,
            workstream="PROTO", design_refs=["§9", "§18"],
            deliverables=[f"tests/contract/test_{slug(n).replace('-', '_')}.py", f"atlassian_client/protocols/ ({n})"],
            acceptance=[f"{n} Protocol and its models exist exactly as specified in §9",
                        "Suite is parametrized over every backend that claims the operations; FakeBackend passes",
                        "Errors, pagination (opaque cursors) and Raises sections of the contract are asserted"],
            checks=[f"pytest tests/contract/test_{slug(n).replace('-', '_')}.py -q"],
            depends_on=["P0.QA.001", "P0.PROTO.001"], source="design:§9",
        ))
    return out


def seed_matrix(text: str, h: dict) -> list[Task]:
    sec = section(text, r"^## 11\. ", r"^## 12\. ")
    lines = [l for l in sec.splitlines() if l.startswith("|")]
    header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    cols = [h["matrix_columns"][c] for c in header[1:]]
    out = []
    for row in table_rows(sec):
        label = strip_md(row[0])
        if label not in h["matrix_rows"]:
            raise ValueError(f"matrix row {label!r} missing from seed_hints.matrix_rows")
        spec = h["matrix_rows"][label]
        for backend, cell in zip(cols, row[1:]):
            if not ("✅" in cell or "◐" in cell):
                continue
            via_org = "ORG_ADMIN" in cell
            read_phase, write_phase = h["backend_phases"][backend]
            phase = spec.get("phase") or (read_phase if spec["access"] in ("read", "search") else write_phase)
            if backend not in ("rest", "mcp"):
                phase = "P5"
            if backend == "rest" and spec.get("phase"):
                phase = spec["phase"]
            partial = cell.startswith("◐")
            dep = "BE.org_admin" if via_org else f"BE.{backend}"
            ws_override = "ORG" if via_org else None
            out.append(Task(
                id=f"MX.{backend}.{spec['slug']}", title=f"{label} on {backend}" + (" (via ORG_ADMIN backend)" if via_org else "") + (" (partial)" if partial else ""),
                phase=phase, workstream=ws_override or {"rest": "REST", "rest_dc": "DC", "mcp": "MCP", "graphql": "GQL", "acli": "CLI",
                                         "twg": "CLI", "jira_cli": "CLI", "browser": "BROWSER", "data_lake": "LAKE"}[backend],
                design_refs=["§11", "§10"],
                depends_on=[dep],
                deliverables=[f"atlassian_client/backends/{backend}/"],
                acceptance=[f"Matrix cell '{label}' x {backend} is {'partial' if partial else 'fully'} implemented as in §11 ({cell})",
                            f"supported_operations() of the {backend} backend lists the operation ids for this row",
                            "Passes the shared protocol contract tests for those operations"],
                checks=[f"pytest tests/contract -k '{spec['slug'].replace('-', '_')} and {backend}' -q"],
                size="S", source="design:§11",
            ))
    return out


def seed_dm(text: str, h: dict) -> list[Task]:
    sec = section(text, r"^### 14\.2 ", r"^### 14\.3 ")
    ledger = section(sec, r"^\| ID \| Delivery method", r"^\*\*Cross-cutting rules")
    out = []
    for row in table_rows(ledger):
        if not row[0].startswith("DM-"):
            continue
        dm, name, evref, prov, cons, ingress, phase = row[:7]
        n = int(dm[3:])
        name_c = strip_md(name)
        ws = "NOTIFY" if phase in h["notify_phases"] and 20 <= n <= 35 else ("FORGE" if "Forge" in name_c else "EVENTS")
        refs = [f"EV {r.strip()}" for r in evref.split(",")]
        guard = "Guard" in prov
        base = dict(phase=phase, workstream=ws, design_refs=["§14.2", "§14.3", "§14.4"], research_refs=refs, source="design:§14.2")
        test = f"tests/events/methods/test_dm_{n:02d}.py"
        verify = f"atlassian_client.events.verify:verify_dm_{n:02d}"
        if guard:
            out.append(Task(id=dm, title=f"{dm} {name_c} - explicit guard", **base,
                            depends_on=["P3.EVENTS.003"],
                            deliverables=["atlassian_client/events/guards.py", test],
                            acceptance=[f"Requesting this path raises UnsupportedDeliveryPathError with the hint in §14.17",
                                        "Catalog entry DM exists with kind GUARD", "Downstream replacement (if any) is implemented"],
                            checks=[f"pytest {test} -q"], size="S"))
            continue
        if "`" in prov:
            out.append(Task(id=f"{dm}.P", title=f"{dm} provisioner: {name_c}", **base,
                            depends_on=["P3.EVENTS.002"],
                            deliverables=[f"atlassian_client/events/provision/ ({strip_md(prov)})"],
                            acceptance=[f"Provisioner plans/applies the Atlassian-side setup: {strip_md(prov)}",
                                        "GUIDED mode always available; API mode where a documented API exists",
                                        "apply() needs confirmation; teardown() reverses where possible"],
                            checks=[f"pytest {test} -k provision -q"]))
        out.append(Task(id=f"{dm}.C", title=f"{dm} consumer, verifier and tests: {name_c}", **base,
                        depends_on=["P3.EVENTS.003", "P3.EVENTS.004"] + ([f"{dm}.P"] if "`" in prov else []),
                        deliverables=[f"atlassian_client/events/ ({strip_md(cons)})", test, verify],
                        acceptance=[f"Consumer implemented: {strip_md(cons)}", "Native events normalized (§14.11), dedup key as in §14.4",
                                    f"{verify.split(':')[1]} end-to-end probe returns a VerificationReport",
                                    f"Catalog entry {dm} importable; contract test passes offline"],
                        checks=[f"pytest {test} -q"]))
    return out


def seed_cc(text: str, h: dict) -> list[Task]:
    sec = section(text, r"^\*\*Cross-cutting rules\*\*", r"^### 14\.3 ")
    out = []
    for row in table_rows(sec):
        if not row[0].startswith("CC-"):
            continue
        cc, rule, evref, impl = row[:4]
        phase = h["cc_phases"]["overrides"].get(cc, h["cc_phases"]["default"])
        out.append(Task(
            id=cc, title=f"{cc} {strip_md(rule)}", phase=phase, workstream="EVENTS",
            design_refs=["§14.2"], research_refs=[f"EV {r.strip()}" for r in evref.split(",")],
            depends_on=["P3.EVENTS.003"],
            deliverables=[strip_md(impl)],
            acceptance=[f"Implemented by: {strip_md(impl)}", "Entry CC exists in CROSS_CUTTING and its implemented_by paths import"],
            checks=[f"pytest tests/events/test_cross_cutting.py -k {cc.lower().replace('-', '_')} -q"], size="S",
            source="design:§14.2"))
    return out


def seed_tools(text: str, h: dict) -> list[Task]:
    sec = section(text, r"^### 16\.3 ", r"^### 16\.4 ")
    out, n = [], 0
    tp = h["tool_phases"]
    for row in table_rows(sec):
        names = re.findall(r"`([a-z_]+)`", row[0])
        access = strip_md(row[1]).split()[0].split("/")[0] if row[1] else "READ"
        for name in names:
            n += 1
            phase = tp["by_tool"].get(name) or tp["by_access"].get(access) or tp["default"]
            ws = "AGENT"
            out.append(Task(
                id=f"TOOL-{n:02d}", title=f"Agent tool {name} ({strip_md(row[1])})", phase=phase, workstream=ws,
                design_refs=["§16.2", "§16.3"], depends_on=["P1.AGENT.001"],
                deliverables=[f"atlassian_client/facade/ (@agent_tool {name})"],
                acceptance=[f"Tool '{name}' is registered, schema generated from the signature",
                            "Docstring has summary, When to use, Args, Returns, Raises, Access, Backends, Example",
                            "Policy: access class enforced; writes support dry_run + confirmation",
                            f"Purpose: {strip_md(row[2])}"],
                checks=[f"pytest tests/agent/test_tools.py -k {name} -q"], size="S", source="design:§16.3"))
    return out


def _table_after(text: str, sec_start: str, sec_end: str, header_hint: str) -> list[list[str]]:
    return table_rows(section(text, sec_start, sec_end))


def seed_security(text: str, h: dict) -> list[Task]:
    out = []
    for i, row in enumerate(table_rows(section(text, r"^## 17\. ", r"^## 18\. ")), 1):
        concern = strip_md(row[0])
        phase = _first_match(h["security_phases"], concern, "P2")
        out.append(Task(
            id=f"SEC-{i:02d}", title=f"Security control: {concern}", type=TaskType.SECURITY, phase=phase,
            workstream="SEC", design_refs=["§17"], human_only=True,
            acceptance=[f"Control implemented: {strip_md(row[1])}", "Reviewer confirms the control in code and in a test",
                        "Evidence file lists the test(s) and file(s) that enforce it"],
            checks=[f"pytest tests/security -k sec_{i:02d} -q"], size="S", source="design:§17"))
    return out


def seed_qa(text: str, h: dict) -> list[Task]:
    out = []
    for i, row in enumerate(table_rows(section(text, r"^## 18\. ", r"^## 19\. ")), 1):
        layer = strip_md(row[0])
        phase = _first_match(h["qa_phases"], layer, "P0")
        out.append(Task(
            id=f"QA.{i:02d}", title=f"Test layer: {layer}", type=TaskType.TEST, phase=phase, workstream="QA",
            design_refs=["§18"], depends_on=["P0.QA.001"],
            deliverables=["tests/"], acceptance=[f"Test layer in place: {strip_md(row[1])[:200]}"],
            checks=[f"pytest tests -m 'layer_{i:02d}' -q"], size="S", source="design:§18"))
    return out


def seed_verify(text: str, h: dict) -> list[Task]:
    out = []
    for row in table_rows(section(text, r"^## 22\. ", None)):
        if not row[0].isdigit():
            continue
        n = int(row[0])
        out.append(Task(
            id=f"VER-{n:02d}", title=f"Verify against live docs: {strip_md(row[1])}", type=TaskType.RESEARCH,
            phase=h["verify_phases"][n], workstream="VER", design_refs=["§22"], needs="sandbox", human_only=True,
            acceptance=[f"Behaviour confirmed (or corrected) against live Atlassian documentation/tenant",
                        f"Result recorded in evidence; component updated if different: {strip_md(row[2])}"],
            checks=[], size="S", source="design:§22"))
    return out


def seed_gates(store: Store) -> list[Task]:
    out = []
    for pid, ph in store.load_phases().items():
        out.append(Task(id=f"GATE.{pid}", title=f"Phase gate {pid}: {ph.name}", type=TaskType.GATE, phase=pid, workstream="REL",
                        design_refs=["§20"], human_only=True,
                        acceptance=[ph.exit_criteria] + [f"Machine: {c}" for c in ph.machine_checks] + [f"Human: {x}" for x in ph.human_items],
                        checks=[], size="S", source="design:§20"))
    return out


def build_all(store: Store | None = None) -> list[Task]:
    store = store or Store()
    text, h = read_design(), hints(store)
    tasks = (seed_backends(text, h) + seed_protocols(text, h) + seed_matrix(text, h) + seed_dm(text, h) + seed_cc(text, h)
             + seed_tools(text, h) + seed_security(text, h) + seed_qa(text, h) + seed_verify(text, h) + seed_gates(store))
    ids = [t.id for t in tasks]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"duplicate generated ids: {sorted(dup)}")
    return tasks
