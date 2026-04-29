"""
Strict per-node context for codegen (Markov blanket policy).

Validates that dependency context keys align with declared DTG inputs and known contracts.
"""

from __future__ import annotations

import os
from typing import Any


def _truthy(name: str, default: str = "0") -> bool:
    v = os.environ.get(name, default).strip().lower()
    return v in ("1", "true", "yes", "on")


def validate_node_dependency_context(
    *,
    node_id: str,
    inputs_required: list[str],
    dependency_node_ids: list[str],
    resolved_keys: list[str],
    contract_ids: list[str] | None = None,
) -> list[str]:
    """
    If STRICT_MARKOV_CONTEXT=1, fail when dependency_context includes keys other than
    declared DTG dependency node ids (plus optional contract ids).

    ``resolved_keys`` are the keys of the dependency_context map (predecessor node ids).
    ``inputs_required`` lists logical artifacts; those are validated separately by DTG rules.
    """
    if not _truthy("STRICT_MARKOV_CONTEXT", "0"):
        return []
    errs: list[str] = []
    allowed = set(dependency_node_ids or [])
    allowed.update(contract_ids or [])
    for k in resolved_keys or []:
        if k in allowed or str(k).startswith("_"):
            continue
        errs.append(
            f"Node '{node_id}': dependency context key '{k}' is not a declared dependency "
            f"node id (Markov isolation; allowed: {sorted(allowed)})"
        )
    return errs


def filter_execution_payload(
    payload: dict[str, Any],
    allowed_top_level_keys: frozenset[str],
) -> dict[str, Any]:
    """Drop undeclared top-level keys from an LLM input dict (soft isolation)."""
    return {k: v for k, v in payload.items() if k in allowed_top_level_keys}
