"""
Validate HLIG/DTG graphs against the language spec in language_readme.md.

Supports:
- Top-level { "nodes", "edges" } (saved graph from HLIGGraph.to_dict())
- Wrapped { "hlig": { "nodes", "edges" } } or full { "project", "hlig": {...} }
"""

from __future__ import annotations

import os
import re
from typing import Any

from core.contract_graph import validate_hlig_contract_nodes, validate_spec_modules_contracts
from core.dtg_task_split import files_owned_max
from core.expansion_engine import (
    DTG_LOGICAL_TYPES,
    EXPANSION_STRATEGIES,
    canonical_expansion_strategy,
    effective_dtg_type,
    needs_expansion_strategy,
)

try:
    import networkx as nx
except ImportError:
    nx = None  # type: ignore[assignment]


# --- Spec constants (from language_readme.md) ---------------------------------

HLIG_ID_PATTERN = re.compile(r"^HLIG-[A-Za-z0-9_-]+(?:-HLIG-[A-Za-z0-9_-]+)*$")
DTG_ID_PATTERN = re.compile(r"^(?:HLIG-[A-Za-z0-9_-]+-)*DTG-[A-Za-z0-9_-]+$")
CONTRACT_ID_PATTERN = re.compile(r"^(?:HLIG-[A-Za-z0-9_-]+-)*CONTRACT-[A-Za-z0-9_-]+$")

HLIG_REQUIRED_NODE = {"id", "task", "inputs", "outputs"}
HLIG_OPTIONAL_NODE = {"language", "external_interfaces", "dtg_root", "dtg", "kind", "child_graph", "inputs_required", "outputs_produced"}
HLIG_EXTERNAL_INTERFACES = {"API", "DB", "Filesystem", "Auth", "message", "None"}

DTG_REQUIRED_NODE = {
    "id",
    "title",
    "description",
    "task_type",
    "inputs_required",
    "outputs_produced",
    "dependencies",
    "success_criteria",
}
DTG_OPTIONAL_NODE = {
    "node_type",
    "output_descriptions",
    "execution_spec",
    "parent_hlig",
    "language",
    "files_owned",
    "expansion_strategy",
    "type",
    "section",
    "implementation",
    "on_failure",
    "test_scope",
    "target_node_ids",
    "failure_log_artifact",
    "kind",
}
DTG_TASK_TYPES = {
    "design",
    "contract",
    "scaffold",
    "code",
    "review",
    "test",
    "integration",
    "documentation",
    "verification",
    "build",
}
DTG_NODE_TYPES = {"reasoning", "design", "coding", "evaluation", "tool", "contract"}
EDGE_TYPES = {"control", "data"}
DEPENDENCY_TYPES = {"strict", "soft", "data-flow"}


def _normalize_graph(data: dict) -> tuple[list, list]:
    """Return (nodes, edges) from various top-level shapes."""
    if "nodes" in data and "edges" in data:
        return data["nodes"], data["edges"]
    graph = data.get("graph")
    if isinstance(graph, dict):
        return graph.get("nodes", []), graph.get("edges", [])
    hlig = data.get("hlig")
    if isinstance(hlig, dict):
        return hlig.get("nodes", []), hlig.get("edges", [])
    return [], []


def _node_kind(node: dict[str, Any]) -> str:
    kind = str(node.get("kind") or "").strip().lower()
    if kind:
        return kind
    node_type = str(node.get("node_type") or "").strip().lower()
    if node_type == "contract":
        return "contract"
    nid = str(node.get("id") or "").upper()
    if "-DTG-" in nid or nid.startswith("DTG-"):
        return "atomic"
    if "-CONTRACT-" in nid:
        return "contract"
    return "composite"


def _check_acyclic(nodes: list[dict], edges: list[dict], node_id_key: str = "id") -> list[str]:
    """Build directed graph from edges (from/to), check acyclicity. Returns list of errors."""
    errs: list[str] = []
    if not nx:
        return errs  # skip acyclicity if NetworkX not available
    g = nx.DiGraph()
    node_ids = {n.get(node_id_key) for n in nodes if n.get(node_id_key)}
    for e in edges:
        u = e.get("from") or e.get("source")
        v = e.get("to") or e.get("target")
        if u and v:
            g.add_edge(u, v)
    try:
        list(nx.topological_sort(g))
    except nx.NetworkXError as ex:
        try:
            cycle = list(nx.find_cycle(g))
            errs.append(f"Graph contains a cycle: {' -> '.join(str(u) for u, _ in cycle)} -> {cycle[0][0]}")
        except Exception:
            errs.append(f"Graph is not acyclic: {ex}")
    return errs


