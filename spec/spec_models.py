"""
Structured SPEC (pre-HLIG). Validated JSON derived from user intent.

Backward compatibility: if the planner omits `spec`, the runner synthesizes a minimal
SPEC from `original_query` + HLIG modules (see `generate_spec_from_intent`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SCHEMA_PATH = Path(__file__).resolve().parent / "spec_schema.json"


def load_spec_schema() -> dict[str, Any] | None:
    try:
        raw = _SCHEMA_PATH.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None


def _basic_validate(spec: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["SPEC must be a JSON object"]
    for key in ("intent", "modules", "contracts", "features", "constraints"):
        if key not in spec:
            errs.append(f"SPEC missing required key: {key}")
    intent = spec.get("intent")
    if intent is not None and (not isinstance(intent, str) or not intent.strip()):
        errs.append("SPEC.intent must be a non-empty string")
    for label in ("modules", "contracts", "features"):
        val = spec.get(label)
        if val is not None and not isinstance(val, list):
            errs.append(f"SPEC.{label} must be an array")
    if spec.get("constraints") is not None and not isinstance(spec["constraints"], dict):
        errs.append("SPEC.constraints must be an object")
    for i, m in enumerate(spec.get("modules") or []):
        if not isinstance(m, dict):
            errs.append(f"SPEC.modules[{i}] must be an object")
            continue
        if not m.get("id"):
            errs.append(f"SPEC.modules[{i}] missing id")
        if not m.get("description"):
            errs.append(f"SPEC.modules[{i}] missing description")
    for i, c in enumerate(spec.get("contracts") or []):
        if not isinstance(c, dict):
            errs.append(f"SPEC.contracts[{i}] must be an object")
            continue
        for fld in ("name", "producer", "schema", "version"):
            if fld not in c or c[fld] in (None, "", {}):
                errs.append(f"SPEC.contracts[{i}] missing or empty '{fld}'")
        cons = c.get("consumers")
        if not isinstance(cons, list) or not cons:
            errs.append(f"SPEC.contracts[{i}].consumers must be a non-empty array")
    return errs


def validate_spec(spec: dict[str, Any]) -> list[str]:
    """Return validation errors; empty list means valid."""
    return _basic_validate(spec)


def minimal_spec_from_intent(intent_text: str) -> dict[str, Any]:
    """Deterministic fallback when the planner does not return a SPEC (deprecated path)."""
    text = (intent_text or "").strip() or "unspecified intent"
    return {
        "intent": text,
        "modules": [
            {
                "id": "HLIG-1",
                "description": "Primary subsystem (refine via planner SPEC in future runs)",
                "role": "implementation",
            }
        ],
        "contracts": [],
        "features": [],
        "constraints": {"_generated": "minimal_spec_from_intent", "notes": "Planner omitted spec; HLIG may still be valid."},
    }


def spec_from_hlig_and_intent(hlig: dict[str, Any], intent_text: str) -> dict[str, Any]:
    """Synthesize SPEC from an existing HLIG object (backward compatibility)."""
    nodes = hlig.get("nodes") or []
    if not nodes and isinstance(hlig.get("graph"), dict):
        nodes = (hlig.get("graph") or {}).get("nodes") or []
    modules: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        kind = str(n.get("kind") or "").strip().lower()
        is_contract = kind == "contract" or (n.get("node_type") or "").lower() == "contract"
        if is_contract:
            schema = n.get("schema") if isinstance(n.get("schema"), dict) else {}
            if isinstance(n.get("source_of_truth"), dict):
                schema = n.get("source_of_truth") or {}
            contracts.append(
                {
                    "name": n.get("name") or n.get("title") or n.get("id"),
                    "producer": n.get("producer") or "",
                    "consumers": list(n.get("consumers") or n.get("implemented_by") or []),
                    "schema": schema,
                    "version": str(n.get("version") or schema.get("version") or "v1"),
                }
            )
            continue
        modules.append(
            {
                "id": n["id"],
                "description": str(n.get("task") or n.get("description") or ""),
                "role": "implementation",
            }
        )
    if not modules:
        modules = minimal_spec_from_intent(intent_text)["modules"]
    return {
        "intent": (intent_text or "").strip() or "unspecified intent",
        "modules": modules,
        "contracts": contracts,
        "features": [],
        "constraints": {"_generated": "spec_from_hlig_and_intent"},
    }


def generate_spec_from_intent(
    intent_text: str,
    *,
    planner_spec: dict[str, Any] | None = None,
    fallback_hlig: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return a validated SPEC dict.

    - If `planner_spec` is provided, validate and return it.
    - Else if `fallback_hlig` is provided, derive SPEC from HLIG + intent.
    - Else return `minimal_spec_from_intent`.
    """
    if planner_spec is not None:
        errs = validate_spec(planner_spec)
        if errs:
            raise ValueError("Invalid SPEC from planner: " + "; ".join(errs[:12]))
        return planner_spec
    if fallback_hlig is not None:
        spec = spec_from_hlig_and_intent(fallback_hlig, intent_text)
        errs = validate_spec(spec)
        if errs:
            return minimal_spec_from_intent(intent_text)
        return spec
    return minimal_spec_from_intent(intent_text)


def merge_planner_spec_with_hlig(
    planner_output: dict[str, Any],
    intent_text: str,
) -> dict[str, Any]:
    """
    Extract or synthesize SPEC from planner JSON. Never raises; returns valid-enough SPEC.
    """
    raw = planner_output.get("spec")
    if isinstance(raw, dict):
        errs = validate_spec(raw)
        if not errs:
            return raw
    hlig = planner_output.get("hlig")
    if isinstance(hlig, dict) and hlig.get("nodes"):
        try:
            return generate_spec_from_intent(intent_text, fallback_hlig=hlig)
        except ValueError:
            pass
    graph = planner_output.get("graph")
    if isinstance(graph, dict) and graph.get("nodes"):
        try:
            return generate_spec_from_intent(intent_text, fallback_hlig={"graph": graph})
        except ValueError:
            pass
    return minimal_spec_from_intent(intent_text)
