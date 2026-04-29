"""
Deterministic DTG → Implementation Graph (IG) expansion.

IG tasks are not stored in the DTG; they are computed at execution time from
`expansion_strategy`, `files_owned`, contracts, and tech_stack.

Execution mapping (artifact generator; stop on failure when abort env is on):
- **scaffold**: expand to paths, create empty files and dirs deterministically (no LLM).
- **code** / legacy integration·verification: expand to one IG step per file; writer +
  mechanical + deterministic on-disk checks + LLM review per step; bounded retries.
- **review**: not run as a separate executor; expressed in the DTG only.
- **test**: expand when files are owned; after a successful local build, run ``cargo test``
  / ``npm test`` for test-typed nodes; if expansion is empty, run tests only.
- **build**: if expansion is empty, run full local build as a checkpoint; otherwise
  follows the code path and build after edits.
"""

from __future__ import annotations

from typing import Any

# Canonical strategies (templates keyed here). Aliases resolve to these (prompts / DTG may use either).
EXPANSION_STRATEGIES = frozenset(
    {
        "frontend_app_standard",
        "backend_service_standard",
        "crud_api_standard",
        "database_schema_standard",
        "integration_standard",
    }
)

# Public alias names (PART 3) — deterministic mapping, no LLM.
EXPANSION_STRATEGY_ALIASES: dict[str, str] = {
    "frontend_standard": "frontend_app_standard",
    "backend_standard": "backend_service_standard",
    "db_schema_standard": "database_schema_standard",
}

EXPANSION_STRATEGIES_ALL_NAMES = EXPANSION_STRATEGIES | frozenset(EXPANSION_STRATEGY_ALIASES.keys())


def canonical_expansion_strategy(name: str) -> str:
    """Map user-facing / legacy alias to canonical template key."""
    n = (name or "").strip()
    return EXPANSION_STRATEGY_ALIASES.get(n, n)

# Logical DTG roles (prompts/dtg_generator.md). Legacy graphs may omit `type` and use `task_type` only.
DTG_LOGICAL_TYPES = frozenset(
    {"design", "contract", "scaffold", "code", "review", "test", "build"}
)


def effective_dtg_type(node: dict) -> str:
    """
    Resolve logical type for validation and execution.
    Prefer explicit `type`; else map legacy `task_type` to strict types.
    """
    raw = (node.get("type") or "").strip().lower()
    if raw in DTG_LOGICAL_TYPES:
        return raw
    tt = (node.get("task_type") or "").strip().lower()
    if tt == "review":
        return "review"
    if tt == "scaffold":
        return "scaffold"
    if tt == "documentation":
        return "design"
    if tt == "contract":
        return "contract"
    if tt in ("integration", "verification"):
        return "code"
    if tt in DTG_LOGICAL_TYPES:
        return tt
    return "design"


def is_design_like_node(node: dict) -> bool:
    """Design or contract — specs/interfaces / API artifacts, not scaffold or code execution."""
    return effective_dtg_type(node) in ("design", "contract")


def is_scaffold_node(node: dict) -> bool:
    """Scaffold nodes create project layout and empty files (deterministic), no LLM codegen."""
    return effective_dtg_type(node) == "scaffold"


def is_code_execution_node(node: dict) -> bool:
    """Nodes that run codegen / IG execution (implementation, tests, build checkpoints)."""
    return effective_dtg_type(node) in ("code", "test", "build")


def needs_expansion_strategy(node: dict) -> bool:
    """True when the node must declare a valid expansion_strategy (implementation / scaffold work)."""
    return effective_dtg_type(node) in ("code", "test", "build", "scaffold")

# Template paths per strategy (concrete files; deterministic order).
_TEMPLATES: dict[str, list[str]] = {
    "frontend_app_standard": [
        "src/components/App.tsx",
        "src/pages/Home.tsx",
        "src/api/client.ts",
        "src/styles/main.css",
    ],
    "backend_service_standard": [
        "src/main.rs",
        "src/routes/mod.rs",
        "src/handlers/mod.rs",
        "src/models/mod.rs",
    ],
    "crud_api_standard": [
        "src/routes/crud.rs",
        "src/handlers/crud.rs",
        "src/models/entity.rs",
    ],
    "database_schema_standard": [
        "migrations/0001_initial/up.sql",
        "src/db/mod.rs",
    ],
    "integration_standard": [
        "src/integration/mod.rs",
    ],
}