def _snake_case_like(s: str) -> bool:
    """Heuristic: canonical names should be snake_case (lowercase, underscores)."""
    if not s or not s.strip():
        return False
    # Allow only [a-z0-9_]
    return bool(re.match(r"^[a-z0-9_]+$", s.strip()))


def validate_hlig(data: dict, strict_canonical_names: bool = False) -> list[str]:
    """
    Validate HLIG graph against language_readme.md Section 3.
    Returns list of error/warning strings (empty if valid).
    """
    nodes, edges = _normalize_graph(data)
    errs: list[str] = []

    if not nodes:
        errs.append("HLIG has no nodes")
        return errs

    node_ids = set()
    for i, n in enumerate(nodes):
        nid = n.get("id")
        if not nid:
            errs.append(f"HLIG node at index {i} missing 'id'")
            continue
        kind = _node_kind(n)
        if kind == "composite" and not HLIG_ID_PATTERN.match(nid):
            errs.append(f"HLIG node id '{nid}' does not match recursive HLIG pattern")
        if kind == "contract" and not (CONTRACT_ID_PATTERN.match(nid) or HLIG_ID_PATTERN.match(nid)):
            errs.append(f"Contract node id '{nid}' should follow recursive CONTRACT pattern")
        if nid in node_ids:
            errs.append(f"Duplicate HLIG node id '{nid}'")
        node_ids.add(nid)

        if kind == "contract":
            if n.get("source_of_truth") is not None:
                sot = n.get("source_of_truth")
                if not isinstance(sot, dict) or not sot.get("uri"):
                    errs.append(f"HLIG contract node '{nid}': source_of_truth must be an object with 'uri'")
            else:
                c_missing = []
                for fld in ("name", "producer", "version"):
                    if not n.get(fld):
                        c_missing.append(fld)
                if not isinstance(n.get("consumers"), list) or not n.get("consumers"):
                    c_missing.append("consumers")
                if "schema" not in n or not isinstance(n.get("schema"), dict):
                    c_missing.append("schema")
                if c_missing:
                    errs.append(
                        f"HLIG contract node '{nid}' missing or invalid fields: {sorted(set(c_missing))}"
                    )
            continue

        if kind == "atomic":
            # v2 DTG-as-atomic nodes inside child_graph
            req_atomic = {"title", "description", "task_type", "inputs_required", "outputs_produced", "dependencies", "success_criteria"}
            missing_atomic = [f for f in req_atomic if f not in n]
            if missing_atomic:
                errs.append(f"Atomic node '{nid}' missing required fields: {sorted(missing_atomic)}")
            if not DTG_ID_PATTERN.match(nid):
                errs.append(f"Atomic node id '{nid}' does not match recursive DTG pattern")
            tt = n.get("task_type")
            if tt and tt not in DTG_TASK_TYPES:
                errs.append(f"Atomic node '{nid}': task_type '{tt}' not in {DTG_TASK_TYPES}")
            continue

        # v2 composite supports inputs_required/outputs_produced aliases
        missing = set()
        if "task" not in n:
            missing.add("task")
        has_inputs = "inputs" in n or "inputs_required" in n
        has_outputs = "outputs" in n or "outputs_produced" in n
        if not has_inputs:
            missing.add("inputs|inputs_required")
        if not has_outputs:
            missing.add("outputs|outputs_produced")
        if missing:
            errs.append(f"HLIG node '{nid}' missing required fields: {sorted(missing)}")

        inputs_val = n.get("inputs") if "inputs" in n else n.get("inputs_required")
        outputs_val = n.get("outputs") if "outputs" in n else n.get("outputs_produced")
        if inputs_val is not None and not isinstance(inputs_val, list):
            errs.append(f"HLIG node '{nid}': inputs/inputs_required must be an array")
        if outputs_val is not None and not isinstance(outputs_val, list):
            errs.append(f"HLIG node '{nid}': outputs/outputs_produced must be an array")

        if strict_canonical_names:
            for label, vals in [("inputs", inputs_val or []), ("outputs", outputs_val or [])]:
                for val in vals:
                    if isinstance(val, str) and not _snake_case_like(val):
                        errs.append(
                            f"HLIG node '{nid}': {label} should use canonical names (snake_case); got '{val}'"
                        )

        ext = n.get("external_interfaces")
        if ext is not None and not isinstance(ext, list):
            errs.append(f"HLIG node '{nid}': 'external_interfaces' must be an array")
        elif isinstance(ext, list):
            for v in ext:
                if v not in HLIG_EXTERNAL_INTERFACES:
                    errs.append(
                        f"HLIG node '{nid}': external_interface '{v}' not in spec set {HLIG_EXTERNAL_INTERFACES}"
                    )

    for j, e in enumerate(edges):
        u = e.get("from") or e.get("source")
        v = e.get("to") or e.get("target")
        if not u or not v:
            errs.append(f"HLIG edge at index {j} missing 'from'/'to' (or 'source'/'target')")
            continue
        if u not in node_ids:
            errs.append(f"HLIG edge references non-existent source node '{u}'")
        if v not in node_ids:
            errs.append(f"HLIG edge references non-existent target node '{v}'")
        edge_type = e.get("edge_type")
        if edge_type and edge_type not in EDGE_TYPES:
            errs.append(f"HLIG edge {u} -> {v}: edge_type '{edge_type}' not in {EDGE_TYPES}")

    errs.extend(_check_acyclic(nodes, edges, "id"))

    errs.extend(validate_hlig_contract_nodes(nodes, edges))

    return errs


