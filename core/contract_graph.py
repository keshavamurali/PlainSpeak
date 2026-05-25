"""
HLIG contract-node validation (producer / consumers / schema presence).
"""

from __future__ import annotations

from typing import Any


def validate_hlig_contract_nodes(nodes: list[dict], edges: list[dict]) -> list[str]:
    """
    For each HLIG node with node_type == contract, ensure producer and consumers
    reference existing module node ids and schema object is present.
    """
    errs: list[str] = []
    node_ids = {str(n["id"]) for n in nodes if isinstance(n, dict) and n.get("id")}
    for n in nodes:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        kind = str(n.get("kind") or "").strip().lower()
        legacy_contract = (n.get("node_type") or "").lower() == "contract"
        if not (kind == "contract" or legacy_contract):
            continue
        nid = n["id"]
        # v2 contract-first path: source_of_truth is authoritative
        if isinstance(n.get("source_of_truth"), dict):
            sot = n.get("source_of_truth") or {}
            if not sot.get("uri"):
                errs.append(f"HLIG contract node '{nid}': source_of_truth.uri is required")
            impl = n.get("implemented_by")
            if impl is not None and (not isinstance(impl, list) or not all(str(x) in node_ids for x in impl)):
                errs.append(
                    f"HLIG contract node '{nid}': implemented_by must be an array of existing node ids"
                )
            continue
        prod = n.get("producer")
        if not prod or str(prod) not in node_ids:
            errs.append(f"HLIG contract node '{nid}': producer '{prod}' must reference an existing HLIG node id")
        cons = n.get("consumers")
        if not isinstance(cons, list) or not cons:
            errs.append(f"HLIG contract node '{nid}': consumers must be a non-empty array")
        elif isinstance(cons, list):
            for c in cons:
                if str(c) not in node_ids:
                    errs.append(
                        f"HLIG contract node '{nid}': consumer '{c}' must reference an existing HLIG node id"
                    )
        if "schema" not in n or not isinstance(n.get("schema"), dict):
            errs.append(f"HLIG contract node '{nid}': schema must be an object")
        if not n.get("version"):
            errs.append(f"HLIG contract node '{nid}': version is required")
        if not n.get("name"):
            errs.append(f"HLIG contract node '{nid}': name is required")

    # Soft: edges that reference contract id as from/to should use control flow through contract (informative only)
    _ = edges
    return errs


def validate_spec_modules_contracts(spec: dict[str, Any], hlig_node_ids: set[str]) -> list[str]:
    """Cross-check SPEC.contracts producer/consumers against HLIG node ids when both exist."""
    errs: list[str] = []
    if not isinstance(spec, dict):
        return errs
    for i, c in enumerate(spec.get("contracts") or []):
        if not isinstance(c, dict):
            continue
        prod = c.get("producer")
        if prod and str(prod) not in hlig_node_ids:
            errs.append(f"SPEC.contracts[{i}].producer '{prod}' not found in HLIG nodes")
        for j, cons in enumerate(c.get("consumers") or []):
            if str(cons) not in hlig_node_ids:
                errs.append(
                    f"SPEC.contracts[{i}].consumers[{j}] '{cons}' not found in HLIG nodes"
                )
    return errs
