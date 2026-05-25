"""Validate planner-emitted `clarification_canonical` (structured intent for downstream LLMs)."""

from __future__ import annotations

from typing import Any

# Keys the planner must emit on the final HLIG response (see prompts/planner.md).
REQUIRED_CLARIFICATION_CANONICAL_KEYS = (
    "surface",
    "user_facing_areas",
    "visual_style",
    "persistence",
    "accounts",
    "files_media",
    "integrations",
)


def clarification_canonical_errors(obj: Any) -> list[str]:
    """Return human-readable issues; empty list means acceptable."""
    errs: list[str] = []
    if not isinstance(obj, dict):
        errs.append("clarification_canonical must be a JSON object")
        return errs
    for k in REQUIRED_CLARIFICATION_CANONICAL_KEYS:
        if k not in obj:
            errs.append(f"clarification_canonical missing required key: {k}")
    if errs:
        return errs

    surf = obj.get("surface")
    if not isinstance(surf, dict):
        errs.append("clarification_canonical.surface must be an object")
    elif not str((surf.get("primary") or surf.get("summary") or "")).strip():
        errs.append("clarification_canonical.surface needs non-empty primary or summary")

    areas = obj.get("user_facing_areas")
    if not isinstance(areas, list) or not areas:
        errs.append("clarification_canonical.user_facing_areas must be a non-empty array of strings")
    elif not all(isinstance(x, str) and x.strip() for x in areas):
        errs.append("clarification_canonical.user_facing_areas entries must be non-empty strings")

    for label in ("visual_style", "persistence", "accounts", "files_media", "integrations"):
        if not isinstance(obj.get(label), dict):
            errs.append(f"clarification_canonical.{label} must be an object")

    return errs
