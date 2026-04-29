"""
Helpers to keep DTG code nodes bounded (file count). Policy only — does not mutate graphs on disk.
"""

from __future__ import annotations

import copy
import os
from typing import Any

DEFAULT_FILES_OWNED_MAX = 3


def files_owned_max() -> int:
    raw = os.environ.get("DTG_FILES_OWNED_MAX", str(DEFAULT_FILES_OWNED_MAX)).strip()
    try:
        n = int(raw)
        return max(1, min(n, 32))
    except ValueError:
        return DEFAULT_FILES_OWNED_MAX


def split_large_task(node: dict[str, Any]) -> list[dict[str, Any]]:
    """
    If node.files_owned exceeds threshold, return a list of shallow-cloned nodes
    with disjoint files_owned (same metadata, split paths round-robin).

    If under threshold, returns [node] (single-element list).
    """
    fo = node.get("files_owned")
    if not isinstance(fo, list) or not fo:
        return [node]
    paths = [p.strip() for p in fo if isinstance(p, str) and p.strip()]
    cap = files_owned_max()
    if len(paths) <= cap:
        return [node]

    chunks: list[list[str]] = [[] for _ in range((len(paths) + cap - 1) // cap)]
    for i, p in enumerate(paths):
        chunks[i % len(chunks)].append(p)

    out: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        n2 = copy.deepcopy(node)
        nid = str(n2.get("id") or "DTG-SPLIT")
        n2["id"] = f"{nid}-part{idx + 1}"
        n2["files_owned"] = chunk
        title = n2.get("title") or nid
        n2["title"] = f"{title} (part {idx + 1}/{len(chunks)})"
        out.append(n2)
    return out