def _validate_recursive_child_graph(nodes: list[dict], errs: list[str], strict_canonical_names: bool) -> None:
    for n in nodes:
        if not isinstance(n, dict):
            continue
        child = n.get("child_graph")
        if child is None:
            continue
        if not isinstance(child, dict):
            errs.append(f"Node '{n.get('id', '?')}': child_graph must be an object")
            continue
        cnodes = child.get("nodes", [])
        cedges = child.get("edges", [])
        if not isinstance(cnodes, list) or not isinstance(cedges, list):
            errs.append(f"Node '{n.get('id', '?')}': child_graph must contain arrays 'nodes' and 'edges'")
            continue
        sub_errs = validate_hlig({"nodes": cnodes, "edges": cedges}, strict_canonical_names=strict_canonical_names)
        errs.extend([f"[child_graph {n.get('id', '?')}] {e}" for e in sub_errs])
        _validate_recursive_child_graph(cnodes, errs, strict_canonical_names)


def contract_first_warnings(nodes: list[dict]) -> list[str]:
    """
    Warn when a code-logical node depends only on other code-logical nodes (no contract/design in deps).
    Soft policy; does not fail validation.
    """
    by_id = {n.get("id"): n for n in nodes if n.get("id")}
    warns: list[str] = []
    for n in nodes:
        nid = n.get("id")
        if not nid or effective_dtg_type(n) != "code":
            continue
        deps = n.get("dependencies") or []
        if not deps:
            continue
        dep_nodes = [by_id[d] for d in deps if d in by_id]
        if len(dep_nodes) != len(deps):
            continue
        impl_only = {"code", "scaffold"}
        if dep_nodes and all(effective_dtg_type(dn) in impl_only for dn in dep_nodes):
            warns.append(
                f"DTG node '{nid}': code node depends only on code/scaffold nodes; "
                "prefer a contract or design node between modules (if applicable — contract-first)."
            )
    return warns


def files_owned_policy_warnings(nodes: list[dict]) -> list[str]:
    """Soft policy: prefer bounded files_owned on code nodes (PART 5)."""
    warns: list[str] = []
    cap = files_owned_max()
    for n in nodes:
        nid = n.get("id", "?")
        if effective_dtg_type(n) != "code":
            continue
        fo = n.get("files_owned")
        if not isinstance(fo, list):
            continue
        nfiles = len([p for p in fo if isinstance(p, str) and p.strip()])
        if nfiles > cap:
            warns.append(
                f"DTG node '{nid}': files_owned has {nfiles} files (recommended max {cap}; "
                f"see core.dtg_task_split.split_large_task)"
            )
    return warns


def section_warnings(nodes: list[dict]) -> list[str]:
    """Recommend non-empty section for readability (prompts/dtg_generator.md)."""
    warns: list[str] = []
    for n in nodes:
        nid = n.get("id", "?")
        sec = n.get("section")
        if sec is None or (isinstance(sec, str) and not sec.strip()):
            warns.append(
                f"DTG node '{nid}': missing or empty 'section' (recommended for grouped, readable DTGs)."
            )
    return warns


