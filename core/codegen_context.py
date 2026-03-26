"""
Shared context for multi-file codegen: contract excerpts and public-surface snapshots.

Keeps DTG/HLIG simple; logic is deterministic and used at execution time only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def load_shared_interfaces_excerpt(outputs_root: Path, max_chars: int) -> str:
    """
    Read outputs_{session}/shared/interfaces.json (written by _write_interface_definitions).
    outputs_root is the parent of each HLIG-* directory (e.g. outputs_123/).
    """
    if max_chars <= 0:
        return ""
    p = outputs_root / "shared" / "interfaces.json"
    if not p.is_file():
        return ""
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + "\n/* ... truncated (SHARED_INTERFACES_MAX_CHARS) ... */\n"


_RUST_PUB = re.compile(
    r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait|type|mod|use|impl)\b.*$",
    re.MULTILINE,
)
_TS_EXPORT = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?|export\s*\{)",
    re.MULTILINE,
)


def extract_public_surface(rel_path: str, content: str, max_lines: int = 64, max_chars: int = 4000) -> str:
    """
    Heuristic public API lines for cross-file naming consistency (not a full parser).
    """
    path = rel_path.replace("\\", "/").lower()
    lines: list[str] = []
    if path.endswith(".rs"):
        for m in _RUST_PUB.finditer(content):
            line = m.group(0).strip()
            if line and line not in lines:
                lines.append(line)
    elif path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        for m in _TS_EXPORT.finditer(content):
            line = m.group(0).strip()
            if line and line not in lines:
                lines.append(line)
    else:
        head = content.strip().splitlines()[: min(24, max_lines)]
        lines = [ln.rstrip() for ln in head if ln.strip()]

    text = "\n".join(lines[:max_lines])
    if len(text) > max_chars:
        text = text[:max_chars] + "\n/* ... truncated ... */"
    return text


def build_snapshots_payload(snapshots: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return JSON-serializable list for LLM input."""
    out: list[dict[str, str]] = []
    for s in snapshots:
        if not isinstance(s, dict):
            continue
        p = s.get("path")
        surf = s.get("public_surface")
        if isinstance(p, str) and p.strip() and isinstance(surf, str):
            out.append({"path": p.strip(), "public_surface": surf})
    return out