def _normalize_tech_stack(tech_stack: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(tech_stack, str):
        return {"framework": tech_stack}
    return tech_stack if isinstance(tech_stack, dict) else {}


def _template_paths_for_strategy(strategy: str, framework: str) -> list[str]:
    _ = framework  # reserved for future stack-specific templates
    return list(_TEMPLATES.get(strategy, _TEMPLATES["integration_standard"]))


def _resolve_file_paths(dtg_node: dict, strategy: str, framework: str) -> list[str]:
    fo = dtg_node.get("files_owned")
    if isinstance(fo, list) and fo:
        out: list[str] = []
        for p in fo:
            if isinstance(p, str) and p.strip():
                out.append(p.strip().replace("\\", "/"))
        return sorted(set(out))
    return _template_paths_for_strategy(strategy, framework)


def _contract_refs(contracts: list[dict[str, Any]] | None) -> list[str]:
    if not contracts:
        return []
    refs: list[str] = []
    for c in contracts:
        if not isinstance(c, dict):
            continue
        r = c.get("interface_ref")
        if r:
            refs.append(str(r))
            continue
        u, v = c.get("from"), c.get("to")
        if u is not None and v is not None:
            refs.append(f"{u}->{v}")
    return sorted(set(refs))


def default_expansion_strategy_for_node(hlig_node: dict, dtg_node: dict) -> str:
    """
    Heuristic default when the designer omits expansion_strategy (backward compatibility).
    """
    if not needs_expansion_strategy(dtg_node):
        return ""
    lang = (hlig_node.get("language") or "").lower()
    task = (hlig_node.get("task") or "").lower()
    if "react" in lang or "vite" in lang or "frontend" in task or "ui" in task or "website" in task:
        return "frontend_app_standard"
    if "schema" in task or "migration" in task or "database" in task or "diesel" in lang:
        return "database_schema_standard"
    if "crud" in task:
        return "crud_api_standard"
    if "integrat" in task:
        return "integration_standard"
    return "backend_service_standard"


def normalize_expansion_strategy(dtg_node: dict) -> str:
    raw = canonical_expansion_strategy(dtg_node.get("expansion_strategy") or "")
    if raw in EXPANSION_STRATEGIES:
        return raw
    parent = dtg_node.get("parent_hlig")
    ph = parent if isinstance(parent, dict) else {}
    fallback = default_expansion_strategy_for_node(ph, dtg_node)
    fb = canonical_expansion_strategy(fallback)
    return fb if fb in EXPANSION_STRATEGIES else "integration_standard"


def expand_dtg_node(
    dtg_node: dict,
    contracts: list[dict[str, Any]] | None,
    tech_stack: dict[str, Any] | str,
    *,
    project_root: Any = None,
) -> list[dict[str, Any]]:
    """
    Expand one DTG node into ordered IG tasks (one primary file per task).

    Each task:
      file_path, task_type (create|update), inputs, outputs, contract_refs
    """
    et = effective_dtg_type(dtg_node)
    if et in ("design", "contract", "review"):
        return []

    ts = _normalize_tech_stack(tech_stack)
    framework = str(ts.get("framework") or "")
    strategy = normalize_expansion_strategy(dtg_node)

    tt = (dtg_node.get("task_type") or "").lower()
    fo = dtg_node.get("files_owned")
    has_owned_files = isinstance(fo, list) and any(isinstance(p, str) and p.strip() for p in fo)
    # Build checkpoints are logical; do not synthesize template files when none are owned.
    if et != "scaffold" and (tt == "build" or et == "build") and not has_owned_files:
        return []

    paths = _resolve_file_paths(dtg_node, strategy, framework)
    if not paths:
        return []

    inputs = list(dtg_node.get("inputs_required") or [])
    if not isinstance(inputs, list):
        inputs = []
    outs_base = list(dtg_node.get("outputs_produced") or [])
    if not isinstance(outs_base, list):
        outs_base = []
    cref = _contract_refs(contracts)

    tasks: list[dict[str, Any]] = []
    for path in paths:
        task_type = "create"
        if project_root is not None:
            try:
                fp = project_root / path
                if fp.is_file():
                    task_type = "update"
            except Exception:
                pass
        outputs = [f"file:{path}"]
        if outs_base:
            outputs = sorted(set(outputs + outs_base))
        tasks.append(
            {
                "file_path": path,
                "task_type": task_type,
                "inputs": list(inputs),
                "outputs": outputs,
                "contract_refs": list(cref),
            }
        )
    return tasks


def expand_node(
    node: dict,
    contracts: list[dict[str, Any]] | None,
    tech_stack: dict[str, Any] | str,
    *,
    project_root: Any = None,
) -> list[dict[str, Any]]:
    """Alias for expand_dtg_node (deterministic runtime IG; not persisted in DTG)."""
    return expand_dtg_node(node, contracts, tech_stack, project_root=project_root)
