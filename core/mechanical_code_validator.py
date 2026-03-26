"""
Mechanical validation of generated source before it is accepted (pre-build gate).

Reduces syntax/parse and trivial format failures by rejecting invalid output early
and surfacing tool output to the code generator for retry.

Environment (Rust):
- MECHANICAL_RUST_EDITION: override edition for rustfmt (default: read from Cargo.toml
  in project_root, else "2021"). Prevents false failures on async/modern syntax when
  rustfmt would otherwise assume 2015 for a bare .rs file.
- MECHANICAL_RUSTFMT_AUTOFORMAT: if 1 (default), run rustfmt --edition … in place on
  the temp file when --check fails so trivial formatting is fixed without an LLM retry.

Environment (React / Vite / Node — Prettier):
- MECHANICAL_PRETTIER: if 1 (default), run Prettier --check on .ts/.tsx/.jsx when the
  HLIG dir has prettier in package.json or a Prettier config; skips if unavailable.
- MECHANICAL_PRETTIER_AUTOFORMAT: if 1 (default), prettier --write on the temp file when
  --check fails (same idea as rustfmt).
- MECHANICAL_PRETTIER_JS: if 1 (default), also run Prettier on .js/.mjs/.cjs after a
  successful node --check (format-only fixes without LLM).
- MECHANICAL_PRETTIER_ALLOW_NPX: if 1 (default), use `npx --yes prettier` when
  node_modules/prettier is missing so TS/TSX checks work before the first `npm install`
  in the HLIG dir (set 0 for air-gapped or to force local install only).

- vite.config.js with package.json "type": "module": reject CommonJS (require /
  module.exports) in vite.config.js so vite build is not the first failure point.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def _env_enabled(name: str, default: str = "1") -> bool:
    v = os.environ.get(name, default).strip().lower()
    return v not in ("0", "false", "no", "off")


def mechanical_validation_enabled() -> bool:
    return _env_enabled("ENABLE_MECHANICAL_VALIDATE", "1")


def incremental_cargo_check_enabled() -> bool:
    return _env_enabled("IG_INCREMENTAL_CARGO_CHECK", "0")


def tsc_mechanical_enabled() -> bool:
    return _env_enabled("ENABLE_TSC_MECHANICAL", "0")


def clippy_mechanical_enabled() -> bool:
    return _env_enabled("ENABLE_CLIPPY_MECHANICAL", "0")


def rustfmt_autoformat_enabled() -> bool:
    """Apply rustfmt in-place on snippet when --check fails (avoids LLM retry for layout-only diffs)."""
    return _env_enabled("MECHANICAL_RUSTFMT_AUTOFORMAT", "1")


def prettier_mechanical_enabled() -> bool:
    return _env_enabled("MECHANICAL_PRETTIER", "1")


def prettier_autoformat_enabled() -> bool:
    return _env_enabled("MECHANICAL_PRETTIER_AUTOFORMAT", "1")


def prettier_for_js_enabled() -> bool:
    """After node --check passes, optionally Prettier plain JS for layout-only fixes."""
    return _env_enabled("MECHANICAL_PRETTIER_JS", "1")


def _cargo_edition(project_root: Path | None) -> str:
    """Edition for rustfmt on isolated snippets: env > Cargo.toml [package].edition > 2021."""
    env = os.environ.get("MECHANICAL_RUST_EDITION", "").strip()
    if env:
        return env
    if project_root:
        cargo = project_root / "Cargo.toml"
        if cargo.is_file():
            try:
                text = cargo.read_text(encoding="utf-8")
            except OSError:
                return "2021"
            m = re.search(r'^\s*edition\s*=\s*"([^"]+)"', text, re.MULTILINE)
            if m:
                return m.group(1).strip()
    return "2021"


_PRETTIER_CONFIG_NAMES = frozenset(
    {
        ".prettierrc",
        ".prettierrc.json",
        ".prettierrc.yaml",
        ".prettierrc.yml",
        "prettier.config.js",
        "prettier.config.mjs",
        "prettier.config.cjs",
        ".prettierrc.js",
        ".prettierrc.cjs",
    }
)


def _node_project_has_prettier(project_root: Path | None) -> bool:
    """True if we should run Prettier (local dep or config in HLIG/Vite root)."""
    if not project_root or not project_root.is_dir():
        return False
    for name in _PRETTIER_CONFIG_NAMES:
        if (project_root / name).is_file():
            return True
    pkg = project_root / "package.json"
    if not pkg.is_file():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    deps = {**(data.get("devDependencies") or {}), **(data.get("dependencies") or {})}
    return "prettier" in deps


def _prettier_installed_locally(project_root: Path) -> bool:
    return (project_root / "node_modules" / "prettier").is_dir()


def _prettier_can_run(project_root: Path | None) -> bool:
    if not _node_project_has_prettier(project_root) or not project_root:
        return False
    if _prettier_installed_locally(project_root):
        return True
    return _env_enabled("MECHANICAL_PRETTIER_ALLOW_NPX", "1")


def _prettier_cmd(tmp_path: str, *, check: bool, project_root: Path) -> list[str]:
    sub = ["prettier", "--check", tmp_path] if check else ["prettier", "--write", tmp_path]
    if _prettier_installed_locally(project_root):
        return ["npm", "exec", "--", *sub]
    return ["npx", "--yes", *sub]


def _validate_prettier_snippet(
    content: str,
    *,
    rel_path: str,
    project_root: Path | None,
) -> tuple[bool, str, str | None]:
    """
    Prettier --check on a temp file with the same extension as rel_path (TS/TSX/JSX/JS).
    Uses npm exec from project_root so local prettier + config apply (Vite/React).
    """
    if not content.strip():
        return False, "empty file", None
    if not prettier_mechanical_enabled() or not _prettier_can_run(project_root):
        return True, "", None

    lower = rel_path.replace("\\", "/").lower()
    suffix = Path(lower).suffix or ".ts"
    if suffix not in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        suffix = ".ts"

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=suffix,
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(content)
        tmp = f.name

    try:
        pr = project_root
        code, out, err = _run(_prettier_cmd(tmp, check=True, project_root=pr), cwd=pr, timeout=90)
        if code == 0:
            return True, "", None
        err_l = (err or "").lower()
        if code < 0 and ("not found" in err_l or err == "command not found"):
            return True, "", None
        if "prettier" in err_l and ("could not determine executable" in err_l or "not found" in err_l):
            return True, "", None

        if prettier_autoformat_enabled():
            code2, _o2, err2 = _run(_prettier_cmd(tmp, check=False, project_root=pr), cwd=pr, timeout=90)
            if code2 == 0:
                try:
                    formatted = Path(tmp).read_text(encoding="utf-8")
                except OSError:
                    formatted = ""
                code3, _o3, _e3 = _run(_prettier_cmd(tmp, check=True, project_root=pr), cwd=pr, timeout=90)
                if code3 == 0 and formatted:
                    return True, "", formatted
            if code2 != 0:
                msg = (err2 or err or out or "prettier failed").strip()
                return False, msg[:4000], None

        msg = (err or out or "prettier --check failed").strip()
        return False, msg[:4000], None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _rustfmt_base_cmd(project_root: Path | None, edition: str) -> list[str]:
    cmd: list[str] = ["rustfmt"]
    if project_root is not None:
        root = project_root / "rustfmt.toml"
        alt = project_root / ".rustfmt.toml"
        cfg_dir = str(project_root) if (root.is_file() or alt.is_file()) else ""
        if cfg_dir:
            cmd.extend(["--config-path", cfg_dir])
    cmd.extend(["--edition", edition])
    return cmd


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 60) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return -1, "", "command not found"
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except OSError as e:
        return -1, "", str(e)


def _validate_rust_snippet(
    content: str,
    *,
    project_root: Path | None,
    edition: str,
) -> tuple[bool, str, str | None]:
    """
    Returns (ok, error_message, formatted_body).
    formatted_body is set when MECHANICAL_RUSTFMT_AUTOFORMAT fixed layout-only issues.
    """
    if not content.strip():
        return False, "empty Rust file", None
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".rs",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(content)
        tmp = f.name
    try:
        base = _rustfmt_base_cmd(project_root, edition)
        code, out, err = _run([*base, "--check", tmp], timeout=45)
        if code == 0:
            return True, "", None
        err_l = (err or "").lower()
        if code < 0 and ("not found" in err_l or err == "command not found"):
            return True, "", None
        if rustfmt_autoformat_enabled():
            code2, _o2, err2 = _run([*base, tmp], timeout=45)
            if code2 == 0:
                try:
                    formatted = Path(tmp).read_text(encoding="utf-8")
                except OSError:
                    formatted = ""
                code3, _o3, _e3 = _run([*base, "--check", tmp], timeout=45)
                if code3 == 0 and formatted:
                    return True, "", formatted
            if code2 != 0:
                msg = (err2 or err or out or "rustfmt failed").strip()
                return False, msg[:4000], None
        msg = (err or out or "rustfmt --check failed").strip()
        return False, msg[:4000], None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _vite_config_esm_coherence(
    rel_path: str, content: str, project_root: Path | None
) -> tuple[bool, str]:
    """
    If package.json declares "type": "module", vite.config.js must be ESM.
    CommonJS in vite.config.js breaks `vite build` (dynamic require / module.exports in ESM package).
    """
    name = Path(rel_path.replace("\\", "/")).name.lower()
    if name != "vite.config.js":
        return True, ""
    if not project_root or not project_root.is_dir():
        return True, ""
    pkg_path = project_root / "package.json"
    if not pkg_path.is_file():
        return True, ""
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, ""
    if str(pkg.get("type", "")).strip().lower() != "module":
        return True, ""
    if re.search(r"\brequire\s*\(", content):
        return (
            False,
            'vite.config.js: package.json has "type":"module" — use '
            '`import { defineConfig } from "vite"` and `export default defineConfig({ ... })`, '
            "not require(). Alternatively rename to vite.config.cjs and keep CommonJS.",
        )
    if re.search(r"\bmodule\.exports\b", content):
        return (
            False,
            'vite.config.js: package.json has "type":"module" — use '
            "`export default defineConfig(...)` instead of module.exports, or use vite.config.cjs.",
        )
    return True, ""


def _validate_js_snippet(content: str) -> tuple[bool, str]:
    if not content.strip():
        return False, "empty JavaScript file"
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".js",
        delete=False,
        encoding="utf-8",
    ) as f:
        f.write(content)
        tmp = f.name
    try:
        code, out, err = _run(["node", "--check", tmp], timeout=30)
        if code == 0:
            return True, ""
        err_l = (err or "").lower()
        if code < 0 and ("not found" in err_l or err == "command not found"):
            return True, ""
        msg = (err or out or "node --check failed").strip()
        return False, msg[:4000]
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _validate_json_snippet(content: str) -> tuple[bool, str]:
    try:
        json.loads(content)
        return True, ""
    except json.JSONDecodeError as e:
        return False, str(e)[:2000]


def _validate_toml_snippet(content: str) -> tuple[bool, str]:
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        return True, ""
    try:
        tomllib.loads(content)
        return True, ""
    except Exception as e:
        return False, str(e)[:2000]


def rust_workspace_coherence_enabled() -> bool:
    """Pre-build static checks on Rust HLIG roots (main.rs/lib.rs vs Cargo.toml and src/ layout)."""
    return _env_enabled("ENABLE_RUST_WORKSPACE_COHERENCE", "1")


def validate_rust_workspace_coherence(project_root: Path) -> tuple[bool, str]:
    """
    Catch common codegen mistakes before cargo: orphan `use crate::foo`, missing log/env_logger in Cargo.toml.

    Reads `src/main.rs` and `src/lib.rs` only (crate roots). Returns (False, message) if inconsistent.
    """
    if not rust_workspace_coherence_enabled():
        return True, ""
    cargo = project_root / "Cargo.toml"
    src = project_root / "src"
    if not cargo.is_file() or not src.is_dir():
        return True, ""
    try:
        cargo_txt = cargo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True, ""

    roots: list[str] = []
    for name in ("main.rs", "lib.rs"):
        p = src / name
        if p.is_file():
            try:
                roots.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    if not roots:
        return True, ""

    combined = "\n".join(roots)
    errs: list[str] = []

    def _dep(key: str) -> bool:
        return bool(re.search(rf"^\s*{re.escape(key)}\s*=", cargo_txt, re.MULTILINE))

    if re.search(r"\blog::", combined) and not _dep("log"):
        errs.append("code uses log:: but Cargo.toml has no `log = ...` dependency entry")
    if "env_logger::" in combined and not _dep("env_logger"):
        errs.append("code uses env_logger:: but Cargo.toml has no `env_logger = ...` dependency entry")
    if "anyhow::" in combined and not _dep("anyhow"):
        errs.append("code uses anyhow:: but Cargo.toml has no `anyhow = ...` dependency entry")
    if re.search(r"\bclap::", combined) and not _dep("clap"):
        errs.append("code uses clap:: but Cargo.toml has no `clap = ...` dependency entry")

    mods = set(re.findall(r"\b(?:pub\s+)?use\s+crate::([a-zA-Z_][a-zA-Z0-9_]*)", combined))
    for m in sorted(mods):
        if m in ("self", "super", "crate"):
            continue
        if (src / f"{m}.rs").is_file():
            continue
        if (src / m / "mod.rs").is_file():
            continue
        if re.search(rf"\bmod\s+{re.escape(m)}\s*\{{", combined):
            continue
        errs.append(
            f"use crate::{m}::… but missing src/{m}.rs or src/{m}/mod.rs "
            f"and no inline `mod {m} {{` in main.rs/lib.rs"
        )

    if errs:
        return False, "; ".join(errs)
    return True, ""


def validate_generated_content(
    rel_path: str,
    content: str,
    framework: str,
    *,
    project_root: Path | None = None,
) -> tuple[bool, str, str | None]:
    """
    Validate a single generated file body.
    Returns (ok, error_message, normalized_content).
    normalized_content is set when Rust/Prettier autoformat adjusted the body (caller should persist it).
    Unknown or non-checked extensions are accepted.
    """
    if not mechanical_validation_enabled():
        return True, "", None

    path = rel_path.replace("\\", "/").strip()
    lower = path.lower()

    if not content and any(
        lower.endswith(ext)
        for ext in (".rs", ".js", ".mjs", ".cjs", ".json", ".toml", ".ts", ".tsx", ".jsx")
    ):
        return False, "empty file", None

    if lower.endswith(".rs"):
        ed = _cargo_edition(project_root)
        return _validate_rust_snippet(content, project_root=project_root, edition=ed)

    if lower.endswith((".tsx", ".jsx", ".ts")):
        # TS/TSX/JSX: Prettier when project is set up (mirrors rustfmt for React/Vite trees).
        return _validate_prettier_snippet(content, rel_path=path, project_root=project_root)

    if lower.endswith((".js", ".mjs", ".cjs")):
        ok_vc, msg_vc = _vite_config_esm_coherence(path, content, project_root)
        if not ok_vc:
            return False, msg_vc, None
        ok, msg = _validate_js_snippet(content)
        if not ok:
            return ok, msg, None
        if prettier_for_js_enabled():
            return _validate_prettier_snippet(content, rel_path=path, project_root=project_root)
        return True, "", None

    if lower.endswith(".json"):
        ok, msg = _validate_json_snippet(content)
        return ok, msg, None

    if lower.endswith(".toml"):
        ok, msg = _validate_toml_snippet(content)
        return ok, msg, None

    return True, "", None


def run_incremental_cargo_check(project_root: Path) -> tuple[bool, str]:
    """Run `cargo check` in project_root (Rust HLIG output). Optional heavy step."""
    if not incremental_cargo_check_enabled():
        return True, ""
    cargo = project_root / "Cargo.toml"
    if not cargo.is_file():
        return True, ""
    code, out, err = _run(["cargo", "check", "--message-format=short"], cwd=project_root, timeout=180)
    if code == 0:
        return True, ""
    err_l = (err or "").lower()
    if code < 0 and ("not found" in err_l or err == "command not found"):
        return True, ""
    msg = (err or out or "cargo check failed").strip()
    return False, msg[:8000]


def extra_toolchain_validate(
    rel_path: str,
    content: str,
    framework: str,
    project_root: Path,
) -> tuple[bool, str]:
    """
    Optional project-level checks after per-file syntax validation (tsc / clippy).
    Skips when tools or config are missing.
    """
    if not mechanical_validation_enabled():
        return True, ""
    _ = content  # file already on disk for these tools
    path = rel_path.replace("\\", "/").lower()
    pr = project_root

    if tsc_mechanical_enabled() and path.endswith((".ts", ".tsx")):
        tsconfig = pr / "tsconfig.json"
        if tsconfig.is_file():
            for cmd in (
                ["npx", "--yes", "tsc", "--noEmit", "-p", str(tsconfig)],
                ["tsc", "--noEmit", "-p", str(tsconfig)],
            ):
                code, out, err = _run(cmd, cwd=pr, timeout=120)
                if code == 0:
                    return True, ""
                err_l = (err or "").lower()
                if code < 0 and ("not found" in err_l or err == "command not found"):
                    continue
                msg = (err or out or "tsc --noEmit failed").strip()
                return False, msg[:6000]
        return True, ""

    if clippy_mechanical_enabled() and path.endswith(".rs") and framework == "rust-tauri":
        cargo = pr / "Cargo.toml"
        if cargo.is_file():
            code, out, err = _run(
                ["cargo", "clippy", "-q", "--message-format=short", "--", "-W", "clippy::all"],
                cwd=pr,
                timeout=180,
            )
            if code == 0:
                return True, ""
            err_l = (err or "").lower()
            if code < 0 and ("not found" in err_l or err == "command not found"):
                return True, ""
            msg = (err or out or "cargo clippy failed").strip()
            return False, msg[:8000]
        return True, ""

    return True, ""
