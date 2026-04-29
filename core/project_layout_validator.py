"""
Validate HLIG output tree against DTG files_owned and stack naming rules.

- Every path in files_owned must exist when the graph declares any owned paths.
- Optional strict pass: files on disk must be expected or under allowed infra dirs.
- JSX must not appear in *.js (use .jsx / .tsx).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from core.hlig_dtg_graphs import DTGGraph

_SKIP_DIR_PARTS = frozenset(
    {"node_modules", "target", "dist", ".git", ".vite", "coverage", "__pycache__"}
)

# Root/config artifacts not usually listed in files_owned
_INFRA_NAMES = frozenset(
    {
        "README.md",
        "build.log",
        "causal_path.json",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "tsconfig.json",
        "tsconfig.node.json",
        "index.html",
        ".prettierrc.json",
        ".gitignore",
        "node_execution_log.jsonl",
        "ig_execution_log.jsonl",
    }
)

_VITE_CONFIG_RE = re.compile(r"^vite\.config\.(js|mjs|cjs|ts)$")

# Minimal scaffold outputs when DTG omits explicit ownership
_OPTIONAL_ALLOW_REL = frozenset(
    {
        "src/index.js",
        "src/main.rs",
        ".prettierrc.json",
    }
)

_ALLOWED_TOP_LEVEL_DIRS = frozenset({"designs", "migrations", "contracts", "shared", "public"})


def collect_files_owned_from_dtg(dtg: DTGGraph) -> set[str]:
    out: set[str] = set()
    for n in dtg.to_dict().get("nodes", []):
        if not isinstance(n, dict):
            continue
        fo = n.get("files_owned")
        if not isinstance(fo, list):
            continue
        for p in fo:
            if isinstance(p, str) and p.strip():
                out.add(p.strip().replace("\\", "/"))
    return out


def _js_looks_like_jsx(content: str) -> bool:
    if "<" not in content:
        return False
    if re.search(r"<\s*[A-Z][A-Za-z0-9_]*", content):
        return True
    if re.search(
        r"<\s*(?:div|span|p|h[1-6]|ul|ol|li|a|button|form|input|label|textarea|select|option|"
        r"section|main|header|footer|nav|article|table|thead|tbody|tr|td|th|svg|path|img|br|hr)\b",
        content,
    ):
        return True
    return False


def _iter_trackable_files(hlig_dir: Path):
    for p in hlig_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(hlig_dir)
        except ValueError:
            continue
        if any(part in _SKIP_DIR_PARTS for part in rel.parts):
            continue
        yield rel.as_posix(), p


def _strict_unexpected_enabled() -> bool:
    v = os.environ.get("PROJECT_LAYOUT_STRICT_UNEXPECTED", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _allowed_rel_paths(hlig_dir: Path) -> set[str]:
    """Paths permitted without being listed in files_owned (infra + optional scaffold)."""
    allowed: set[str] = set(_OPTIONAL_ALLOW_REL)
    for name in _INFRA_NAMES:
        if (hlig_dir / name).is_file():
            allowed.add(name)
    try:
        for p in hlig_dir.iterdir():
            if not p.is_file():
                continue
            n = p.name
            if _VITE_CONFIG_RE.match(n) or n.startswith(".eslintrc") or "eslint.config" in n:
                allowed.add(n)
    except OSError:
        pass
    return allowed


def layout_validation_enabled() -> bool:
    v = os.environ.get("ENABLE_PROJECT_LAYOUT_VALIDATE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def validate_project_layout(
    hlig_dir: Path,
    dtg: DTGGraph,
    framework: str,
) -> tuple[bool, list[str]]:
    """
    Returns (ok, errors). Does not raise.
    """
    errs: list[str] = []
    if not hlig_dir.is_dir():
        return False, [f"HLIG directory missing: {hlig_dir}"]

    expected = collect_files_owned_from_dtg(dtg)
    allowed = _allowed_rel_paths(hlig_dir)
    allowed |= expected

    for rel in sorted(expected):
        fp = hlig_dir / rel
        if not fp.is_file():
            errs.append(f"missing file declared in DTG files_owned: {rel}")

    for rel, p in _iter_trackable_files(hlig_dir):
        lower = rel.lower()
        if lower.endswith(".js"):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _js_looks_like_jsx(text):
                errs.append(f"JSX in .js (rename to .jsx or .tsx): {rel}")

    if _strict_unexpected_enabled() and expected:
        for rel, _p in _iter_trackable_files(hlig_dir):
            if rel in allowed:
                continue
            top = rel.split("/")[0]
            if top in _ALLOWED_TOP_LEVEL_DIRS:
                continue
            errs.append(f"unexpected file (not in files_owned or infra allowlist): {rel}")

    return (len(errs) == 0), errs
</think>

I introduced a typo: `.Strip()` should be `.strip()`.

<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>
StrReplace