def validate_dtg(dtg: dict, hlig_node_id: str | None = None) -> list[str]:
    """
    Validate a single DTG (hlig_node_id, nodes, edges) against language_readme.md Section 4.
    Returns list of error/warning strings.
    """
    nodes = dtg.get("nodes", [])
    edges = dtg.get("edges", [])
    errs: list[str] = []

    if not nodes:
        errs.append("DTG has no nodes")
        return errs

    node_ids = set()
    outputs_by_node: dict[str, set[str]] = {}
    for i, n in enumerate(nodes):
        nid = n.get("id")
        if not nid:
            errs.append(f"DTG node at index {i} missing 'id'")
            continue
        if not DTG_ID_PATTERN.match(nid):
            errs.append(f"DTG node id '{nid}' does not match pattern DTG-{{H}}-{{N}}")
        if nid in node_ids:
            errs.append(f"Duplicate DTG node id '{nid}'")
        node_ids.add(nid)

        missing = DTG_REQUIRED_NODE - set(n.keys())
        if missing:
            errs.append(f"DTG node '{nid}' missing required fields: {sorted(missing)}")

        tt = n.get("task_type")
        if tt and tt not in DTG_TASK_TYPES:
            errs.append(f"DTG node '{nid}': task_type '{tt}' not in {DTG_TASK_TYPES}")

        nt = n.get("node_type")
        if nt and nt not in DTG_NODE_TYPES:
            errs.append(f"DTG node '{nid}': node_type '{nt}' not in {DTG_NODE_TYPES}")

        ty = n.get("type")
        if ty is not None:
            if not isinstance(ty, str) or not str(ty).strip():
                errs.append(f"DTG node '{nid}': 'type' must be a non-empty string when present")
            elif str(ty).strip().lower() not in DTG_LOGICAL_TYPES:
                errs.append(
                    f"DTG node '{nid}': type '{ty}' not in {sorted(DTG_LOGICAL_TYPES)}"
                )

        if needs_expansion_strategy(n):
            es = n.get("expansion_strategy")
            if not es or not isinstance(es, str) or not es.strip():
                errs.append(
                    f"DTG node '{nid}': expansion_strategy is required for logical type "
                    f"'{effective_dtg_type(n)}' (code/test/build/scaffold)"
                )
            elif canonical_expansion_strategy(es.strip()) not in EXPANSION_STRATEGIES:
                errs.append(
                    f"DTG node '{nid}': expansion_strategy '{es}' not in "
                    f"{sorted(EXPANSION_STRATEGIES)} (aliases allowed: frontend_standard, backend_standard, db_schema_standard)"
                )

        if effective_dtg_type(n) == "code":
            fo = n.get("files_owned")
            if isinstance(fo, list):
                nfiles = len([p for p in fo if isinstance(p, str) and p.strip()])
                cap = files_owned_max()
                if nfiles > cap and os.environ.get("STRICT_DTG_FILE_COUNT", "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                ):
                    errs.append(
                        f"DTG node '{nid}': files_owned has {nfiles} paths; "
                        f"policy max is {cap} (split with core.dtg_task_split.split_large_task)"
                    )

        out = n.get("outputs_produced")
        if isinstance(out, list):
            outputs_by_node[nid] = set(out)
        else:
            outputs_by_node[nid] = set()

    # inputs_required must match outputs_produced of some dependency node (spec 4.4)
    for n in nodes:
        nid = n.get("id")
        deps = n.get("dependencies") or []
        in_req = list(n.get("inputs_required") or [])
        for dep in deps:
            if dep not in node_ids:
                errs.append(f"DTG node '{nid}' dependency '{dep}' is not a node in this DTG")
        produced_by_deps = set()
        for d in deps:
            produced_by_deps |= outputs_by_node.get(d, set())
        for inp in in_req:
            if inp not in produced_by_deps and deps:
                errs.append(
                    f"DTG node '{nid}' inputs_required '{inp}' is not in any dependency's outputs_produced"
                )

    # files_owned: no file may appear in more than one node (FILE OWNERSHIP RULE)
    file_to_node: dict[str, str] = {}
    for n in nodes:
        nid = n.get("id", "")
        fo = n.get("files_owned")
        if not isinstance(fo, list):
            continue
        for path in fo:
            if not isinstance(path, str) or not path.strip():
                continue
            p = path.strip()
            if p in file_to_node and file_to_node[p] != nid:
                errs.append(
                    f"DTG node '{nid}' files_owned contains '{p}' which is already owned by node '{file_to_node[p]}' (each file must have exactly one owner)"
                )
            else:
                file_to_node[p] = nid

    for j, e in enumerate(edges):
        u = e.get("from") or e.get("source")
        v = e.get("to") or e.get("target")
        if not u or not v:
            errs.append(f"DTG edge at index {j} missing 'from'/'to'")
            continue
        if u not in node_ids:
            errs.append(f"DTG edge references non-existent source node '{u}'")
        if v not in node_ids:
            errs.append(f"DTG edge references non-existent target node '{v}'")
        edge_type = e.get("edge_type")
        if edge_type and edge_type not in EDGE_TYPES:
            errs.append(f"DTG edge {u} -> {v}: edge_type '{edge_type}' not in {EDGE_TYPES}")
        dep_type = e.get("dependency_type")
        if dep_type and dep_type not in DEPENDENCY_TYPES:
            errs.append(f"DTG edge {u} -> {v}: dependency_type '{dep_type}' not in {DEPENDENCY_TYPES}")

    errs.extend(_check_acyclic(nodes, edges, "id"))

    return errs


def validate_graph(data: dict, strict_canonical_names: bool = False) -> dict[str, Any]:
    """
    Validate full graph (HLIG + any embedded DTGs).
    Returns dict with keys: ok (bool), errors (list), warnings (list), hlig_errors, dtg_errors_by_node.
    """
    result: dict[str, Any] = {
        "ok": True,
        "errors": [],
        "warnings": [],
        "hlig_errors": [],
        "dtg_errors_by_node": {},
    }

    nodes, edges = _normalize_graph(data)
    if not nodes and "hlig" not in data and "nodes" not in data:
        result["errors"].append("No HLIG nodes or hlig object found")
        result["ok"] = False
        return result

    if nodes or edges:
        hlig_data = {"nodes": nodes, "edges": edges}
        result["hlig_errors"] = validate_hlig(hlig_data, strict_canonical_names=strict_canonical_names)
        _validate_recursive_child_graph(nodes, result["hlig_errors"], strict_canonical_names=strict_canonical_names)
        if result["hlig_errors"]:
            result["errors"].extend(result["hlig_errors"])
            result["ok"] = False

    # Validate embedded DTGs in HLIG nodes
    for n in nodes or []:
        nid = n.get("id")
        dtg = n.get("dtg")
        if not isinstance(dtg, dict):
            continue
        dtg_errs = validate_dtg(dtg, hlig_node_id=nid)
        if dtg_errs:
            result["dtg_errors_by_node"][nid or "?"] = dtg_errs
            result["errors"].extend([f"[DTG {nid}] {e}" for e in dtg_errs])
            result["ok"] = False
        dnodes = dtg.get("nodes") or []
        if isinstance(dnodes, list):
            prefix = f"[DTG {nid}] " if nid else ""
            for w in contract_first_warnings(dnodes):
                result["warnings"].append(prefix + w)
            for w in section_warnings(dnodes):
                result["warnings"].append(prefix + w)
            for w in files_owned_policy_warnings(dnodes):
                result["warnings"].append(prefix + w)

    spec = data.get("spec")
    node_ids_set = {str(n.get("id")) for n in (nodes or []) if isinstance(n, dict) and n.get("id")}
    if isinstance(spec, dict):
        try:
            from spec.spec_models import validate_spec

            for e in validate_spec(spec):
                result["warnings"].append(f"SPEC validation: {e}")
        except ImportError:
            pass
        for e in validate_spec_modules_contracts(spec, node_ids_set):
            result["warnings"].append(f"SPEC/HLIG alignment: {e}")

    return result


def validate_graph_file(path: str | None, strict_canonical_names: bool = False) -> dict[str, Any]:
    """Load JSON graph from path and validate. path=None reads from stdin."""
    import json

    if path:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        import sys
        data = json.load(sys.stdin)
    return validate_graph(data, strict_canonical_names=strict_canonical_names)


if __name__ == "__main__":
    import sys
    args = [a for a in (sys.argv or [])[1:] if a != "--strict"]
    strict = "--strict" in (sys.argv or [])
    p = args[0] if args else None
    r = validate_graph_file(p, strict_canonical_names=strict)
    print("Valid" if r["ok"] else "Invalid")
    for e in r["errors"]:
        print(f"  ERROR: {e}")
    for w in r["warnings"]:
        print(f"  WARN: {w}")
    sys.exit(0 if r["ok"] else 1)
