"""
DTG Artifact Generator - traverses DTG nodes and generates design docs and code.

CVP (Causal Visual Programming) integration:
- Causal path traceability: records which HLIG nodes led to each artifact for audit
- Markov blanket scoping: restricts agent context to causal parents only
"""

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from core.codegen_context import (
    build_snapshots_payload,
    extract_public_surface,
    load_shared_interfaces_excerpt,
)
from core.deterministic_file_validator import validate_file as deterministic_validate_file
from core.expansion_engine import (
    effective_dtg_type,
    expand_dtg_node,
    is_code_execution_node,
    is_design_like_node,
    is_scaffold_node,
)
from core.hlig_dtg_graphs import HLIGGraph, DTGGraph
from core.markov_context import validate_node_dependency_context
from core.mechanical_code_validator import (
    extra_toolchain_validate,
    run_incremental_cargo_check,
    validate_generated_content,
    validate_rust_workspace_coherence,
)

try:
    from core.debug_logger import log_pipeline_event, CostLimitExceeded, check_cost_limit_before_llm, log_llm_input
except ImportError:
    log_pipeline_event = lambda *a, **kw: None
    CostLimitExceeded = Exception  # noqa: type to satisfy isinstance
    def check_cost_limit_before_llm(_session_id: str) -> None: ...
    log_llm_input = lambda *a, **kw: None

PROJECT_ROOT = Path(__file__).resolve().parent.parent

IG_EXECUTION_LOG = "ig_execution_log.jsonl"
NODE_EXECUTION_LOG = "node_execution_log.jsonl"


