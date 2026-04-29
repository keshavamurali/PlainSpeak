"""
Deterministic per-file validation (no LLM).

Used after codegen writes to disk: syntax, resolvable relative imports, and
optional contract artifact checks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.mechanical_code_validator import validate_generated_content


def _rel_from_root(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return path.name


def _resolve_contract_paths(root: Path, refs: list[str] | None) -> tuple[list[Path], list[str]]:
    """Map abstract contract refs to on-disk JSON artifacts. Returns (found_paths, missing_refs)."""
    out: list[Path] = []
    missing: list[str] = []
    for r in refs or []:
        if not r or not isinstance(r, str):
            continue
        s = r.strip()
        if not s:
            continue
        # Abstract IG refs (e.g. edge interface_ref "A->B") are not filesystem contracts.
        if "->" in s or s.startswith("file:"):
            continue
        candidates: list[Path] = []
        if s.endswith(".json") or "/" in s:
            candidates.append(root / s.lstrip("/"))
        else:
            for sub in ("contracts", "shared", "designs"):
                candidates.append(root / sub / f"{s}.json")
                candidates.append(root / sub / f"{s}.openapi.json")
        hit: Path | None = None
        for p in candidates:
            if p.is_file():
                hit = p
                break
        if hit:
            out.append(hit)
        else:
            missing.append(s)
    return out, missing


_REL_IMPORT_RE = re.compile(
    r"(?:from\s+['\"](\.\.?/[^'\"]+)['\"]|import\s+[^;]*?from\s+['\"](\.\.?/[^'\"]+)['\"])",
    re.MULTILINE,
)
_RUST_USE_CRATE_RE = re.compile(
    r"^\s*use\s+crate::([\w:]+)\s*;",
    re.MULTILINE,
)


def _check_relative_ts_js_imports(rel_path: str, content: str, project_root: Path) -> list[str]:
    errs: list[str] = []
    lower = rel_path.lower()
    if not lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return errs
    base = (project_root / rel_path).parent
    for m in _REL_IMPORT_RE.finditer(content):
        spec = m.group(1) or m.group(2) or ""
        if not spec or not spec.startswith("."):
            continue
        target = (base / spec).resolve()
        try:
            target.relative_to(project_root.resolve())
        except ValueError:
            errs.append(f"import escapes project root: {spec}")
            continue
        if target.is_file():
            continue
        exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json")
        found = any(target.with_suffix(sfx).is_file() for sfx in exts) or (target / "index.ts").is_file() or (
            target / "index.tsx"
        ).is_file()
        if not found:
            errs.append(f"unresolved relative import '{spec}' from {rel_path}")
    return errs


def _rust_mod_path_to_candidates(crate_rel: str, project_root: Path) -> list[Path]:
    """crate::foo::bar -> src/foo/bar.rs, src/foo/bar/mod.rs"""
    parts = [p for p in crate_rel.split("::") if p and p != "crate"]
    if not parts:
        return []
    base = project_root / "src"
    sub = base.joinpath(*parts)
    return [sub.with_suffix(".rs"), sub / "mod.rs"]


def _check_rust_crate_imports(rel_path: str, content: str, project_root: Path) -> list[str]:
    errs: list[str] = []
    if not rel_path.lower().endswith(".rs"):
        return errs
    for m in _RUST_USE_CRATE_RE.finditer(content):
        path = m.group(1)
        if "*" in path or path.rstrip().endswith("::"):
            continue
        if "::{" in path:
            path = path.split("::{", 1)[0].strip()
        path = path.split(" as ")[0].strip()
        if not path:
            continue
        candidates = _rust_mod_path_to_candidates(path, project_root)
        if candidates and not any(p.is_file() for p in candidates):
            errs.append(f"unresolved crate module '{path}' (from {rel_path})")
    return errs


def _validate_contract_files(paths: list[Path]) -> list[str]:
    errs: list[str] = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            errs.append(f"contract file unreadable {p}: {e}")
            continue
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError as e:
            errs.append(f"contract JSON invalid {p}: {e}")
            continue
        if isinstance(data, dict) and "openapi" in data:
            ver = data.get("openapi")
            if not isinstance(ver, str) or not ver.startswith("3."):
                errs.append(f"OpenAPI contract {p}: expected openapi 3.x string")
    return errs


def validate_file(
    file_path: Path,
    contract_refs: list[str] | None = None,
    *,
    project_root: Path | None = None,
    framework: str = "",
) -> tuple[bool, list[str]]:
    """
    Deterministic checks for a single on-disk source file.

    Returns (ok, errors). Does not call an LLM.
    """
    errors: list[str] = []
    if not file_path.is_file():
        return False, [f"not a file: {file_path}"]
    root = project_root or file_path.parent
    rel = _rel_from_root(file_path, root)
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, [f"read failed: {e}"]
    ok, msg, fixed = validate_generated_content(rel, content, framework, project_root=root)
    if fixed is not None and fixed != content:
        try:
            file_path.write_text(fixed, encoding="utf-8")
            content = fixed
        except OSError:
            pass
    if not ok:
        errors.append(msg or "syntax/format validation failed")

    errors.extend(_check_relative_ts_js_imports(rel, content, root))
    errors.extend(_check_rust_crate_imports(rel, content, root))
    paths, missing_refs = _resolve_contract_paths(root, contract_refs)
    for mref in missing_refs:
        errors.append(f"contract ref has no matching JSON file: {mref}")
    errors.extend(_validate_contract_files(paths))
    return (len(errors) == 0), errors