class LocalBuildFailedError(RuntimeError):
    """Raised when local `cargo`/`npm` build fails after per-DTG-node retries (default: abort the pipeline)."""

    def __init__(
        self,
        message: str,
        *,
        hlig_id: str = "",
        dtg_node_id: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.hlig_id = hlig_id
        self.dtg_node_id = dtg_node_id
        self.stderr = stderr


def _per_node_build_max_retries() -> int:
    """Extra attempts after the first try (default 2 => 3 total attempts per DTG code node)."""
    raw = os.environ.get("PER_NODE_BUILD_RETRIES", "2").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 2


def _ig_code_step_max_iterations() -> int:
    """Max generate→review→mechanical iterations per IG file (default 3 = strict retry budget)."""
    raw = os.environ.get("IG_CODE_STEP_MAX_ITERATIONS", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _ig_two_phase_enabled() -> bool:
    return _env_truthy("IG_TWO_PHASE_CODEGEN", "0")


def _ig_two_phase_eligible(fp: str) -> bool:
    pl = fp.lower().replace("\\", "/")
    return pl.endswith((".rs", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))


def _shared_interfaces_max_chars() -> int:
    return _ctx_limit("SHARED_INTERFACES_MAX_CHARS", 14_000)


def _truncate_same_node_prior(content: str) -> str:
    """Larger default than CODE_CONTEXT for cross-file consistency within one DTG node."""
    lim = _ctx_limit("SAME_NODE_PRIOR_MAX_CHARS", 8000)
    if lim <= 0:
        return content
    if len(content) <= lim:
        return content
    return content[:lim] + "\n/* ... truncated (SAME_NODE_PRIOR_MAX_CHARS) ... */\n"


def _append_ig_execution_log(hlig_dir: Path, record: dict) -> None:
    fp = hlig_dir / IG_EXECUTION_LOG
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        with fp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def _append_node_execution_log(hlig_dir: Path, record: dict[str, Any]) -> None:
    """Structured per-DTG-node execution record (deterministic logging)."""
    fp = hlig_dir / NODE_EXECUTION_LOG
    try:
        fp.parent.mkdir(parents=True, exist_ok=True)
        with fp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def _deterministic_on_disk_validate_enabled() -> bool:
    return _env_truthy("ENABLE_DETERMINISTIC_FILE_VALIDATE", "1")


def _maybe_validate_project_layout(
    hlig_dir: Path,
    dtg: DTGGraph,
    framework: str,
    *,
    session_id: str,
    hlig_id: str,
) -> None:
    """After codegen, check files_owned vs disk and naming rules. Raises when abort env is on."""
    from core.project_layout_validator import layout_validation_enabled, validate_project_layout

    if not layout_validation_enabled():
        return
    ok, errs = validate_project_layout(hlig_dir, dtg, framework)
    if ok:
        log_pipeline_event(session_id, "project_layout_ok", {"hlig": hlig_id})
        return
    msg = "\n".join(errs[:40])
    log_pipeline_event(
        session_id,
        "project_layout_failed",
        {"hlig": hlig_id, "error_count": len(errs), "sample": errs[:15]},
    )
    if _abort_on_local_build_failure():
        raise LocalBuildFailedError(
            f"Project layout validation failed for HLIG {hlig_id}:\n{msg}",
            hlig_id=hlig_id,
            dtg_node_id="__project_layout__",
            stderr=msg[:8000],
        )


def _parse_code_reviewer_status(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "pass"
    try:
        from core.json_parser import parse_llm_json
        parsed = parse_llm_json(text, required_keys=["overall_status"])
        return str(parsed.get("overall_status", "pass")).lower()
    except Exception:
        return "pass"


def _abort_on_local_build_failure() -> bool:
    """
    When True, failed local build after node retries raises LocalBuildFailedError (stops the pipeline).
    Default True: stop the run on any build failure after retries.
    Set ABORT_ON_LOCAL_BUILD_FAILURE=0 (or false/no) to log and continue with remaining DTG nodes.
    """
    v = os.environ.get("ABORT_ON_LOCAL_BUILD_FAILURE", "1").strip().lower()
    return v not in ("0", "false", "no")


# Configurable context truncation (env: DESIGN_CONTEXT_MAX_CHARS, CODE_CONTEXT_MAX_CHARS). Reduces input tokens.
# Use 0 for no truncation.
def _ctx_limit(name: str, default: int) -> int:
    v = os.environ.get(name, "")
    return int(v) if v else default


_DESIGN_CTX = _ctx_limit("DESIGN_CONTEXT_MAX_CHARS", 2000)
_CODE_CTX = _ctx_limit("CODE_CONTEXT_MAX_CHARS", 1500)


def _truncate_retry_project_files(files: dict[str, str]) -> dict[str, str]:
    """
    Cap total size of previous_attempt_files sent on build retry (env BUILD_RETRY_FILES_MAX_TOTAL_CHARS).
    Prioritize Cargo.toml, package.json, then other paths alphabetically. Use 0 for unlimited.
    """
    max_total = _ctx_limit("BUILD_RETRY_FILES_MAX_TOTAL_CHARS", 100_000)
    if max_total <= 0 or not files:
        return files
    keys = sorted(
        files.keys(),
        key=lambda k: (
            0 if k == "Cargo.toml" or k.endswith("/Cargo.toml") else 1 if k == "package.json" else 2,
            k,
        ),
    )
    out: dict[str, str] = {}
    n = 0
    for k in keys:
        v = files[k]
        if n + len(v) <= max_total:
            out[k] = v
            n += len(v)
        else:
            room = max_total - n
            if room > 800:
                out[k] = v[:room] + "\n/* ... truncated (BUILD_RETRY_FILES_MAX_TOTAL_CHARS) ... */\n"
            break
    return out


def _snapshot_project_files_for_retry(
    hlig_dir: Path,
    framework: str,
    last_generated: dict[str, str],
) -> dict[str, str]:
    """
    Build full-text snapshot for the next LLM attempt after a failed build.
    Prefer on-disk content (matrix post-processing may have edited manifests).
    Always include Cargo.toml / package.json when present.
    """
    merged: dict[str, str] = {}
    for rel, content in last_generated.items():
        fp = hlig_dir / rel
        if fp.is_file():
            try:
                merged[rel] = fp.read_text(encoding="utf-8")
            except OSError:
                merged[rel] = content
        else:
            merged[rel] = content
    if framework == "rust-tauri":
        cargo = hlig_dir / "Cargo.toml"
        if cargo.is_file():
            try:
                merged["Cargo.toml"] = cargo.read_text(encoding="utf-8")
            except OSError:
                pass
    elif framework == "node-react":
        pj = hlig_dir / "package.json"
        if pj.is_file():
            try:
                merged["package.json"] = pj.read_text(encoding="utf-8")
            except OSError:
                pass
    return _truncate_retry_project_files(merged)


def _truncate_design(s: str) -> str:
    return s[:_DESIGN_CTX] if _DESIGN_CTX > 0 else s


def _truncate_code(s: str) -> str:
    return s[:_CODE_CTX] if _CODE_CTX > 0 else s


def _make_code_output_spec(node_id: str, files: list[tuple[str, str]]) -> str:
    """Build canonical code_output JSON for LLM consumption (dependency context)."""
    spec = {
        "type": "code_output",
        "version": "1.0",
        "node_id": node_id,
        "files": [
            {"path": p, "content_preview": _truncate_code(c)}
            for p, c in files
        ],
    }
    return json.dumps(spec, indent=2, default=str)


def _make_dtg_node_ref(dep_node: dict) -> str:
    """Build canonical dtg_node_ref JSON when design spec is missing (e.g. hlig_no_design_docs)."""
    ref = {
        "type": "dtg_node_ref",
        "version": "1.0",
        "node_id": dep_node.get("id", ""),
        "title": dep_node.get("title", ""),
        "description": dep_node.get("description", ""),
        "inputs_required": dep_node.get("inputs_required", []),
        "outputs_produced": dep_node.get("outputs_produced", []),
        "output_descriptions": dep_node.get("output_descriptions", {}),
        "success_criteria": dep_node.get("success_criteria", []),
    }
    return json.dumps(ref, indent=2, default=str)


def _build_dep_ctx(deps: list[str], resolved: dict[str, str], nodes_by_id: dict[str, dict]) -> dict[str, str]:
    """Build dependency_context with canonical formats. Injects dtg_node_ref when design spec is missing."""
    result: dict[str, str] = {}
    for dep in deps:
        content = resolved.get(dep)
        if content:
            result[dep] = content
            continue
        node = nodes_by_id.get(dep)
        if node and is_design_like_node(node):
            result[dep] = _make_dtg_node_ref(node)
    return result


def _build_implementation_brief(
    dependency_context: dict[str, str],
    interface_definitions: list[dict] | None,
    dtg_node: dict,
    framework: str,
) -> str:
    """
    Build a full LLM-oriented implementation brief from design/DTG context.
    Gives the coder a single, clear prompt block: what to implement, interfaces, and compilability.
    """
    lines: list[str] = []
    lines.append("## Implementation brief (follow this when generating code)")
    lines.append("")
    lines.append(f"**This task:** {dtg_node.get('title', '')} — {dtg_node.get('description', '')}")
    lines.append("")
    for dep_id, content in dependency_context.items():
        if not content or not content.strip():
            continue
        try:
            parsed = json.loads(content)
        except Exception:
            lines.append(f"### Dependency {dep_id}")
            lines.append(content[:2000] + ("..." if len(content) > 2000 else ""))
            lines.append("")
            continue
        ptype = parsed.get("type", "")
        if ptype == "design_spec":
            lines.append(f"### Design spec ({dep_id})")
            arch = parsed.get("architecture") or {}
            if arch:
                lines.append("**Architecture:** " + json.dumps(arch, indent=2, default=str))
            instr = parsed.get("implementation_instructions") or []
            if instr:
                lines.append("**Implementation steps (follow in order):**")
                for i, step in enumerate(instr, 1):
                    lines.append(f"  {i}. {step}")
            constraints = parsed.get("constraints") or []
            if constraints:
                lines.append("**Constraints:** " + "; ".join(constraints))
            outputs = parsed.get("outputs") or []
            if outputs:
                lines.append("**Outputs to produce:** " + ", ".join(str(o) for o in outputs))
            iface_refs = parsed.get("interface_refs") or []
            if iface_refs:
                lines.append("**Interface refs:** " + ", ".join(str(r) for r in iface_refs))
            lines.append("")
        elif ptype == "dtg_node_ref":
            lines.append(f"### DTG ref ({dep_id}) — use when no full design spec")
            lines.append(f"**Title:** {parsed.get('title', '')}")
            lines.append(f"**Description:** {parsed.get('description', '')}")
            for key in ("inputs_required", "outputs_produced", "success_criteria"):
                val = parsed.get(key)
                if val:
                    lines.append(f"**{key}:** {json.dumps(val, default=str)}")
            lines.append("")
        # code_output: no need to repeat in brief; dependency_context already has it
    if interface_definitions:
        lines.append("### Required interfaces (APIs / contracts)")
        lines.append("Implement and respect these contracts; both Frontend and Backend use the same definitions.")
        lines.append(json.dumps(interface_definitions, indent=2, default=str))
        lines.append("")
    lines.append("### Compilation requirement")
    if framework == "rust-tauri":
        lines.append("Code must compile with `cargo build`. Use valid Rust 2021; all imports and types must resolve.")
        lines.append(
            "Local verification may run: `cargo check`, then `cargo clippy` (unless ENABLE_LOCAL_CLIPPY=0), then `cargo build`."
        )
    else:
        lines.append("Code must build with `npm run build`. Use valid JS/ES modules; all imports must resolve.")
        lines.append(
            "Local verification may run: `npm exec -- tsc --noEmit` when TypeScript is listed, `npm exec -- eslint .` when ESLint is configured, then `npm run build` (see prompts/README.md for env toggles)."
        )
    dep_rules = _dependency_rules_text(framework, _load_dependency_matrix())
    if dep_rules:
        lines.append("")
        lines.append(dep_rules)
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# CVP: Metadata key for causal path in generated artifacts (traceability)
CAUSAL_PATH_FILE = "causal_path.json"

DEPENDENCY_MATRIX_PATH = PROJECT_ROOT / "agents" / "config" / "dependency_matrix.yaml"


def _load_dependency_matrix() -> dict:
    """Load pinned dependency matrix from config. Returns {} if missing or invalid."""
    if not DEPENDENCY_MATRIX_PATH.exists():
        return {}
    try:
        import yaml
        with open(DEPENDENCY_MATRIX_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _dependency_rules_text(framework: str, matrix: dict) -> str:
    """Build dependency and layout rules text for the given framework to inject into implementation_brief."""
    if not matrix:
        return ""
    section = matrix.get("rust-tauri" if framework == "rust-tauri" else "node-react")
    if not isinstance(section, dict):
        return ""
    lines = ["### Dependency and layout rules (required)"]
    rules = section.get("cargo_rules") or section.get("package_json_rules") or ""
    if rules:
        lines.append(rules.strip())
    if framework == "rust-tauri":
        ds = section.get("diesel_sqlite_rules")
        if isinstance(ds, str) and ds.strip():
            lines.append("### Diesel + SQLite (required when using Diesel with SQLite)")
            lines.append(ds.strip())
        ap = section.get("argon2_password_rules")
        if isinstance(ap, str) and ap.strip():
            lines.append("### Argon2 / password-hash (required when using argon2 or password hashing)")
            lines.append(ap.strip())
        wh = section.get("warp_http_rules")
        if isinstance(wh, str) and wh.strip():
            lines.append("### Warp HTTP (required when using the `warp` crate)")
            lines.append(wh.strip())
    deps = section.get("dependencies")
    if deps and isinstance(deps, dict):
        lines.append("Use these dependency versions (or compatible); do not invent versions or features:")
        lines.append(json.dumps(deps, indent=2, default=str))
    dev_deps = section.get("dev_dependencies")
    if dev_deps and isinstance(dev_deps, dict):
        lines.append("Use these devDependency versions for tooling (vite, typescript, eslint, etc.):")
        lines.append(json.dumps(dev_deps, indent=2, default=str))
    return "\n".join(lines) if len(lines) > 1 else ""


def _normalize_hlig_language_field(hlig_node: dict) -> str:
    """Normalize planner `language` (string or list) to a single comma-separated string."""
    raw = hlig_node.get("language")
    if raw is None:
        return ""
    if isinstance(raw, list):
        return ", ".join(str(x).strip() for x in raw if str(x).strip())
    return str(raw).strip()


def _lang_has_explicit_node_backend(lang_lc: str) -> bool:
    """True when `language` names a Node/JS HTTP server stack (caller passes lowercased string)."""
    return bool(
        re.search(
            r"\b(node\.?js|express|fastify|nestjs|koa|hapi|bun|deno)\b",
            lang_lc,
        )
    )


def _hlig_id_implies_frontend(hlig_id: str) -> bool:
    u = (hlig_id or "").upper()
    return any(x in u for x in ("FRONTEND", "WEB-CLIENT", "SPA-UI"))


def _hlig_id_implies_backend(hlig_id: str) -> bool:
    u = (hlig_id or "").upper()
    return any(
        x in u
        for x in (
            "BACKEND",
            "SERVER",
            "MICROSERVICE",
            "-API",
            "API-SERVICE",
        )
    )


def _infer_framework(hlig_node: dict) -> str:
    """
    Infer framework from HLIG node.
    - node-react: web/JS stack when Rust is not declared, or SPA HLIGs mis-tagged Rust+web without Tauri.
    - rust-tauri: Tauri/desktop, Rust-only services, or backend/API HLIGs implemented in Rust.

    HLIG ids containing BACKEND / SERVER / *-API (planner convention) pin Rust codegen unless
    `language` explicitly names Node (Express, Fastify, …). FRONTEND-style ids pin Vite/React.
    """
    hlig_id = str(hlig_node.get("id") or "")
    task = (hlig_node.get("task") or "").lower()
    interfaces = {str(x).lower() for x in (hlig_node.get("external_interfaces") or [])}
    lang_raw = _normalize_hlig_language_field(hlig_node)
    lang = lang_raw.lower()

    def _lang_has_rust(s: str) -> bool:
        return bool(re.search(r"\brush\b", s))

    def _lang_has_tauri(s: str) -> bool:
        return "tauri" in s

    web_markers = (
        "react",
        "javascript",
        "typescript",
        "html",
        "css",
        "vue",
        "svelte",
        "vite",
    )
    has_rust = _lang_has_rust(lang)
    has_tauri = _lang_has_tauri(lang)
    has_web = any(m in lang for m in web_markers)

    if _hlig_id_implies_frontend(hlig_id):
        return "node-react"
    if _hlig_id_implies_backend(hlig_id) and not _lang_has_explicit_node_backend(lang):
        return "rust-tauri"

    # Desktop / Tauri targets always need a Rust crate root.
    if "desktop" in task or has_tauri:
        return "rust-tauri"

    # Historical default when language omitted: assume Rust-capable HLIG.
    if not lang.strip():
        return "rust-tauri"

    # No Rust in declared stack — never scaffold Cargo (e.g. Vite/React-only HLIGs).
    if not has_rust:
        return "node-react"

    frontendish = any(
        k in task
        for k in (
            "frontend",
            "user interface",
            "web page",
            "web pages",
            "website",
            "landing page",
            "visitor",
            "display",
            "spa",
        )
    ) or "react" in task

    backendish = any(
        k in task
        for k in (
            "backend",
            "microservice",
            " axum",
            " actix",
            "diesel",
            "sqlx",
            "rest api",
            "graphql server",
            "web server",
            "http server",
            "file server",
            "api server",
            "server directory",
            "server directories",
            "reading files",
            "read files from",
            "filesystem",
            "database",
        )
    )
    if "api" in task and not frontendish:
        backendish = True
    if (("server" in task) or ("serve" in task)) and frontendish:
        # e.g. "Serve and display the … website" — UI HLIG, not a Rust service.
        pass
    elif "server" in task and not frontendish:
        backendish = True

    # Rust-only compilation unit.
    if not has_web:
        return "rust-tauri"

    # Rust + web without Tauri: full-stack mistake vs SPA — prefer Node when task reads like UI.
    if not has_tauri and frontendish and not backendish:
        return "node-react"

    if has_rust and ("api" in interfaces or "db" in interfaces) and not (frontendish and has_web):
        return "rust-tauri"

    if backendish or "backend" in task:
        return "rust-tauri"

    return "rust-tauri"


def _framework_for_atomic_node(node: dict, default_framework: str) -> str:
    """
    Resolve framework for an atomic DTG node from explicit implementation metadata.
    Falls back to HLIG-level inferred framework for backward compatibility.
    """
    impl = node.get("implementation")
    if not isinstance(impl, dict):
        return default_framework
    fw = str(impl.get("framework") or "").strip().lower()
    lang = str(impl.get("language") or "").strip().lower()
    if fw in ("react", "vite", "nextjs", "next", "node-react"):
        return "node-react"
    if fw in ("express", "fastify", "nestjs", "jest", "vitest", "playwright", "node", "npm"):
        return "node-react"
    if fw in ("tauri", "rust", "axum", "actix", "rocket", "cargo", "rust-tauri"):
        return "rust-tauri"
    if any(x in lang for x in ("typescript", "javascript", "node")):
        return "node-react"
    if "rust" in lang:
        return "rust-tauri"
    return default_framework


def _prune_empty_rust_scaffold(hlig_dir: Path) -> None:
    """Remove Cargo stub from a prior rust-tauri mis-inference when this HLIG is node-react."""
    cargo = hlig_dir / "Cargo.toml"
    main_rs = hlig_dir / "src" / "main.rs"
    if not cargo.is_file() or not main_rs.is_file():
        return
    rs_files = [
        p
        for p in hlig_dir.rglob("*.rs")
        if "target" not in p.parts and "node_modules" not in p.parts
    ]
    if len(rs_files) != 1:
        return
    try:
        if rs_files[0].resolve() != main_rs.resolve():
            return
    except OSError:
        return
    body = main_rs.read_text(encoding="utf-8", errors="replace")
    decommented = "\n".join(line.partition("//")[0] for line in body.splitlines())
    compact = re.sub(r"\s+", "", decommented)
    if compact != "fnmain(){}":
        return
    ct = cargo.read_text(encoding="utf-8", errors="replace")
    if "[package]" not in ct or "src/main.rs" not in ct:
        return
    try:
        cargo.unlink()
        main_rs.unlink()
    except OSError:
        return
    rtc = hlig_dir / "rust-toolchain.toml"
    if rtc.is_file():
        try:
            rtxt = rtc.read_text(encoding="utf-8", errors="replace")
            if "[toolchain]" in rtxt and 'channel = "stable"' in rtxt:
                rtc.unlink()
        except OSError:
            pass
    src = hlig_dir / "src"
    try:
        if src.is_dir() and not any(src.iterdir()):
            src.rmdir()
    except OSError:
        pass


# Rust first build can be slow (cargo fetch + compile); use longer timeout
_RUST_BUILD_TIMEOUT_SEC = 180


def _env_truthy(name: str, default: str = "1") -> bool:
    """True unless env is 0/false/no/off (case-insensitive)."""
    v = os.environ.get(name, default).strip().lower()
    return v not in ("0", "false", "no", "off")


def _ig_code_review_enabled() -> bool:
    return _env_truthy("ENABLE_IG_CODE_REVIEW", "1")


def _any_src_files_for_tsc(hlig_dir: Path) -> bool:
    """True if src/ has files tsc can typecheck (.ts/.tsx/.js/.jsx)."""
    src = hlig_dir / "src"
    if not src.is_dir():
        return False
    skip = {"node_modules", "dist", ".git"}
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        if any(x in p.parts for x in skip):
            continue
        if p.suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            return True
    return False


def _should_run_tsc_noemit(hlig_dir: Path) -> bool:
    """True if tsconfig + typescript dep exist and src has checkable source files."""
    if not (hlig_dir / "tsconfig.json").exists():
        return False
    pj = hlig_dir / "package.json"
    if not pj.exists():
        return False
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        if "typescript" not in deps:
            return False
    except Exception:
        return False
    return _any_src_files_for_tsc(hlig_dir)


def _should_run_eslint(hlig_dir: Path) -> bool:
    """
    True only if eslint is listed in package.json AND a config exists.
    Avoids ad-hoc `npx eslint` downloading ESLint 10 (Node 20+) when eslint is not a project dep.
    """
    pj = hlig_dir / "package.json"
    if not pj.exists():
        return False
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        if "eslint" not in deps:
            return False
        if data.get("eslintConfig"):
            return True
    except Exception:
        return False
    for name in (
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        "eslint.config.ts",
        ".eslintrc.cjs",
        ".eslintrc.js",
        ".eslintrc.json",
    ):
        if (hlig_dir / name).exists():
            return True
    return False


def _normalize_tsconfig_vite_inputs(hlig_dir: Path, cfg: dict) -> None:
    """
    Fix invalid include patterns (e.g. src*.jsx) and set allowJs when only JSX/JS sources exist
    so `tsc --noEmit` finds inputs (avoids TS18003).
    """
    src = hlig_dir / "src"
    comp = cfg.setdefault("compilerOptions", {})
    if src.is_dir():
        has_js_or_jsx = False
        for p in src.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in (".js", ".jsx"):
                has_js_or_jsx = True
                break
        if has_js_or_jsx:
            comp["allowJs"] = True
            comp.setdefault("checkJs", False)
    inc = cfg.get("include")
    bad = not isinstance(inc, list) or not inc
    if isinstance(inc, list):
        for pat in inc:
            if not isinstance(pat, str):
                bad = True
                break
            if pat == "src":
                continue
            # Broken globs like "src*.jsx" (must not match real recursive patterns)
            if pat.startswith("src") and "**" not in pat and "/" not in pat.replace("\\", "/"):
                bad = True
                break
    if bad:
        cfg["include"] = ["src"]


def _ensure_eslint_flat_config_peers(hlig_dir: Path, matrix: dict) -> None:
    """
    If eslint flat config imports the typescript-eslint meta-package, ensure devDependencies
    include matrix-pinned versions (avoids ERR_MODULE_NOT_FOUND for 'typescript-eslint').
    """
    pkg_path = hlig_dir / "package.json"
    if not pkg_path.exists():
        return
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except Exception:
        return
    flat_names = (
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        "eslint.config.ts",
    )
    needs_ts_eslint = False
    for name in flat_names:
        p = hlig_dir / name
        if not p.exists():
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if "typescript-eslint" in txt:
            needs_ts_eslint = True
            break
    if not needs_ts_eslint:
        return
    section = matrix.get("node-react")
    if not isinstance(section, dict):
        return
    mdeps: dict[str, Any] = {}
    for mk in ("dependencies", "dev_dependencies"):
        block = section.get(mk)
        if isinstance(block, dict):
            mdeps.update(block)
    if not mdeps:
        return
    dev = dict(pkg.get("devDependencies") or {})
    changed = False
    for k in ("typescript-eslint", "@typescript-eslint/eslint-plugin", "@typescript-eslint/parser", "eslint"):
        if k not in dev and k in mdeps:
            dev[k] = mdeps[k]
            changed = True
    if changed:
        pkg["devDependencies"] = dev
        try:
            pkg_path.write_text(json.dumps(pkg, indent=2), encoding="utf-8")
        except OSError:
            pass


def _run_local_build(hlig_dir: Path, framework: str, timeout_sec: int = 120) -> tuple[bool, str, str]:
    """
    Run a lightweight compile/build in hlig_dir. No MCP.
    Returns (success, stdout, stderr).

    Environment (optional):
    - Rust: ENABLE_LOCAL_CLIPPY (default 1) — run `cargo clippy --all-targets` after `cargo check`.
      CLIPPY_DENY_WARNINGS (default 0) — if 1, append `-- -D warnings` to clippy.
    - Node: ENABLE_LOCAL_TSC (default 1) — run `npm exec -- tsc --noEmit` when tsconfig.json + typescript in package.json.
      ENABLE_LOCAL_ESLINT (default 1) — run `npm exec -- eslint .` when eslint is configured.
      ESLINT_MAX_WARNINGS_ZERO (default 0) — if 1, pass `--max-warnings 0` to eslint.
    """
    if not hlig_dir.exists():
        return False, "", "directory does not exist"
    if framework == "rust-tauri" and timeout_sec == 120:
        timeout_sec = _RUST_BUILD_TIMEOUT_SEC
    try:
        if framework == "rust-tauri":
            # Fast syntax/type gate before full link (incremental build makes second step cheap)
            r_check = subprocess.run(
                ["cargo", "check"],
                cwd=str(hlig_dir),
                capture_output=True,
                timeout=timeout_sec,
                text=True,
            )
            out_parts: list[str] = ["--- cargo check ---\n" + (r_check.stdout or "")]
            err_parts: list[str] = [r_check.stderr or ""]
            if r_check.returncode != 0:
                return False, "".join(out_parts), "".join(err_parts)

            if _env_truthy("ENABLE_LOCAL_CLIPPY", "1"):
                clippy_cmd = ["cargo", "clippy", "--all-targets"]
                if _env_truthy("CLIPPY_DENY_WARNINGS", "0"):
                    clippy_cmd.extend(["--", "-D", "warnings"])
                r_clip = subprocess.run(
                    clippy_cmd,
                    cwd=str(hlig_dir),
                    capture_output=True,
                    timeout=timeout_sec,
                    text=True,
                )
                out_parts.append("\n--- cargo clippy ---\n" + (r_clip.stdout or ""))
                err_parts.append(r_clip.stderr or "")
                if r_clip.returncode != 0:
                    return False, "".join(out_parts), "".join(err_parts)

            r = subprocess.run(
                ["cargo", "build"],
                cwd=str(hlig_dir),
                capture_output=True,
                timeout=timeout_sec,
                text=True,
            )
            ok = r.returncode == 0
            out_parts.append("\n--- cargo build ---\n" + (r.stdout or ""))
            err_parts.append(r.stderr or "")
            return ok, "".join(out_parts), "".join(err_parts)

        # node-react
        subprocess.run(
            ["npm", "install"],
            cwd=str(hlig_dir),
            capture_output=True,
            timeout=timeout_sec,
            text=True,
        )
        out_n: list[str] = []
        err_n: list[str] = []

        if _env_truthy("ENABLE_LOCAL_TSC", "1") and _should_run_tsc_noemit(hlig_dir):
            r_ts = subprocess.run(
                ["npm", "exec", "--", "tsc", "--noEmit"],
                cwd=str(hlig_dir),
                capture_output=True,
                timeout=timeout_sec,
                text=True,
            )
            out_n.append("--- npm exec tsc --noEmit ---\n" + (r_ts.stdout or ""))
            err_n.append(r_ts.stderr or "")
            if r_ts.returncode != 0:
                return False, "\n".join(out_n), "\n".join(err_n)

        if _env_truthy("ENABLE_LOCAL_ESLINT", "1") and _should_run_eslint(hlig_dir):
            eslint_cmd = ["npm", "exec", "--", "eslint", "."]
            if _env_truthy("ESLINT_MAX_WARNINGS_ZERO", "0"):
                eslint_cmd.extend(["--max-warnings", "0"])
            r_es = subprocess.run(
                eslint_cmd,
                cwd=str(hlig_dir),
                capture_output=True,
                timeout=timeout_sec,
                text=True,
            )
            out_n.append("--- npm exec eslint . ---\n" + (r_es.stdout or ""))
            err_n.append(r_es.stderr or "")
            if r_es.returncode != 0:
                return False, "\n".join(out_n), "\n".join(err_n)

        r = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(hlig_dir),
            capture_output=True,
            timeout=timeout_sec,
            text=True,
        )
        out_n.append("--- npm run build ---\n" + (r.stdout or ""))
        err_n.append(r.stderr or "")
        ok = r.returncode == 0
        return ok, "\n".join(out_n), "\n".join(err_n)
    except subprocess.TimeoutExpired:
        return False, "", "build timed out"
    except FileNotFoundError:
        return False, "", "cargo or npm not found"
    except Exception as e:
        return False, "", str(e)


def _run_project_tests(hlig_dir: Path, framework: str, timeout_sec: int = 300) -> tuple[bool, str, str]:
    """Run automated tests (cargo test / npm test). Returns (success, stdout, stderr)."""
    if not hlig_dir.exists():
        return False, "", "directory does not exist"
    if framework == "rust-tauri":
        if timeout_sec == 300:
            timeout_sec = _RUST_BUILD_TIMEOUT_SEC
        try:
            r = subprocess.run(
                ["cargo", "test"],
                cwd=str(hlig_dir),
                capture_output=True,
                timeout=timeout_sec,
                text=True,
            )
            return r.returncode == 0, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            return False, "", "cargo test timed out"
        except FileNotFoundError:
            return False, "", "cargo not found"
        except Exception as e:
            return False, "", str(e)
    try:
        pkg_path = hlig_dir / "package.json"
        if not pkg_path.is_file():
            return True, "", "no package.json; skip tests"
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        scripts = pkg.get("scripts") or {}
        if "test" not in scripts:
            return True, "", "no npm test script; skip"
        r = subprocess.run(
            ["npm", "test"],
            cwd=str(hlig_dir),
            capture_output=True,
            timeout=timeout_sec,
            text=True,
        )
        return r.returncode == 0, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return False, "", "npm test timed out"
    except FileNotFoundError:
        return False, "", "npm not found"
    except Exception as e:
        return False, "", str(e)


BUILD_LOG_FILE = "build.log"


def _write_build_log(hlig_dir: Path, success: bool, stdout: str, stderr: str) -> None:
    """Append build result and output to build.log in the HLIG code directory."""
    log_path = hlig_dir / BUILD_LOG_FILE
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    status = "success" if success else "failure"
    block = f"\n{'='*60}\n[{ts}] Build {status}\n{'='*60}\n"
    if stdout:
        block += f"--- stdout ---\n{stdout}\n"
    if stderr:
        block += f"--- stderr ---\n{stderr}\n"
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(block)
    except Exception:
        pass


def _parse_build_failure_hints(stderr: str, framework: str) -> str:
    """Produce short, actionable hints from build stderr for the code generator retry."""
    err = (stderr or "").lower()
    hints = []
    if framework == "rust-tauri":
        if "does not have that feature" in err or "failed to select a version" in err or "which didn't match" in err:
            hints.append("Dependency error: use only the dependency versions and features from the implementation_brief (dependency_matrix). Do not use removed or non-existent crate features or version constraints.")
        if "no targets specified" in err or "src/main.rs" in err or "src/lib.rs" in err or "[lib] section" in err or "[[bin]]" in err:
            hints.append("Layout error: Cargo.toml must have [[bin]] (with path src/main.rs) or [lib] (with path src/lib.rs), and that source file must exist.")
        if "found at both" in err and "mod.rs" in err:
            hints.append("Module conflict: do not create both src/<name>.rs and src/<name>/mod.rs; use only one (either a single file src/X.rs or a directory src/X/mod.rs). Delete or rename the duplicate.")
        if "expected `;`, found" in err or "expected one of `!` or `::`, found" in err or "unexpected token" in err:
            hints.append("Syntax error: add semicolons after statement expressions where required; fix macro invocation placement and any stray tokens.")
        if "asexpression" in err and "uuid" in err and "binary" in err:
            hints.append(
                "Diesel + SQLite: do not map UUID columns to Binary with Rust `uuid::Uuid` unless schema and derives match. Prefer `Text` + `String` in models for SQLite IDs, or use a single pattern from implementation_brief (diesel_sqlite_rules)."
            )
        if "naivedatetime" in err and ("timestamp" in err or "fromsqlrow" in err) and "sqlite" in err:
            hints.append(
                "Diesel + SQLite: `NaiveDateTime` often does not match `Timestamp` on SQLite in generated code. Use `i64` for unix epoch, or `diesel::sql_types::TimestamptzSqlite`-compatible types per implementation_brief (diesel_sqlite_rules)."
            )
        if "queryable" in err and "uuid" in err and "binary" in err:
            hints.append(
                "Diesel + SQLite: align `schema.rs` sql_types with Rust field types (e.g. Text+String for IDs on SQLite). Follow diesel_sqlite_rules in implementation_brief."
            )
        if "cannotconnect" in err.replace("_", "") or "databaseerrorkind::cannotconnect" in err:
            hints.append(
                "Diesel 2.x: remove `DatabaseErrorKind::CannotConnect` — it does not exist. Match `diesel::result::Error` with documented variants only. For pool errors use `diesel::r2d2::Error` with `diesel::r2d2::Pool`, not standalone `r2d2::Error`. See diesel_sqlite_rules in implementation_brief."
            )
        if "failed to receive migrations" in err or "embed_migrations!" in err:
            hints.append(
                "Diesel migrations (preferred): remove `embed_migrations!` / `diesel_migrations` embed usage; add `migrations/` next to Cargo.toml with dated subfolders and `up.sql`, then apply each file with `diesel::connection::SimpleConnection::batch_execute` on `SqliteConnection` (see diesel_sqlite_rules). Legacy: if keeping embed, emit `migrations/` and fix macro syntax per implementation_brief."
            )
        if "macros that expand to items must" in err and "embed_migrations" in err:
            hints.append(
                "Prefer replacing `embed_migrations!` with file-based `batch_execute` migrations (diesel_sqlite_rules). If you must keep embed: end with **`embed_migrations!(\"migrations\");`** in submodules; do not use `embed_migrations!{\"migrations\"}`."
            )
        if ("embed_migrations" in err and "expected one of `!` or `::`" in err) or (
            "embed_migrations!();" in err.replace(" ", "")
        ):
            hints.append(
                "Prefer removing `embed_migrations!` and using `batch_execute` + on-disk `migrations/*/up.sql` (diesel_sqlite_rules). Legacy fix: `use diesel_migrations::embed_migrations;` and `embed_migrations!(\"migrations\");` — never empty `embed_migrations!()`."
            )
        if "not found in this scope" in err and "migrations" in err:
            hints.append(
                "If you removed embed: implement a small runner that reads `migrations/*/up.sql` and `batch_execute`s each. If you still use embed: ensure `diesel_migrations` is in Cargo.toml and `migrations/` exists so the macro expands."
            )
        if "osrng" in err.replace("_", "") and ("argon2" in err or "password_hash" in err or "password-hash" in err):
            hints.append(
                "Argon2/password-hash: add `rand_core` with features [\"getrandom\"] (or `rand` with OsRng) to Cargo.toml; use `use rand_core::OsRng` (or `rand::rngs::OsRng`). Do not use a broken `password_hash::rand_core::OsRng` import. See argon2_password_rules in implementation_brief."
            )
        if "salt" in err and "hash_password" in err and ("intosalt" in err.replace(" ", "").replace("'", "") or "into<salt" in err):
            hints.append(
                "Argon2: `hash_password` second argument must be a salt (e.g. `let salt = SaltString::generate(&mut OsRng);` then `.hash_password(password.as_bytes(), &salt)`), not `&mut OsRng`. See argon2_password_rules."
            )
        if "password_hash::error" in err or ("stderror" in err.replace("_", "") and "password_hash" in err):
            hints.append(
                "password_hash::Error with anyhow: use `.map_err(|e| anyhow::anyhow!(\"{e:?}\"))?` instead of bare `?`. Do not put anyhow::Error inside Box<dyn StdError> for Diesel. See argon2_password_rules."
            )
        if ("similar names, but are actually distinct types" in err and "diesel::r2d2" in err and "r2d2" in err) or (
            "expected `diesel::r2d2::error`" in err.replace("`", "").lower()
            and "found `r2d2::error`" in err.replace("`", "").lower()
        ):
            hints.append(
                "Use only diesel::r2d2 for Pool/ConnectionManager: `use diesel::r2d2::{ConnectionManager, Pool};` and `diesel::r2d2::Error`. Remove standalone `r2d2` crate from pool code or align all signatures to one error type."
            )
        if "vec<migrationversion" in err.replace("_", "").replace("'", "").lower():
            hints.append(
                "run_pending_migrations returns Vec<MigrationVersion> in Diesel 2: add .map(|_| ())? or change return type; do not use Result<(), _> without mapping."
            )
        if "borrow of moved value" in err or "e0382" in err:
            hints.append(
                "Rust ownership: clone config before moving into closures, use references, or restructure so values are not used after move (E0382)."
            )
        if "httpservicefactory" in err.replace("_", "").replace(" ", "").lower():
            hints.append(
                "Actix-web 4: use web::get().to(handler) with handler return types that implement Responder; check extractors and HttpResponse match the matrix actix-web version."
            )
        if "warp::reject::reject" in err and "not satisfied" in err:
            hints.append(
                "Warp: `warp::reject::custom(e)` needs `impl warp::reject::Reject for YourError {}` (empty impl) after `#[derive(Debug)]`. Do not store `std::io::Error` in a `Clone` derive — use `String` or map I/O to text. See warp_http_rules in implementation_brief."
            )
        if "is_internal" in err and "rejection" in err and "warp" in err:
            hints.append(
                "Warp Rejection: there is no `.is_internal()` on `warp::reject::Rejection` (0.3). Use `err.find::<YourError>()` to recover custom errors. See warp_http_rules."
            )
        if "temporary value dropped while borrowed" in err and ("with_status" in err or "warp" in err):
            hints.append(
                "Warp reply: use an owned `String` for `warp::reply::with_status(body, status)` (bind `let body = msg;` first). Do not pass `&e.to_string()` — it does not live long enough. See warp_http_rules."
            )
        if "io::error" in err and "clone" in err and "not satisfied" in err:
            hints.append(
                "Do not `#[derive(Clone)]` on enums/structs holding `std::io::Error`; use `String` (e.g. Io(String)) or omit Clone. See warp_http_rules."
            )
        if "unresolved import `anyhow`" in err or ("e0432" in err and "anyhow" in err):
            hints.append(
                "Add `anyhow = \"1\"` (or matrix version) to Cargo.toml if you `use anyhow::...`. See dependency list in implementation_brief."
            )
        if "unresolved import `clap`" in err or ("cannot find attribute `command`" in err and "clap" in err):
            hints.append(
                "Add `clap` with `features = [\"derive\"]` to Cargo.toml if you use `#[derive(Parser)]` / clap attributes. See dependency list in implementation_brief."
            )
        if "is not a future" in err and "result" in err:
            hints.append(
                "Async: only `.await` things that are `Future` (e.g. async fn calls). `Result` is not a Future — use `?` or `match`, not `.await` on plain `Result`."
            )
        if "--- cargo clippy ---" in err or ("clippy" in err and "error" in err):
            hints.append(
                "cargo clippy: fix reported lints. Set CLIPPY_DENY_WARNINGS=0 (default) for warn-only clippy, or ENABLE_LOCAL_CLIPPY=0 to skip clippy."
            )
        if "doesnotsupportreturningclause" in err.replace("_", "") or "no valid sql fragment for the `sqlite`" in err:
            hints.append(
                "Diesel + SQLite: avoid `.returning(...).get_result()` unless Cargo.toml `diesel` includes feature `returning_clauses_for_sqlite_3_35` (matrix default) AND returning columns exactly match the struct; or use `execute()` then a separate `select` by primary key."
            )
        if "compatibletype" in err.replace("_", "") and "selectby" in err:
            hints.append(
                "Diesel: struct fields must match query/returning columns (order, count, types). Use `#[derive(Selectable)]`, `Model::as_select()`, or explicit `.select()` lists aligned with Queryable/Insertable. See diesel_sqlite_rules."
            )
        if "cannot find derive macro `insertable`" in err or "cannot find derive macro `queryable`" in err:
            hints.append(
                "Diesel: add `use diesel::prelude::*;` at the top of the module (or explicit `use diesel::{Insertable, Queryable, ...}`). Derive macros must be in scope."
            )
        if "crate, not an attribute" in err and "diesel" in err:
            hints.append(
                "Diesel: `#[diesel(...)]` requires the struct to derive Diesel traits (Insertable, Queryable, etc.) and `use diesel::prelude::*` so derive macros resolve."
            )
    if framework == "node-react":
        if "cannot find name 'vi'" in err or "cannot find name 'jest'" in err or "cannot use namespace 'jest' as a value" in err:
            hints.append("Tests: use Vitest (vi) only, not Jest (jest). Import or use vi from 'vitest'. Ensure build script does not run tsc on test files, or exclude test files from tsconfig include.")
        if "has no exported member" in err or "does not exist on type" in err and "fetch" in err:
            hints.append("API client: export from api/client (or equivalent) every function that hooks and components import (e.g. fetchAboutContent, fetchMenuPdf).")
        if "testing-library__jest-dom" in err or "property assignment expected" in err and "tsconfig" in err:
            hints.append("tsconfig: use valid JSON; do not reference type packages that are not installed (e.g. testing-library__jest-dom). Include only 'vite/client' for import.meta.env.")
        if 'could not resolve entry module "index.html"' in err:
            hints.append("Vite entrypoint: ensure there is an index.html at the project root that references the correct entry script (for example, <script type=\"module\" src=\"/src/main.tsx\"></script>). Do not move or remove index.html.")
        if "name the file with the .jsx" in err or "name the file with the .tsx" in err:
            hints.append(
                "Vite/React: any file containing JSX must use extension .jsx or .tsx (not .js). Rename the file and update imports (e.g. src/index.js -> src/index.jsx; index.html script src must match)."
            )
        if "failed to parse source for import analysis" in err and "jsx" in err:
            hints.append(
                "Vite: JSX in a .js file is invalid. Use .jsx/.tsx for components and entry, or output plain JS without JSX syntax."
            )
        if (
            "failed to load config" in err
            or "dynamic require" in err.lower()
            or "commonjs-variable-in-esm" in err.replace("_", "").lower()
            or ("module.exports" in err and "vite.config" in err.replace(" ", "").lower())
        ):
            hints.append(
                "Vite config: if package.json has \"type\": \"module\", use ESM in vite.config.js (import { defineConfig } from 'vite'; export default defineConfig({...})). "
                "Do not use module.exports in vite.config.js — rename to vite.config.cjs for CommonJS or switch the config to import/export."
            )
        if "default" in err and "is not exported" in err and ("rollup" in err or "vite" in err or ".tsx" in err or ".jsx" in err):
            hints.append(
                "ES modules: match default vs named imports — use `import { useX } from './hooks/useX'` if the module uses named exports, or add `export default` for default imports."
            )
        if (
            "npx tsc" in err
            or "npm exec" in err and "tsc" in err
            or "tsc --noemit" in err.replace(" ", "").lower()
            or "ts18003" in err.replace(" ", "")
            or "no inputs were found in config file" in err
        ):
            hints.append(
                "TypeScript: fix `tsc --noEmit` errors (types, imports). If TS18003 / no inputs: set tsconfig \"include\" to [\"src\"] (not broken globs like src*.jsx), enable allowJs when using .js/.jsx under src, and ensure source files exist. Set ENABLE_LOCAL_TSC=0 to skip the typecheck step during local build."
            )
        if "err_module_not_found" in err.replace(" ", "").replace("\n", "") and "typescript-eslint" in err:
            hints.append(
                "ESLint flat config: add devDependency `typescript-eslint` (and pin `eslint` ^8 per matrix) so `import ... from 'typescript-eslint'` resolves after npm install."
            )
        if "ebadengine" in err.replace(" ", "").replace("\n", "") or "engine eslint" in err or ("eslint@" in err and "node" in err and "required" in err):
            hints.append(
                "ESLint / Node: pin eslint to ^8.57 in devDependencies for Node 18; do not rely on npx pulling ESLint 9+/10 (Node 20+). Use npm exec eslint after npm install."
            )
        if "npx eslint" in err or "npm exec" in err and "eslint" in err or ("eslint" in err and "error" in err):
            hints.append(
                "ESLint: fix reported issues or align config with the matrix. Set ENABLE_LOCAL_ESLINT=0 to skip, or ESLINT_MAX_WARNINGS_ZERO=0 to allow warnings."
            )
    # Common Tauri v2 config/feature errors (surface regardless of framework flag when stderr includes them)
    if "unknown field `devpath`" in stderr or "unknown field `devPath`" in stderr:
        hints.append("Tauri config: tauri.conf.json must use only fields from the current Tauri 2 schema. Remove legacy keys such as 'devPath' and prefer 'devUrl' or 'frontendDist' as specified in the template.")
    if "identifier\" is a required property" in stderr:
        hints.append("Tauri config: set a valid 'identifier' field in tauri.conf.json (e.g. 'com.example.app'); it is required by the schema.")
    if "depends on `tauri` with feature `api-all`" in stderr or "depends on `tauri` with feature `ipc-all`" in stderr:
        hints.append("Tauri features: remove non-existent features like 'api-all' or 'ipc-all' from the tauri dependency. Use only features allowed by the dependency matrix.")
    if not hints:
        return ""
    return "Hints to fix: " + " ".join(hints)


# Tauri features known to be invalid in Tauri 2.x (removed or never existed)
_TAURI_FORBIDDEN_FEATURES = frozenset({"disable-devtools", "shell-open", "api-all", "ipc-all"})


def _format_cargo_diesel_dep(diesel_spec: Any) -> str | None:
    """Single-line Cargo.toml dependency entry for diesel from matrix YAML."""
    if isinstance(diesel_spec, dict):
        ver = str(diesel_spec.get("version", "2")).strip()
        feats = diesel_spec.get("features") or []
        if isinstance(feats, list) and feats:
            inner = ", ".join(f'"{f}"' for f in feats if f)
            return f'diesel = {{ version = "{ver}", features = [{inner}] }}'
        return f'diesel = "{ver}"'
    if diesel_spec:
        return f'diesel = "{str(diesel_spec).strip()}"'
    return None


def _fix_embed_migrations_line(line: str) -> str:
    """
    Normalize a single line containing embed_migrations!.
    - Submodules (e.g. src/db/connection.rs): rustc requires a trailing semicolon between items
      ("macros that expand to items must be ... followed by a semicolon").
    - Crate roots: `embed_migrations!("migrations");` is also valid — do not strip semicolons.
    - Fix bad rustc suggestions: `embed_migrations!{"migrations"}` -> `embed_migrations!("migrations");`
    """
    if "embed_migrations!" not in line:
        return line
    ending = "\n" if line.endswith("\n") else ""
    s = line[:-1] if line.endswith("\n") else line

    if re.search(r"embed_migrations!\s*\{", s):
        s = re.sub(r'embed_migrations!\s*\{\s*"([^"]+)"\s*\}\s*;?', r'embed_migrations!("\1");', s)

    m = re.search(r'embed_migrations!\(\s*"([^"]+)"\s*\)', s)
    if not m:
        return s + ending
    pos_after_paren = m.end()
    tail = s[pos_after_paren:].lstrip()
    if tail.startswith(";"):
        return s + ending
    s = s[:pos_after_paren] + ";" + s[pos_after_paren:]
    return s + ending


def _normalize_rust_embed_migrations(hlig_dir: Path) -> None:
    """
    Fix common LLM mistakes around Diesel embed_migrations! (legacy — preferred pattern is
    file-based migrations + SimpleConnection::batch_execute, no diesel_migrations embed):
    - Replace legacy #[macro_use] extern crate diesel_migrations; with use diesel_migrations::embed_migrations;
    - Ensure embed_migrations!("..."); has a trailing semicolon in submodule-friendly form.
    """
    src = hlig_dir / "src"
    if not src.is_dir():
        return
    for fp in src.rglob("*.rs"):
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        orig = text
        text = re.sub(
            r"#\[macro_use\]\s*\n\s*extern\s+crate\s+diesel_migrations\s*;",
            "use diesel_migrations::embed_migrations;",
            text,
            flags=re.MULTILINE,
        )
        text = re.sub(
            r"#\[macro_use\]\s*extern\s+crate\s+diesel_migrations\s*;",
            "use diesel_migrations::embed_migrations;",
            text,
        )
        lines = text.splitlines(keepends=True)
        text = "".join(_fix_embed_migrations_line(line) if "embed_migrations!" in line else line for line in lines)
        if text != orig:
            try:
                fp.write_text(text, encoding="utf-8")
            except OSError:
                pass


def _apply_dependency_matrix_to_cargo(hlig_dir: Path, matrix: dict) -> None:
    """
    Override Cargo.toml dependencies to match the dependency matrix (rust-tauri only).
    Fixes common LLM mistakes: wrong tauri-build version, invalid tauri features (e.g. disable-devtools).
    """
    cargo = hlig_dir / "Cargo.toml"
    if not cargo.exists():
        return
    section = matrix.get("rust-tauri")
    if not isinstance(section, dict):
        return
    deps = section.get("dependencies")
    if not isinstance(deps, dict):
        return

    def _version_str(key: str) -> str:
        v = deps.get(key)
        if v is None:
            return ""
        if isinstance(v, dict):
            return str(v.get("version", "")).strip() or ""
        return str(v).strip()

    tauri_ver = _version_str("tauri") or "2.0"
    tauri_build_ver = _version_str("tauri-build") or "2.5"

    content = cargo.read_text(encoding="utf-8")
    changed = False

    # Replace tauri-build = "..." or tauri-build = { ... } with matrix version
    prev = content
    content = re.sub(
        r'tauri-build\s*=\s*["\'][^"\']*["\']',
        f'tauri-build = "{tauri_build_ver}"',
        content,
    )
    content = re.sub(
        r'tauri-build\s*=\s*\{[^}]*\}',
        f'tauri-build = {{ version = "{tauri_build_ver}" }}',
        content,
    )
    if content != prev:
        changed = True

    # Replace tauri = "..." with matrix version
    prev = content
    content = re.sub(
        r'tauri\s*=\s*["\'][^"\']*["\']',
        f'tauri = "{tauri_ver}"',
        content,
    )
    if content != prev:
        changed = True

    # Replace tauri = { version = "X", features = [...] } with matrix version and strip forbidden features
    def replace_tauri_inline(m: re.Match) -> str:
        inner = m.group(1)
        # Replace version
        inner = re.sub(r'version\s*=\s*["\'][^"\']*["\']', f'version = "{tauri_ver}"', inner)
        # Remove forbidden features from features = [...]
        def strip_features(fm: re.Match) -> str:
            feats = fm.group(1)
            parts = [p.strip().strip('"\'') for p in re.split(r"[,]", feats) if p.strip()]
            kept = [p for p in parts if p and p not in _TAURI_FORBIDDEN_FEATURES]
            if not kept:
                return "features = []"
            return "features = [\"" + "\", \"".join(kept) + "\"]"
        inner = re.sub(r'features\s*=\s*\[([^\]]*)\]', strip_features, inner)
        return f"tauri = {{ {inner} }}"

    prev = content
    content = re.sub(r'tauri\s*=\s*\{([^}]*)\}', replace_tauri_inline, content)
    if content != prev:
        changed = True

    # Normalize diesel = ... to matrix (pins features e.g. returning_clauses_for_sqlite_3_35)
    diesel_line = _format_cargo_diesel_dep(deps.get("diesel"))
    if diesel_line and re.search(r"(?m)^\s*diesel\s*=", content):
        prev = content
        new_c = re.sub(
            r"(?m)^\s*diesel\s*=\s*\{[^\n]+\}\s*$",
            diesel_line,
            content,
            count=1,
        )
        if new_c == content:
            new_c = re.sub(
                r'(?m)^\s*diesel\s*=\s*"[^"]+"\s*$',
                diesel_line,
                content,
                count=1,
            )
        if new_c != content:
            content = new_c
            changed = True

    if changed:
        cargo.write_text(content, encoding="utf-8")


def _file_likely_contains_jsx(content: str) -> bool:
    """Heuristic: true if file content looks like JSX (Vite cannot parse JSX in .js without renaming)."""
    if "<" not in content:
        return False
    # React components: <App, <React.StrictMode
    if re.search(r"<\s*[A-Z][A-Za-z0-9_]*", content):
        return True
    # Common lowercase HTML tags in JSX
    if re.search(
        r"<\s*(?:div|span|p|h[1-6]|ul|ol|li|a|button|form|input|label|textarea|select|option|"
        r"section|main|header|footer|nav|article|table|thead|tbody|tr|td|th|svg|path|img|br|hr)\b",
        content,
    ):
        return True
    return False


def _normalize_node_react_jsx_files(hlig_dir: Path) -> None:
    """
    Rename src/**/*.js that contain JSX to .jsx and update import paths in the project.
    Vite's default pipeline rejects JSX inside .js files (see vite:build-import-analysis).
    """
    src = hlig_dir / "src"
    if not src.is_dir():
        return
    renames: list[tuple[Path, Path]] = []
    for path in sorted(src.rglob("*.js"), key=lambda p: -len(str(p))):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not _file_likely_contains_jsx(content):
            continue
        new_path = path.with_suffix(".jsx")
        if new_path.exists():
            continue
        renames.append((path, new_path))
    if not renames:
        return
    for old_p, new_p in renames:
        try:
            old_p.rename(new_p)
        except OSError:
            continue
    try:
        rel_pairs = [(o.relative_to(hlig_dir).as_posix(), n.relative_to(hlig_dir).as_posix()) for o, n in renames]
    except ValueError:
        return
    rel_pairs.sort(key=lambda t: len(t[0]), reverse=True)
    text_suffixes = frozenset({".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".mjs", ".cjs", ".css"})
    skip_dir = frozenset({"node_modules", "dist", ".git"})

    for fpath in hlig_dir.rglob("*"):
        if not fpath.is_file():
            continue
        if any(part in skip_dir for part in fpath.parts):
            continue
        suf = fpath.suffix.lower()
        if suf not in text_suffixes and not fpath.name.startswith("vite.config."):
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
        except OSError:
            continue
        orig = text
        for old_pos, new_pos in rel_pairs:
            if old_pos in text:
                text = text.replace(old_pos, new_pos)
        if text != orig:
            try:
                fpath.write_text(text, encoding="utf-8")
            except OSError:
                pass


_NPM_DEV_NAMES = frozenset(
    {
        "vite",
        "vitest",
        "typescript",
        "eslint",
        "typescript-eslint",
        "prettier",
        "@testing-library/react",
        "@testing-library/user-event",
    }
)
_NPM_DEV_PREFIXES = ("@types/", "@typescript-eslint/", "@vitejs/")


def _npm_use_dev_dependencies(name: str) -> bool:
    if name in _NPM_DEV_NAMES:
        return True
    return any(name.startswith(p) for p in _NPM_DEV_PREFIXES)


def _merge_npm_matrix_into_package(pkg: dict, matrix: dict) -> bool:
    """Pin dependency and devDependency versions from agents/config/dependency_matrix.yaml (node-react)."""
    section = matrix.get("node-react")
    if not isinstance(section, dict):
        return False
    dev_block = section.get("dev_dependencies")
    prod_block = section.get("dependencies")
    pkg_deps = pkg.setdefault("dependencies", {})
    pkg_dev = pkg.setdefault("devDependencies", {})
    changed = False

    def apply_version(bucket: str, key: str, raw_ver: str) -> None:
        nonlocal changed
        ver = raw_ver.strip()
        if not ver or not isinstance(key, str):
            return
        if bucket == "dev":
            pkg_deps.pop(key, None)
            if pkg_dev.get(key) != ver:
                pkg_dev[key] = ver
                changed = True
        else:
            pkg_dev.pop(key, None)
            if pkg_deps.get(key) != ver:
                pkg_deps[key] = ver
                changed = True

    if isinstance(dev_block, dict):
        for k, v in dev_block.items():
            if v is None:
                continue
            apply_version("dev", k, str(v))

    if isinstance(prod_block, dict):
        for k, v in prod_block.items():
            if v is None:
                continue
            if isinstance(dev_block, dict) and k in dev_block:
                continue
            if isinstance(dev_block, dict):
                apply_version("prod", k, str(v))
            else:
                bucket = "dev" if _npm_use_dev_dependencies(k) else "prod"
                apply_version(bucket, k, str(v))

    return changed


def _apply_dependency_matrix_to_node_project(hlig_dir: Path, matrix: dict) -> None:
    """
    Defensive fixes for generated Node/React/TS projects so npm run build is more likely to succeed.
    - Merge pinned versions from dependency_matrix (node-react) into package.json.
    - Force build script to 'vite build' only (avoid tsc failing on test files).
    - Ensure tsconfig.json exists and is valid; remove invalid type refs (e.g. testing-library__jest-dom), exclude tests.
    """
    pkg_path = hlig_dir / "package.json"
    if not pkg_path.exists():
        return
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except Exception:
        return
    npm_merged = _merge_npm_matrix_into_package(pkg, matrix)
    scripts = pkg.get("scripts") or {}
    build_script = (scripts.get("build") or "").strip()
    script_changed = False
    if build_script and "tsc" in build_script:
        scripts["build"] = "vite build"
        script_changed = True
    # Strip "|| echo ..." fallbacks so build fails when the real command fails (no false success)
    for key in ("build", "dev", "start"):
        val = (scripts.get(key) or "").strip()
        if "|| echo" in val or "|| true" in val:
            scripts[key] = val.split("||")[0].strip()
            script_changed = True
    if script_changed:
        pkg["scripts"] = scripts
    if npm_merged or script_changed:
        pkg_path.write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    tsconfig_path = hlig_dir / "tsconfig.json"
    try:
        raw = tsconfig_path.read_text(encoding="utf-8") if tsconfig_path.exists() else ""
        # Strip JSONC comments for parsing
        raw_stripped = re.sub(r"//[^\n]*", "", raw)
        raw_stripped = re.sub(r"/\*[\s\S]*?\*/", "", raw_stripped)
        cfg = json.loads(raw_stripped) if raw_stripped.strip() else {}
    except Exception:
        cfg = {}
    comp = cfg.get("compilerOptions") or {}
    types = comp.get("types")
    if isinstance(types, list):
        types = [t for t in types if t and "testing-library__jest-dom" not in str(t) and "jest" not in str(t).lower()]
        if "vite/client" not in types:
            types.append("vite/client")
        comp["types"] = types
    else:
        comp["types"] = ["vite/client"]
    comp.setdefault("target", "ES2020")
    comp.setdefault("module", "ESNext")
    comp.setdefault("moduleResolution", "bundler")
    comp.setdefault("strict", True)
    cfg["compilerOptions"] = comp
    if "exclude" not in cfg:
        cfg["exclude"] = ["node_modules", "dist", "**/__tests__/**", "**/*.test.*", "**/*.spec.*"]
    elif isinstance(cfg["exclude"], list):
        for pat in ("**/__tests__/**", "**/*.test.*", "**/*.spec.*"):
            if pat not in cfg["exclude"]:
                cfg["exclude"].append(pat)
    if "include" not in cfg:
        cfg["include"] = ["src"]
    _normalize_tsconfig_vite_inputs(hlig_dir, cfg)
    tsconfig_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

    _ensure_eslint_flat_config_peers(hlig_dir, matrix)

    # Vite + React: JSX in *.js breaks the build — rename to .jsx and fix import paths.
    try:
        pkg2 = json.loads(pkg_path.read_text(encoding="utf-8"))
    except Exception:
        pkg2 = pkg
    scripts2 = pkg2.get("scripts") or {}
    deps_blob = json.dumps(pkg2.get("dependencies", {})) + json.dumps(pkg2.get("devDependencies", {}))
    build_low = (scripts2.get("build") or "").lower()
    if "vite" in build_low or "react" in deps_blob.lower():
        _normalize_node_react_jsx_files(hlig_dir)

    # Ensure a minimal Vite entry HTML exists so `vite build` can resolve "index.html".
    # We infer the most likely entry script from existing files under src/.
    index_html = hlig_dir / "index.html"
    if not index_html.exists():
        src_dir = hlig_dir / "src"
        candidates = [
            "main.tsx",
            "main.jsx",
            "main.js",
            "index.tsx",
            "index.jsx",
            "index.js",
        ]
        entry = None
        if src_dir.exists():
            for name in candidates:
                cand = src_dir / name
                if cand.exists():
                    entry = f"/src/{name}"
                    break
        if entry is None:
            # Fallback to a sensible default that Vite + React commonly use.
            entry = "/src/main.tsx"
        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>HLIG Frontend</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="{entry}"></script>
  </body>
</html>
"""
        try:
            index_html.write_text(html, encoding="utf-8")
        except Exception:
            # If we cannot write index.html, let the build error surface; hints will still guide fixes.
            pass


def _topological_order(dtg: DTGGraph) -> list[dict]:
    """Return DTG nodes in topological order (designs before code)."""
    import networkx as nx

    try:
        from networkx.exception import NetworkXUnfeasible
    except ImportError:
        NetworkXUnfeasible = type("NetworkXUnfeasible", (Exception,), {})

    d = dtg.to_dict()
    nodes = d.get("nodes", [])
    edges = d.get("edges", [])
    nodes_by_id = {n["id"]: n for n in nodes if n.get("id")}
    g = nx.DiGraph()
    for n in nodes:
        g.add_node(n["id"])
    for e in edges:
        src, tgt = e.get("from"), e.get("to")
        if src and tgt:
            g.add_edge(src, tgt)
    try:
        order = list(nx.topological_sort(g))
    except (nx.NetworkXError, NetworkXUnfeasible):
        order = list(nodes_by_id.keys())
    return [nodes_by_id[nid] for nid in order if nid in nodes_by_id]


def _safe_filename(name: str) -> str:
    """Convert title to safe filename."""
    return re.sub(r"[^\w\-_]", "_", name).strip("_") or "untitled"


def _is_contract_hlig_node(attrs: dict) -> bool:
    kind = str(attrs.get("kind") or "").strip().lower()
    if kind:
        return kind == "contract"
    return (attrs.get("node_type") or "").lower() == "contract"


def _get_interfaces_for_hlig(hlig_graph: HLIGGraph, hlig_id: str) -> list[dict]:
    """Extract interface definitions from HLIG contract nodes and edges involving this HLIG."""
    result: list[dict] = []
    for nid, attrs in hlig_graph.nodes():
        if not _is_contract_hlig_node(attrs):
            continue
        impl_by = attrs.get("implemented_by") or []
        cons = attrs.get("consumers") or impl_by
        if not isinstance(cons, list):
            cons = []
        prod = attrs.get("producer")
        if hlig_id != prod and hlig_id not in cons:
            continue
        schema = {}
        if isinstance(attrs.get("source_of_truth"), dict):
            schema = dict(attrs.get("source_of_truth") or {})
        elif isinstance(attrs.get("schema"), dict):
            schema = dict(attrs.get("schema") or {})
        result.append(
            {
                "from": prod,
                "to": list(cons),
                "interface_type": "contract",
                "contract_node_id": nid,
                "name": attrs.get("name") or attrs.get("title"),
                "version": attrs.get("version") or schema.get("version"),
                **{k: v for k, v in schema.items() if k in ("type", "description", "endpoints", "schema", "ref")},
            }
        )
    for u, v, data in hlig_graph.edges():
        if u != hlig_id and v != hlig_id:
            continue
        spec = data.get("interface_spec")
        ref = data.get("interface_ref")
        data_spec = data.get("data_spec")
        if spec and isinstance(spec, dict):
            result.append({
                "from": u, "to": v,
                "interface_type": data.get("interface_type", "dependency"),
                **{k: v for k, v in spec.items() if k in ("type", "description", "endpoints", "schema", "ref")},
            })
        elif data_spec and isinstance(data_spec, dict):
            result.append(
                {
                    "from": u,
                    "to": v,
                    "interface_type": "data-flow",
                    "output_ref": data_spec.get("output_ref"),
                    "input_ref": data_spec.get("input_ref"),
                }
            )
        elif ref:
            result.append({"from": u, "to": v, "interface_ref": ref, "interface_type": data.get("interface_type", "dependency")})
    return result


def _write_interface_definitions(hlig_graph: HLIGGraph, outputs_dir: Path) -> None:
    """
    Extract interface_spec from HLIG edges and write to shared/interfaces.json.
    Both Frontend and Backend can read this file during code generation.
    """
    by_edge: dict[str, dict] = {}
    by_contract: dict[str, dict] = {}
    for nid, attrs in hlig_graph.nodes():
        if not _is_contract_hlig_node(attrs):
            continue
        source = attrs.get("source_of_truth")
        schema = source if isinstance(source, dict) else attrs.get("schema")
        by_contract[str(nid)] = {
            "id": nid,
            "name": attrs.get("name") or attrs.get("title") or nid,
            "contract_type": attrs.get("contract_type") or "contract",
            "source_of_truth": schema if isinstance(schema, dict) else {},
            "implemented_by": list(attrs.get("implemented_by") or attrs.get("consumers") or []),
            "producer": attrs.get("producer"),
        }
    for u, v, data in hlig_graph.edges():
        spec = data.get("interface_spec")
        ref = data.get("interface_ref")
        data_spec = data.get("data_spec")
        if spec and isinstance(spec, dict):
            key = f"{u}→{v}"
            by_edge[key] = {
                "from": u,
                "to": v,
                "interface_type": data.get("interface_type", "dependency"),
                **{k: v for k, v in spec.items() if k in ("type", "description", "endpoints", "schema", "ref")},
            }
        elif ref:
            key = f"{u}→{v}"
            by_edge[key] = {"from": u, "to": v, "interface_ref": ref, "interface_type": data.get("interface_type", "dependency")}
        elif data_spec and isinstance(data_spec, dict):
            key = f"{u}→{v}"
            by_edge[key] = {
                "from": u,
                "to": v,
                "interface_type": "data-flow",
                "output_ref": data_spec.get("output_ref"),
                "input_ref": data_spec.get("input_ref"),
            }
    if not by_edge and not by_contract:
        return
    shared_dir = outputs_dir / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    out = {"by_edge": by_edge, "by_contract": by_contract}
    (shared_dir / "interfaces.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")


class DTGArtifactGenerator:
    """Generates design documents and code from DTG nodes."""

    def __init__(self, prompts_dir: Path | None = None):
        self.prompts_dir = prompts_dir or (PROJECT_ROOT / "prompts")

    def _load_prompt(self, name: str) -> str:
        path = self.prompts_dir / f"{name}.md"
        if path.exists():
            return path.read_text()
        return ""

    def _call_llm(self, prompt: str, input_data: dict, session_id: str = "", agent_name: str = "artifact_gen") -> str:
        check_cost_limit_before_llm(session_id)
        log_llm_input(session_id, agent_name, input_data)
        try:
            from core.model_manager import ModelManager
            from core.debug_logger import log_llm_call
        except ImportError:
            return ""

        try:
            mm = ModelManager()
            variable_input = json.dumps(input_data, indent=2, default=str)
            full_prompt = f"{prompt.strip()}\n\n## Input\n\n```json\n{variable_input}\n```"
            text, usage = mm.generate_text(full_prompt)
            if session_id:
                log_llm_call(
                    session_id, agent_name, full_prompt, text,
                    usage=usage._asdict() if usage else None,
                    variable_input=variable_input,
                )
            return text
        except Exception as e:
            log_pipeline_event(session_id, "artifact_generation_error", {"error": str(e)})
            if isinstance(e, CostLimitExceeded):
                raise
            return ""

    def _generate_design_doc(
        self,
        node: dict,
        dependency_context: dict[str, str],
        session_id: str,
        causal_path: list[dict] | None = None,
        causal_parent_context: dict[str, str] | None = None,
        interface_definitions: list[dict] | None = None,
    ) -> str:
        """
        Generate canonical design spec (JSON) for a DTG design node.
        Output is for LLM consumption (code/test generation). CVP: causal_path and causal_parent_context.
        """
        prompt = self._load_prompt("design_doc_generator")
        if not prompt:
            return ""

        input_data = {
            "dtg_node": {k: v for k, v in node.items()},
            "dependency_context": dependency_context,
        }
        if causal_path:
            input_data["causal_path"] = causal_path
        if causal_parent_context:
            input_data["causal_parent_context"] = causal_parent_context
        if interface_definitions:
            input_data["interface_definitions"] = interface_definitions
        try:
            raw = self._call_llm(prompt, input_data, session_id).strip()
        except CostLimitExceeded:
            # Propagate cost-limit errors so the runner can stop the pipeline.
            raise
        # Parse and re-serialize to ensure valid JSON; fallback to raw if parse fails
        try:
            from core.json_parser import parse_llm_json, JsonParsingError
            # Enforce minimal contract: we must at least have a "type" field.
            parsed = parse_llm_json(raw, required_keys=["type"])
            if isinstance(parsed, dict) and parsed.get("type") == "design_spec":
                return json.dumps(parsed, indent=2, default=str)
        except JsonParsingError as e:
            log_pipeline_event(
                session_id,
                "design_json_validation_error",
                {"node": node.get("id", ""), "error": str(e)},
            )
        except Exception:
            # Swallow non-validation errors here and fall back to raw content.
            pass
        return raw

    def _generate_code(
        self,
        node: dict,
        framework: str,
        dependency_context: dict[str, str],
        session_id: str,
        causal_path: list[dict] | None = None,
        causal_parent_context: dict[str, str] | None = None,
        interface_definitions: list[dict] | None = None,
        compile_errors: str | None = None,
        design_docs_available: bool = True,
        implementation_brief: str | None = None,
        previous_attempt_files: dict[str, str] | None = None,
        same_node_prior_files: dict[str, str] | None = None,
        mechanical_validation_errors: str | None = None,
        codegen_phase: str | None = None,
        current_file_skeleton: str | None = None,
        module_interface_snapshots: list[dict[str, str]] | None = None,
        shared_interfaces_excerpt: str | None = None,
    ) -> dict[str, str]:
        """
        Generate code files for a DTG code node. Returns {path: content}.
        CVP: causal_path and causal_parent_context restrict/annotate context (Markov blanket).
        When compile_errors is set, the model should fix the code to resolve the build output.
        previous_attempt_files: full file contents from the last failed build (plus manifests); required for targeted fixes.
        implementation_brief: full design-based prompt text for clarity; use when provided.
        """
        prompt = self._load_prompt("code_generator")
        if not prompt:
            return {}

        input_data = {
            "dtg_node": {k: v for k, v in node.items()},
            "framework": framework,
            "dependency_context": dependency_context,
            "design_docs_available": design_docs_available,
        }
        files_owned = node.get("files_owned")
        if isinstance(files_owned, list):
            input_data["files_owned"] = files_owned
        if causal_path:
            input_data["causal_path"] = causal_path
        if causal_parent_context:
            input_data["causal_parent_context"] = causal_parent_context
        if interface_definitions:
            input_data["interface_definitions"] = interface_definitions
        if compile_errors:
            input_data["compile_errors"] = compile_errors
        if previous_attempt_files:
            input_data["previous_attempt_files"] = previous_attempt_files
        if implementation_brief:
            input_data["implementation_brief"] = implementation_brief
        if same_node_prior_files:
            input_data["same_node_prior_files"] = {
                k: _truncate_same_node_prior(v) for k, v in sorted(same_node_prior_files.items())
            }
        if mechanical_validation_errors:
            input_data["mechanical_validation_errors"] = mechanical_validation_errors
        if codegen_phase:
            input_data["codegen_phase"] = codegen_phase
        if current_file_skeleton is not None:
            input_data["current_file_skeleton"] = current_file_skeleton
        if module_interface_snapshots:
            input_data["module_interface_snapshots"] = build_snapshots_payload(module_interface_snapshots)
        if shared_interfaces_excerpt:
            input_data["shared_interfaces_json_excerpt"] = shared_interfaces_excerpt
        try:
            response = self._call_llm(prompt, input_data, session_id)
        except CostLimitExceeded:
            # Bubble up cost limit so the runner can terminate cleanly.
            raise

        try:
            from core.json_parser import parse_llm_json, JsonParsingError
        except ImportError:
            return {}

        try:
            # Enforce minimal contract: expect a top-level "files" array.
            parsed = parse_llm_json(response, required_keys=["files"])
            files = parsed.get("files", [])
            if not isinstance(files, list):
                raise JsonParsingError("`files` must be a list")
            result: dict[str, str] = {}
            for f in files:
                if isinstance(f, dict) and f.get("path"):
                    result[f["path"]] = f.get("content", "")
            return result
        except JsonParsingError as e:
            log_pipeline_event(
                session_id,
                "code_json_validation_error",
                {"node": node.get("id", ""), "error": str(e)},
            )
            return {}
        except Exception:
            return {}

    def _review_ig_file(
        self,
        session_id: str,
        *,
        dtg_node: dict,
        file_path: str,
        content: str,
        framework: str,
    ) -> tuple[str, str]:
        if not _ig_code_review_enabled():
            return "pass", ""
        prompt = self._load_prompt("code_reviewer")
        if not prompt:
            return "pass", ""
        max_chars = _ctx_limit("IG_REVIEW_FILE_MAX_CHARS", 24_000)
        trimmed = content if max_chars <= 0 else content[:max_chars]
        input_data = {
            "review_scope": "single_file",
            "framework": framework,
            "file_path": file_path,
            "file_content": trimmed,
            "dtg_node": {
                "id": dtg_node.get("id", ""),
                "title": dtg_node.get("title", ""),
                "description": (dtg_node.get("description") or "")[:4000],
            },
        }
        raw = self._call_llm(prompt, input_data, session_id, agent_name="code_reviewer_ig")
        return _parse_code_reviewer_status(raw), raw

    def _scaffold_project(self, hlig_dir: Path, framework: str, hlig_node: dict) -> None:
        """Create minimal package.json or Cargo.toml so project is buildable. For Rust, ensure [[bin]] and src/main.rs exist."""
        if framework == "node-react":
            _prune_empty_rust_scaffold(hlig_dir)
        if framework == "rust-tauri":
            cargo = hlig_dir / "Cargo.toml"
            name = _safe_filename(hlig_node.get("id", "app")).lower()
            if not cargo.exists():
                cargo.write_text(f'''[package]
name = "{name}"
version = "0.1.0"
edition = "2021"

[[bin]]
name = "app"
path = "src/main.rs"

[dependencies]
''', encoding="utf-8")
                rtc = hlig_dir / "rust-toolchain.toml"
                if not rtc.exists():
                    rtc.write_text(
                        '[toolchain]\nchannel = "stable"\n',
                        encoding="utf-8",
                    )
            else:
                content = cargo.read_text(encoding="utf-8")
                if "[[bin]]" not in content and "[lib]" not in content:
                    cargo.write_text(content.rstrip() + "\n\n[[bin]]\nname = \"app\"\npath = \"src/main.rs\"\n", encoding="utf-8")
            src_dir = hlig_dir / "src"
            src_dir.mkdir(parents=True, exist_ok=True)
            main_rs = src_dir / "main.rs"
            if not main_rs.exists():
                main_rs.write_text("fn main() {}\n", encoding="utf-8")
        else:
            pkg = hlig_dir / "package.json"
            src_dir = hlig_dir / "src"
            index_js = src_dir / "index.js"
            if not pkg.exists():
                pkg.write_text(
                    """{
  "name": "hlig-generated",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "node src/index.js",
    "build": "node --no-warnings src/index.js",
    "start": "node src/index.js"
  },
  "devDependencies": {
    "prettier": "^3.4.2"
  }
}
""",
                    encoding="utf-8",
                )
                prc = hlig_dir / ".prettierrc.json"
                if not prc.exists():
                    prc.write_text(
                        '{\n  "semi": true,\n  "singleQuote": true\n}\n',
                        encoding="utf-8",
                    )
                src_dir.mkdir(parents=True, exist_ok=True)
                if not index_js.exists():
                    index_js.write_text(
                        "// Placeholder entry; replace with generated or real implementation.\nconsole.log('HLIG placeholder');\n",
                        encoding="utf-8",
                    )
            else:
                # Ensure entry point exists if build script runs node src/index.js (avoid false success)
                build_script = ""
                try:
                    pkg_data = json.loads(pkg.read_text(encoding="utf-8"))
                    build_script = (pkg_data.get("scripts") or {}).get("build") or ""
                except Exception:
                    pass
                if "src/index.js" in build_script and not index_js.exists():
                    src_dir.mkdir(parents=True, exist_ok=True)
                    index_js.write_text(
                        "// Placeholder entry; replace with generated or real implementation.\nconsole.log('HLIG placeholder');\n",
                        encoding="utf-8",
                    )

    def _write_readme(self, hlig_dir: Path, hlig_node: dict, framework: str, generated_nodes: list[str]) -> None:
        """Write README with build instructions."""
        task = hlig_node.get("task", "Unknown task")
        lines = [
            f"# {hlig_node.get('id', 'HLIG')} — {task}",
            "",
            "This directory contains generated design documents and code for this HLIG node.",
            "",
            "## Generated Artifacts",
            "",
        ]
        for n in generated_nodes:
            lines.append(f"- {n}")
        lines.extend([
            "",
            "## Build Instructions",
            "",
        ])

        if framework == "rust-tauri":
            lines.extend([
                "### Prerequisites",
                "- Rust toolchain: https://rustup.rs/",
                "- For Tauri desktop: Node.js (for frontend), see https://tauri.app/",
                "",
                "### Build",
                "```bash",
                "cargo build",
                "```",
                "",
                "### Run",
                "```bash",
                "cargo run",
                "```",
                "",
                "### Test",
                "```bash",
                "cargo test",
                "```",
            ])
        else:
            lines.extend([
                "### Prerequisites",
                "- Node.js 18+ and npm",
                "",
                "### Install",
                "```bash",
                "npm install",
                "```",
                "",
                "### Development",
                "```bash",
                "npm run dev",
                "```",
                "",
                "### Build",
                "```bash",
                "npm run build",
                "```",
                "",
                "### Start",
                "```bash",
                "npm start",
                "```",
            ])

        (hlig_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")

    def _write_causal_path_metadata(self, hlig_dir: Path, causal_path: list[dict]) -> None:
        """CVP: Write causal path traceability metadata for audit/explainability."""
        path_file = hlig_dir / CAUSAL_PATH_FILE
        path_file.write_text(json.dumps({"causal_path": causal_path}, indent=2), encoding="utf-8")

    def _execute_scaffold_node(
        self,
        *,
        node: dict,
        hlig_dir: Path,
        hlig_node: dict,
        framework: str,
        session_id: str,
        hlig_graph: HLIGGraph | None,
        hlig_id: str,
        resolved: dict[str, str],
        nodes_by_id: dict[str, dict],
    ) -> None:
        """Deterministically create empty files from IG expansion (no LLM)."""
        nid = node.get("id", "")
        deps = node.get("dependencies") or []
        dep_ctx = _build_dep_ctx(deps, resolved, nodes_by_id)
        iface_defs = _get_interfaces_for_hlig(hlig_graph, hlig_id) if hlig_graph else None
        contracts = iface_defs if iface_defs else []
        tech_stack = {"framework": framework}
        tasks = expand_dtg_node(node, contracts, tech_stack, project_root=hlig_dir)
        created: list[str] = []
        for t in tasks:
            fp = str(t.get("file_path") or "").strip()
            if not fp:
                continue
            full = hlig_dir / fp
            full.parent.mkdir(parents=True, exist_ok=True)
            if not full.exists():
                full.write_text("", encoding="utf-8")
            created.append(fp.replace("\\", "/"))
        brief_ctx = (json.dumps({"dependencies": dep_ctx}, default=str) if dep_ctx else "")[:2000]
        resolved[nid] = f"scaffold:{','.join(sorted(set(created)))}\n{brief_ctx}"
        log_pipeline_event(
            session_id,
            "scaffold_applied",
            {"node": nid, "files": created},
        )
        _append_node_execution_log(
            hlig_dir,
            {
                "node_id": nid,
                "type": "scaffold",
                "inputs": list(node.get("inputs_required") or []),
                "outputs": list(node.get("outputs_produced") or []),
                "attempts": 1,
                "status": "success" if created else "failure",
            },
        )

    def _execute_code_node_with_per_node_build(
        self,
        *,
        node: dict,
        hlig_dir: Path,
        hlig_node: dict,
        framework: str,
        session_id: str,
        hlig_graph: HLIGGraph | None,
        hlig_id: str,
        resolved: dict[str, str],
        nodes_by_id: dict[str, dict],
        causal_path: list[dict],
        causal_parent_context: dict[str, str] | None,
        design_docs_available: bool,
        enable_local_build: bool,
        generated_design: list[str],
        generated_code_paths: list[str],
        graph_state: Any = None,
    ) -> None:
        """
        Generate one code-type DTG node, run local build; on failure retry LLM for this node only.
        After max retries, raises LocalBuildFailedError by default; set ABORT_ON_LOCAL_BUILD_FAILURE=0 to continue.
        """
        nid = node.get("id", "")
        deps = node.get("dependencies") or []
        iface_defs = _get_interfaces_for_hlig(hlig_graph, hlig_id) if hlig_graph else None
        contracts = iface_defs if iface_defs else []
        tech_stack = {"framework": framework}
        et_exec = effective_dtg_type(node)
        ig_early = expand_dtg_node(node, contracts, tech_stack, project_root=hlig_dir)
        if et_exec == "build" and not ig_early:
            ok_b, out_b, err_b = (True, "", "")
            if enable_local_build:
                ok_b, out_b, err_b = _run_local_build(hlig_dir, framework)
                _write_build_log(hlig_dir, ok_b, out_b, err_b)
            _append_node_execution_log(
                hlig_dir,
                {
                    "node_id": nid,
                    "type": et_exec,
                    "inputs": list(node.get("inputs_required") or []),
                    "outputs": list(node.get("outputs_produced") or []),
                    "attempts": 1,
                    "status": "success" if ok_b else "failure",
                },
            )
            if not ok_b and _abort_on_local_build_failure():
                raise LocalBuildFailedError(
                    f"Build checkpoint failed for DTG node {nid}. See {hlig_dir / BUILD_LOG_FILE}",
                    hlig_id=hlig_id,
                    dtg_node_id=nid,
                    stderr=err_b or out_b,
                )
            return
        if et_exec == "test" and not ig_early:
            ok_t, out_t, err_t = _run_project_tests(hlig_dir, framework)
            _write_build_log(hlig_dir, ok_t, out_t, f"--- npm/cargo test ---\n{err_t}")
            _append_node_execution_log(
                hlig_dir,
                {
                    "node_id": nid,
                    "type": et_exec,
                    "inputs": list(node.get("inputs_required") or []),
                    "outputs": list(node.get("outputs_produced") or []),
                    "attempts": 1,
                    "status": "success" if ok_t else "failure",
                },
            )
            if not ok_t and _abort_on_local_build_failure():
                raise LocalBuildFailedError(
                    f"Test checkpoint failed for DTG node {nid}. See {hlig_dir / BUILD_LOG_FILE}",
                    hlig_id=hlig_id,
                    dtg_node_id=nid,
                    stderr=err_t or out_t,
                )
            return
        shared_iface_excerpt = load_shared_interfaces_excerpt(
            hlig_dir.parent, _shared_interfaces_max_chars()
        )
        module_snapshots: list[dict[str, str]] = []
        compile_errors: str | None = None
        previous_attempt_files: dict[str, str] | None = None
        last_err = ""
        max_extra = _per_node_build_max_retries()
        files: dict[str, str] = {}
        ig_record: dict[str, Any] | None = None

        for attempt in range(max_extra + 1):
            dep_ctx = _build_dep_ctx(deps, resolved, nodes_by_id)
            in_req = [str(x) for x in (node.get("inputs_required") or []) if x is not None]
            mc_errs = validate_node_dependency_context(
                node_id=nid,
                inputs_required=in_req,
                dependency_node_ids=list(deps),
                resolved_keys=list(dep_ctx.keys()),
                contract_ids=None,
            )
            if mc_errs:
                log_pipeline_event(
                    session_id,
                    "markov_context_violation",
                    {"dtg_node_id": nid, "errors": mc_errs},
                )
                if _env_truthy("STRICT_MARKOV_CONTEXT", "0"):
                    raise RuntimeError("; ".join(mc_errs))
            impl_brief = _build_implementation_brief(dep_ctx, iface_defs, node, framework)
            ig_tasks = expand_dtg_node(node, contracts, tech_stack, project_root=hlig_dir)
            log_pipeline_event(
                session_id,
                "dtg_ig_expansion",
                {
                    "dtg_node_id": nid,
                    "ig_task_paths": [t.get("file_path") for t in ig_tasks],
                    "ig_task_count": len(ig_tasks),
                },
            )
            if graph_state is not None and hasattr(graph_state, "record_snapshot"):
                graph_state.record_snapshot(
                    "expanded_ig",
                    {hlig_id: {"dtg_node_id": nid, "ig_paths": [t.get("file_path") for t in ig_tasks]}},
                )

            node_files: dict[str, str] = {}
            expected_ig_files = sum(
                1 for t in ig_tasks if isinstance(t, dict) and str(t.get("file_path") or "").strip()
            )
            ig_paths_ordered = [
                str(t.get("file_path")).strip()
                for t in ig_tasks
                if isinstance(t, dict) and str(t.get("file_path") or "").strip()
            ]

            if not ig_tasks:
                max_steps = _ig_code_step_max_iterations()
                mech_err: str | None = None
                node_files = {}
                for step in range(max_steps):
                    node_files = self._generate_code(
                        node,
                        framework,
                        dep_ctx,
                        session_id,
                        causal_path=causal_path,
                        causal_parent_context=causal_parent_context,
                        interface_definitions=iface_defs,
                        compile_errors=compile_errors,
                        design_docs_available=design_docs_available,
                        implementation_brief=impl_brief,
                        previous_attempt_files=previous_attempt_files,
                        mechanical_validation_errors=mech_err,
                        shared_interfaces_excerpt=shared_iface_excerpt or None,
                    )
                    if not node_files:
                        mech_err = None
                        continue
                    bad_parts: list[str] = []
                    for rel_path, content in list(node_files.items()):
                        ok, msg, fixed = validate_generated_content(
                            rel_path, content, framework, project_root=hlig_dir
                        )
                        if fixed is not None:
                            node_files[rel_path] = fixed
                        if not ok:
                            bad_parts.append(f"{rel_path}: {msg}")
                    if not bad_parts:
                        break
                    mech_err = "Fix mechanical validation errors:\n" + "\n".join(bad_parts)
                    log_pipeline_event(
                        session_id,
                        "mechanical_validation_failed",
                        {"node": nid, "attempt": step + 1, "summary": mech_err[:1200]},
                    )
                for rel_path, content in node_files.items():
                    full_path = hlig_dir / rel_path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content, encoding="utf-8")
                ig_record = {
                    "node_id": nid,
                    "section": node.get("section") or "",
                    "dtg_node_id": nid,
                    "expanded_ig": sorted(node_files.keys()),
                    "status": "success" if node_files else "failure",
                }
            else:
                per_file_review_failed = False
                per_file_mechanical_failed = False
                for ig_task in ig_tasks:
                    fp = str(ig_task.get("file_path") or "").strip()
                    if not fp:
                        continue
                    sub_node = {**node, "files_owned": [fp]}
                    prior = {k: v for k, v in sorted(node_files.items()) if k != fp}
                    content = ""
                    last_review = "pass"
                    mech_err: str | None = None
                    mech_ok_final = False
                    for step in range(_ig_code_step_max_iterations()):
                        prev_files: dict[str, str] | None = None
                        if previous_attempt_files:
                            prev_files = previous_attempt_files
                        elif step > 0 and content:
                            prev_files = {fp: content}
                        base_kw: dict[str, Any] = {
                            "causal_path": causal_path,
                            "causal_parent_context": causal_parent_context,
                            "interface_definitions": iface_defs,
                            "compile_errors": compile_errors,
                            "design_docs_available": design_docs_available,
                            "implementation_brief": impl_brief,
                            "previous_attempt_files": prev_files,
                            "same_node_prior_files": prior or None,
                            "mechanical_validation_errors": mech_err,
                            "module_interface_snapshots": list(module_snapshots) if module_snapshots else None,
                            "shared_interfaces_excerpt": shared_iface_excerpt or None,
                        }
                        if not content:
                            if step == 0 and _ig_two_phase_enabled() and _ig_two_phase_eligible(fp):
                                sk = self._generate_code(
                                    sub_node,
                                    framework,
                                    dep_ctx,
                                    session_id,
                                    codegen_phase="skeleton",
                                    **base_kw,
                                )
                                sb = sk.get(fp, "") or ""
                                if sb:
                                    chunk = self._generate_code(
                                        sub_node,
                                        framework,
                                        dep_ctx,
                                        session_id,
                                        codegen_phase="implement",
                                        current_file_skeleton=sb,
                                        **base_kw,
                                    )
                                    content = chunk.get(fp, "") or ""
                            if not content:
                                chunk = self._generate_code(
                                    sub_node,
                                    framework,
                                    dep_ctx,
                                    session_id,
                                    **base_kw,
                                )
                                content = chunk.get(fp, "") or ""
                        if not content:
                            mech_err = None
                            continue
                        ok_m, msg_m, fixed_m = validate_generated_content(
                            fp, content, framework, project_root=hlig_dir
                        )
                        if fixed_m is not None:
                            content = fixed_m
                        if not ok_m:
                            mech_err = f"{fp}: {msg_m}"
                            log_pipeline_event(
                                session_id,
                                "mechanical_validation_failed",
                                {
                                    "path": fp,
                                    "node": nid,
                                    "step": step + 1,
                                    "error": msg_m[:500],
                                },
                            )
                            content = ""
                            continue
                        st, _raw = self._review_ig_file(
                            session_id,
                            dtg_node=node,
                            file_path=fp,
                            content=content,
                            framework=framework,
                        )
                        last_review = st
                        if st == "fail":
                            mech_err = None
                            content = ""
                            continue
                        full_path = hlig_dir / fp
                        full_path.parent.mkdir(parents=True, exist_ok=True)
                        full_path.write_text(content, encoding="utf-8")
                        if _deterministic_on_disk_validate_enabled():
                            cref = list(ig_task.get("contract_refs") or [])
                            ok_d, errs_d = deterministic_validate_file(
                                full_path,
                                cref,
                                project_root=hlig_dir,
                                framework=framework,
                            )
                            if not ok_d:
                                mech_err = f"{fp} (deterministic):\n" + "\n".join(errs_d[:20])
                                log_pipeline_event(
                                    session_id,
                                    "deterministic_file_validate_failed",
                                    {
                                        "path": fp,
                                        "node": nid,
                                        "step": step + 1,
                                        "errors": errs_d[:10],
                                    },
                                )
                                content = ""
                                continue
                        ok_tool, msg_tool = extra_toolchain_validate(fp, content, framework, hlig_dir)
                        if not ok_tool:
                            mech_err = f"{fp} (toolchain): {msg_tool}"
                            log_pipeline_event(
                                session_id,
                                "toolchain_validation_failed",
                                {
                                    "path": fp,
                                    "node": nid,
                                    "step": step + 1,
                                    "error": msg_tool[:600],
                                },
                            )
                            content = ""
                            continue
                        if framework == "rust-tauri":
                            cok, cerr = run_incremental_cargo_check(hlig_dir)
                            if not cok:
                                mech_err = f"cargo check:\n{cerr}"
                                log_pipeline_event(
                                    session_id,
                                    "incremental_cargo_check_failed",
                                    {
                                        "path": fp,
                                        "node": nid,
                                        "preview": cerr[:600],
                                    },
                                )
                                content = ""
                                continue
                        mech_err = None
                        mech_ok_final = True
                        break
                    if mech_ok_final:
                        node_files[fp] = content
                        module_snapshots.append(
                            {
                                "path": fp,
                                "public_surface": extract_public_surface(fp, content),
                            }
                        )
                    elif content:
                        full_path = hlig_dir / fp
                        full_path.parent.mkdir(parents=True, exist_ok=True)
                        full_path.write_text(content, encoding="utf-8")
                        node_files[fp] = content
                        if last_review != "fail":
                            per_file_mechanical_failed = True
                    if last_review == "fail":
                        per_file_review_failed = True
                ig_log_status = (
                    "failure"
                    if per_file_review_failed
                    or per_file_mechanical_failed
                    or len(node_files) < expected_ig_files
                    else "success"
                )
                ig_record = {
                    "node_id": nid,
                    "section": node.get("section") or "",
                    "dtg_node_id": nid,
                    "expanded_ig": list(ig_paths_ordered),
                    "status": ig_log_status,
                }

            files = node_files
            if framework == "rust-tauri" and files:
                ok_rw, rw_msg = validate_rust_workspace_coherence(hlig_dir)
                if not ok_rw:
                    last_err = rw_msg[:4000]
                    compile_errors = (
                        "Rust workspace coherence check failed. "
                        "Emit every `src/*.rs` module referenced by `use crate::…`, "
                        "and add every external crate you use to Cargo.toml.\n\n"
                        + rw_msg
                    )
                    previous_attempt_files = _snapshot_project_files_for_retry(
                        hlig_dir, framework, files
                    )
                    log_pipeline_event(
                        session_id,
                        "rust_workspace_coherence_retry",
                        {
                            "hlig": hlig_id,
                            "dtg_node": nid,
                            "attempt": attempt + 1,
                            "summary": rw_msg[:1200],
                        },
                    )
                    continue
            if files:
                resolved[nid] = _make_code_output_spec(nid, list(files.items()))
                log_pipeline_event(session_id, "code_generated", {"node": nid, "files": list(files.keys())})
            self._scaffold_project(hlig_dir, framework, hlig_node)
            readme_paths = generated_design + generated_code_paths + list(files.keys())
            self._write_readme(hlig_dir, hlig_node, framework, readme_paths)
            if not enable_local_build:
                if ig_record:
                    _append_ig_execution_log(hlig_dir, ig_record)
                _append_node_execution_log(
                    hlig_dir,
                    {
                        "node_id": nid,
                        "type": effective_dtg_type(node),
                        "inputs": list(node.get("inputs_required") or []),
                        "outputs": list(node.get("outputs_produced") or []),
                        "attempts": attempt + 1,
                        "status": "success",
                    },
                )
                for p in files:
                    if p not in generated_code_paths:
                        generated_code_paths.append(p)
                return
            matrix = _load_dependency_matrix()
            if framework == "rust-tauri":
                _apply_dependency_matrix_to_cargo(hlig_dir, matrix)
                _normalize_rust_embed_migrations(hlig_dir)
            elif framework == "node-react":
                _apply_dependency_matrix_to_node_project(hlig_dir, matrix)
            success, out, err = _run_local_build(hlig_dir, framework)
            _write_build_log(hlig_dir, success, out, err)
            if success:
                if effective_dtg_type(node) == "test":
                    tok, ot, etk = _run_project_tests(hlig_dir, framework)
                    _write_build_log(hlig_dir, tok, ot, f"--- tests ---\n{etk}")
                    if not tok:
                        last_err = etk or ot or ""
                        compile_errors = f"Tests failed.\nstdout:\n{ot}\nstderr:\n{etk}"
                        if files:
                            previous_attempt_files = _snapshot_project_files_for_retry(hlig_dir, framework, files)
                        else:
                            previous_attempt_files = None
                        log_pipeline_event(
                            session_id,
                            "test_retry",
                            {
                                "hlig": hlig_id,
                                "dtg_node": nid,
                                "attempt": attempt + 1,
                                "stderr_preview": last_err[:500],
                            },
                        )
                        continue
                if ig_record:
                    _append_ig_execution_log(hlig_dir, ig_record)
                log_pipeline_event(
                    session_id,
                    "local_build_ok",
                    {"hlig": hlig_id, "dtg_node": nid, "attempt": attempt + 1},
                )
                _append_node_execution_log(
                    hlig_dir,
                    {
                        "node_id": nid,
                        "type": effective_dtg_type(node),
                        "inputs": list(node.get("inputs_required") or []),
                        "outputs": list(node.get("outputs_produced") or []),
                        "attempts": attempt + 1,
                        "status": "success",
                    },
                )
                for p in files:
                    if p not in generated_code_paths:
                        generated_code_paths.append(p)
                return
            last_err = err or out or ""
            raw_errors = f"Previous build failed.\nstdout:\n{out}\nstderr:\n{err}"
            hints = _parse_build_failure_hints(err or out, framework)
            compile_errors = raw_errors + ("\n\nHints to fix:\n" + hints if hints else "")
            if files:
                previous_attempt_files = _snapshot_project_files_for_retry(hlig_dir, framework, files)
            else:
                previous_attempt_files = None
            log_pipeline_event(
                session_id,
                "local_build_retry",
                {
                    "hlig": hlig_id,
                    "dtg_node": nid,
                    "attempt": attempt + 1,
                    "stderr_preview": last_err[:500],
                    "previous_files_keys": list(previous_attempt_files.keys()) if previous_attempt_files else [],
                },
            )
        if ig_record is not None:
            _append_ig_execution_log(
                hlig_dir,
                {**ig_record, "status": "failure"},
            )
        if _abort_on_local_build_failure():
            _append_node_execution_log(
                hlig_dir,
                {
                    "node_id": nid,
                    "type": effective_dtg_type(node),
                    "inputs": list(node.get("inputs_required") or []),
                    "outputs": list(node.get("outputs_produced") or []),
                    "attempts": max_extra + 1,
                    "status": "failure",
                },
            )
            raise LocalBuildFailedError(
                f"Local build failed for HLIG {hlig_id} DTG node {nid} after {max_extra + 1} attempt(s). "
                f"See {hlig_dir / BUILD_LOG_FILE}",
                hlig_id=hlig_id,
                dtg_node_id=nid,
                stderr=last_err,
            )
        log_pipeline_event(
            session_id,
            "local_build_failed_continue",
            {
                "hlig": hlig_id,
                "dtg_node": nid,
                "reason": "local build failed after retries; continuing (ABORT_ON_LOCAL_BUILD_FAILURE=0)",
            },
        )
        for p in files:
            if p not in generated_code_paths:
                generated_code_paths.append(p)

    def generate_for_hlig(
        self,
        hlig_id: str,
        hlig_node: dict,
        dtg: DTGGraph,
        outputs_dir: Path,
        session_id: str,
        hlig_graph: HLIGGraph | None = None,
        causal_parent_context: dict[str, str] | None = None,
        graph_state: Any = None,
    ) -> Path | None:
        """
        Generate design docs and code for one HLIG node's DTG.
        CVP: causal_parent_context (Markov blanket) restricts context to causal parents only.
        Returns the HLIG subdirectory path or None on failure.
        """
        if (hlig_node.get("node_type") or "").lower() == "contract":
            return None
        hlig_dir = outputs_dir / hlig_id
        hlig_dir.mkdir(parents=True, exist_ok=True)
        designs_dir = hlig_dir / "designs"
        designs_dir.mkdir(exist_ok=True)
        src_dir = hlig_dir / "src"
        src_dir.mkdir(exist_ok=True)

        # CVP: Compute causal path for traceability
        causal_path: list[dict] = []
        if hlig_graph:
            path_tuples = hlig_graph.get_causal_path(hlig_id)
            causal_path = [
                {"id": nid, "task": data.get("task", ""), "outputs": data.get("outputs", [])}
                for nid, data in path_tuples
            ]
            self._write_causal_path_metadata(hlig_dir, causal_path)

        framework = _infer_framework(hlig_node)
        log_pipeline_event(
            session_id,
            "framework_inferred",
            {
                "hlig_id": hlig_id,
                "framework": framework,
                "language_normalized": _normalize_hlig_language_field(hlig_node) or None,
            },
        )
        order = _topological_order(dtg)
        nodes_by_id = {n["id"]: n for n in order if n.get("id")}
        resolved: dict[str, str] = {}
        generated_design: list[str] = []

        # Scaffold project structure so it's buildable
        self._scaffold_project(hlig_dir, framework, hlig_node)

        # Design nodes run once
        for node in order:
            nid = node.get("id", "")
            if effective_dtg_type(node) == "review":
                continue
            if is_scaffold_node(node):
                self._execute_scaffold_node(
                    node=node,
                    hlig_dir=hlig_dir,
                    hlig_node=hlig_node,
                    framework=framework,
                    session_id=session_id,
                    hlig_graph=hlig_graph,
                    hlig_id=hlig_id,
                    resolved=resolved,
                    nodes_by_id=nodes_by_id,
                )
                continue
            if not is_design_like_node(node):
                continue
            deps = node.get("dependencies") or []
            dep_ctx = _build_dep_ctx(deps, resolved, nodes_by_id)
            iface_defs = _get_interfaces_for_hlig(hlig_graph, hlig_id) if hlig_graph else None
            doc = self._generate_design_doc(
                node, dep_ctx, session_id, causal_path=causal_path, causal_parent_context=causal_parent_context,
                interface_definitions=iface_defs,
            )
            if doc:
                safe_name = _safe_filename(node.get("title", nid))
                fp = designs_dir / f"{nid}_{safe_name}.json"
                fp.write_text(doc, encoding="utf-8")
                resolved[nid] = _truncate_design(doc)
                generated_design.append(f"designs/{fp.name}")
            log_pipeline_event(session_id, "design_generated", {"node": nid})
            _append_node_execution_log(
                hlig_dir,
                {
                    "node_id": nid,
                    "type": effective_dtg_type(node),
                    "inputs": list(node.get("inputs_required") or []),
                    "outputs": list(node.get("outputs_produced") or []),
                    "attempts": 1,
                    "status": "success" if doc else "failure",
                },
            )

        enable_local_build = os.environ.get("ENABLE_LOCAL_BUILD", "1").strip().lower() not in ("0", "false", "no")
        generated_code_paths: list[str] = []
        for node in order:
            if effective_dtg_type(node) == "review":
                continue
            if not is_code_execution_node(node):
                continue
            self._execute_code_node_with_per_node_build(
                node=node,
                hlig_dir=hlig_dir,
                hlig_node=hlig_node,
                framework=_framework_for_atomic_node(node, framework),
                session_id=session_id,
                hlig_graph=hlig_graph,
                hlig_id=hlig_id,
                resolved=resolved,
                nodes_by_id=nodes_by_id,
                causal_path=causal_path,
                causal_parent_context=causal_parent_context,
                design_docs_available=True,
                enable_local_build=enable_local_build,
                generated_design=generated_design,
                generated_code_paths=generated_code_paths,
                graph_state=graph_state,
            )
        _maybe_validate_project_layout(
            hlig_dir, dtg, framework, session_id=session_id, hlig_id=hlig_id
        )
        return hlig_dir

    def _load_existing_design_docs(self, designs_dir: Path) -> dict[str, str]:
        """Load design spec content from designs_dir for dependency context. Returns {node_id: content}.
        Loads .json (canonical design_spec) first; falls back to .md for backward compatibility."""
        resolved: dict[str, str] = {}
        if not designs_dir.exists():
            return resolved
        # Prefer .json (canonical design_spec for LLM consumption)
        for fp in designs_dir.glob("*.json"):
            stem = fp.stem
            if "_" in stem:
                nid = stem.split("_", 1)[0]
            else:
                nid = stem
            try:
                resolved[nid] = _truncate_design(fp.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Fallback: legacy .md (for backward compatibility)
        for fp in designs_dir.glob("*.md"):
            stem = fp.stem
            if "_" in stem:
                nid = stem.split("_", 1)[0]
            else:
                nid = stem
            if nid not in resolved:
                try:
                    resolved[nid] = _truncate_design(fp.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return resolved

    def generate_design_docs_only(
        self,
        hlig_graph: HLIGGraph,
        session_id: str,
        date_dir: Path,
    ) -> Path | None:
        """
        Generate only design documents for design-type DTG nodes.
        Creates outputs_{session_id}/ under date_dir. Returns the outputs directory path.
        """
        outputs_dir = date_dir / f"outputs_{session_id}"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        _write_interface_definitions(hlig_graph, outputs_dir)
        topo_order = hlig_graph.topological_order()
        node_data_by_id = {nid: dict(data) for nid, data in hlig_graph.nodes()}
        hlig_outputs: dict[str, str] = {}

        for nid in topo_order:
            data = node_data_by_id.get(nid, {})
            if _is_contract_hlig_node(data):
                continue
            dtg = data.get("dtg")
            if not isinstance(dtg, DTGGraph):
                continue
            hlig_node = {"id": nid, **{k: v for k, v in data.items() if k != "dtg" and not callable(v)}}
            causal_parent_ids = hlig_graph.get_causal_parents(nid)
            causal_parent_context = {pid: hlig_outputs.get(pid, "") for pid in causal_parent_ids if pid in hlig_outputs}

            try:
                hlig_dir = outputs_dir / nid
                hlig_dir.mkdir(parents=True, exist_ok=True)
                designs_dir = hlig_dir / "designs"
                designs_dir.mkdir(exist_ok=True)
                causal_path: list[dict] = []
                if hasattr(hlig_graph, "get_causal_path"):
                    path_tuples = hlig_graph.get_causal_path(nid)
                    causal_path = [
                        {"id": nid2, "task": d.get("task", ""), "outputs": d.get("outputs", [])}
                        for nid2, d in path_tuples
                    ]
                    self._write_causal_path_metadata(hlig_dir, causal_path)
                order = _topological_order(dtg)
                resolved: dict[str, str] = {}
                for node in order:
                    if not is_design_like_node(node):
                        continue
                    nid2 = node.get("id", "")
                    deps = node.get("dependencies") or []
                    dep_ctx = {d: resolved.get(d, "") for d in deps if resolved.get(d)}
                    doc = self._generate_design_doc(
                        node, dep_ctx, session_id,
                        causal_path=causal_path,
                        causal_parent_context=causal_parent_context if causal_parent_context else None,
                        interface_definitions=_get_interfaces_for_hlig(hlig_graph, nid),
                    )
                    if doc:
                        safe_name = _safe_filename(node.get("title", nid2))
                        fp = designs_dir / f"{nid2}_{safe_name}.json"
                        fp.write_text(doc, encoding="utf-8")
                        resolved[nid2] = _truncate_design(doc)
                    log_pipeline_event(session_id, "design_generated", {"node": nid2})
                task = hlig_node.get("task", "")
                hlig_outputs[nid] = f"[{nid}] {task}\n(design docs generated)"
            except Exception as e:
                log_pipeline_event(session_id, "artifact_generation_error", {"hlig": nid, "error": str(e)})
                if isinstance(e, LocalBuildFailedError):
                    raise
                if isinstance(e, CostLimitExceeded):
                    raise

        return outputs_dir

    def generate_code_only(
        self,
        hlig_graph: HLIGGraph,
        session_id: str,
        outputs_dir: Path,
        has_design_docs: bool = True,
        graph_state: Any = None,
    ) -> Path | None:
        """
        Generate only code for code-type DTG nodes.
        When has_design_docs is False (e.g. hlig_no_design_docs pipeline), does not load from designs/;
        dependency context uses dtg_node_ref from DTG metadata only. When True, loads design specs from designs/.
        """
        topo_order = hlig_graph.topological_order()
        node_data_by_id = {nid: dict(data) for nid, data in hlig_graph.nodes()}
        hlig_outputs: dict[str, str] = {}

        for nid in topo_order:
            data = node_data_by_id.get(nid, {})
            if _is_contract_hlig_node(data):
                continue
            dtg = data.get("dtg")
            if not isinstance(dtg, DTGGraph):
                continue
            hlig_node = {"id": nid, **{k: v for k, v in data.items() if k != "dtg" and not callable(v)}}
            hlig_dir = outputs_dir / nid
            causal_parent_ids = hlig_graph.get_causal_parents(nid)
            causal_parent_context = {pid: hlig_outputs.get(pid, "") for pid in causal_parent_ids if pid in hlig_outputs}

            try:
                hlig_dir.mkdir(parents=True, exist_ok=True)
                designs_dir = hlig_dir / "designs"
                if has_design_docs and designs_dir.exists():
                    resolved = self._load_existing_design_docs(designs_dir)
                else:
                    resolved = {}
                order = _topological_order(dtg)
                nodes_by_id = {n["id"]: n for n in order if n.get("id")}
                enable_local_build = os.environ.get("ENABLE_LOCAL_BUILD", "1").strip().lower() not in ("0", "false", "no")
                existing = [
                    f.name
                    for f in (
                        (list(designs_dir.glob("*.json")) + list(designs_dir.glob("*.md")))
                        if designs_dir.exists()
                        else []
                    )
                ]
                generated_design_refs = [f"designs/{x}" for x in existing]
                causal_path: list[dict] = []
                if hasattr(hlig_graph, "get_causal_path"):
                    path_tuples = hlig_graph.get_causal_path(nid)
                    causal_path = [
                        {"id": nid3, "task": d.get("task", ""), "outputs": d.get("outputs", [])}
                        for nid3, d in path_tuples
                    ]
                framework = _infer_framework(hlig_node)
                log_pipeline_event(
                    session_id,
                    "framework_inferred",
                    {
                        "hlig_id": nid,
                        "framework": framework,
                        "language_normalized": _normalize_hlig_language_field(hlig_node) or None,
                    },
                )
                self._scaffold_project(hlig_dir, framework, hlig_node)
                for node in order:
                    if effective_dtg_type(node) == "review":
                        continue
                    if is_scaffold_node(node):
                        self._execute_scaffold_node(
                            node=node,
                            hlig_dir=hlig_dir,
                            hlig_node=hlig_node,
                            framework=framework,
                            session_id=session_id,
                            hlig_graph=hlig_graph,
                            hlig_id=nid,
                            resolved=resolved,
                            nodes_by_id=nodes_by_id,
                        )
                generated_code_paths: list[str] = []
                for node in order:
                    if effective_dtg_type(node) == "review":
                        continue
                    if not is_code_execution_node(node):
                        continue
                    self._execute_code_node_with_per_node_build(
                        node=node,
                        hlig_dir=hlig_dir,
                        hlig_node=hlig_node,
                        framework=_framework_for_atomic_node(node, framework),
                        session_id=session_id,
                        hlig_graph=hlig_graph,
                        hlig_id=nid,
                        resolved=resolved,
                        nodes_by_id=nodes_by_id,
                        causal_path=causal_path,
                        causal_parent_context=causal_parent_context if causal_parent_context else None,
                        design_docs_available=has_design_docs,
                        enable_local_build=enable_local_build,
                        generated_design=generated_design_refs,
                        generated_code_paths=generated_code_paths,
                        graph_state=graph_state,
                    )
                _maybe_validate_project_layout(
                    hlig_dir, dtg, framework, session_id=session_id, hlig_id=nid
                )
                task = hlig_node.get("task", "")
                readme = hlig_dir / "README.md"
                summary = readme.read_text(encoding="utf-8")[:3000] if readme.exists() else ""
                hlig_outputs[nid] = f"[{nid}] {task}\n{summary}"
            except Exception as e:
                log_pipeline_event(session_id, "artifact_generation_error", {"hlig": nid, "error": str(e)})
                if isinstance(e, LocalBuildFailedError):
                    raise
                if isinstance(e, CostLimitExceeded):
                    raise

        return outputs_dir

    def generate_all(
        self,
        hlig_graph: HLIGGraph,
        session_id: str,
        date_dir: Path,
    ) -> Path | None:
        """
        Generate artifacts for all HLIG nodes with DTGs.
        CVP: Processes nodes in topological order; passes causal_parent_context (Markov blanket)
        from parent HLIG outputs to children for scoped reasoning.
        Creates outputs_{session_id}/ under date_dir.
        Returns the outputs directory path or None.
        """
        outputs_dir = date_dir / f"outputs_{session_id}"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        _write_interface_definitions(hlig_graph, outputs_dir)

        # CVP: Process in topological order so parent outputs are available for Markov blanket
        topo_order = hlig_graph.topological_order()
        node_data_by_id = {nid: dict(data) for nid, data in hlig_graph.nodes()}
        hlig_outputs: dict[str, str] = {}

        for nid in topo_order:
            data = node_data_by_id.get(nid, {})
            if _is_contract_hlig_node(data):
                continue
            dtg = data.get("dtg")
            if not isinstance(dtg, DTGGraph):
                continue
            hlig_node = {"id": nid, **{k: v for k, v in data.items() if k != "dtg" and not callable(v)}}

            # CVP: Build causal_parent_context from Markov blanket (only causal parents)
            causal_parent_ids = hlig_graph.get_causal_parents(nid)
            causal_parent_context = {pid: hlig_outputs.get(pid, "") for pid in causal_parent_ids if pid in hlig_outputs}

            try:
                result_path = self.generate_for_hlig(
                    nid, hlig_node, dtg, outputs_dir, session_id,
                    hlig_graph=hlig_graph,
                    causal_parent_context=causal_parent_context if causal_parent_context else None,
                    graph_state=None,
                )
                if result_path:
                    # Store summary for downstream Markov blanket scoping
                    task = hlig_node.get("task", "")
                    readme = result_path / "README.md"
                    summary = ""
                    if readme.exists():
                        summary = readme.read_text(encoding="utf-8")[:3000]
                    hlig_outputs[nid] = f"[{nid}] {task}\n{summary}"
            except Exception as e:
                log_pipeline_event(session_id, "artifact_generation_error", {"hlig": nid, "error": str(e)})
                if isinstance(e, LocalBuildFailedError):
                    raise
                if isinstance(e, CostLimitExceeded):
                    raise

        return outputs_dir
