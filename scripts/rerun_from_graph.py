#!/usr/bin/env python3
"""
Re-run artifact generation from a saved HLIG graph JSON (e.g. session_log/.../graph_<session_id>.json).

Use when a prior run failed (e.g. LocalBuildFailedError) but the graph is already on disk.

Examples:
  # Regenerate code only, reuse designs in outputs_<session_id>/ (same folder as the graph file)
  uv run python scripts/rerun_from_graph.py \\
    session_log/sessions/2026/03/21/graph_4056567746.json

  # Full regenerate: design docs + code into the same outputs directory tree
  uv run python scripts/rerun_from_graph.py path/to/graph_4056567746.json --mode artifacts

  # Custom outputs directory
  uv run python scripts/rerun_from_graph.py path/to/graph_X.json --outputs-dir /tmp/outputs_X
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _session_id_from_graph_path(graph_path: Path) -> str:
    stem = graph_path.stem
    if stem.startswith("graph_"):
        return stem[len("graph_") :]
    return stem


def _outputs_has_design_docs(outputs_dir: Path) -> bool:
    if not outputs_dir.is_dir():
        return False
    for child in outputs_dir.iterdir():
        if not child.is_dir():
            continue
        designs = child / "designs"
        if designs.is_dir():
            if list(designs.glob("*.json")) or list(designs.glob("*.md")):
                return True
    return False


def _provision(hlig_graph, outputs_path: Path, session_id: str) -> None:
    try:
        from core.debug_logger import log_pipeline_event
        from core.provision_dependencies import provision_all

        results = provision_all(
            hlig_graph,
            outputs_path,
            session_id=session_id,
            use_docker_compose=False,
        )
        if results:
            log_pipeline_event(session_id, "dependencies_provisioned", {"provisioned": results})
    except Exception as e:
        try:
            from core.debug_logger import log_pipeline_event

            log_pipeline_event(session_id, "provision_error", {"error": str(e)})
        except ImportError:
            print(f"Warning: dependency provisioning failed: {e}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-run design/code generation from a saved graph_*.json file.",
    )
    parser.add_argument(
        "graph",
        type=Path,
        help="Path to graph JSON (e.g. session_log/sessions/.../graph_<session_id>.json)",
    )
    parser.add_argument(
        "--mode",
        choices=("code", "design_docs", "artifacts"),
        default="code",
        help="code: regenerate code only (default). design_docs: regenerate designs. artifacts: full generate_all.",
    )
    parser.add_argument(
        "--outputs-dir",
        type=Path,
        default=None,
        help="Artifact output directory (default: <graph_dir>/outputs_<session_id>)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Override session id (default: parsed from graph_<id>.json filename)",
    )
    parser.add_argument(
        "--no-design-docs",
        action="store_true",
        help="For --mode code: pass has_design_docs=False (ignore designs/*.json).",
    )
    parser.add_argument(
        "--yes-design-docs",
        action="store_true",
        help="For --mode code: force has_design_docs=True.",
    )
    args = parser.parse_args()

    graph_path = args.graph.resolve()
    if not graph_path.is_file():
        print(f"Error: file not found: {graph_path}", file=sys.stderr)
        return 1

    session_id = (args.session_id or _session_id_from_graph_path(graph_path)).strip()
    if not session_id:
        print("Error: could not determine session_id", file=sys.stderr)
        return 1

    outputs_dir = args.outputs_dir
    if outputs_dir is None:
        outputs_dir = graph_path.parent / f"outputs_{session_id}"
    else:
        outputs_dir = outputs_dir.resolve()

    try:
        from core.dtg_artifact_generator import DTGArtifactGenerator, LocalBuildFailedError
        from core.hlig_dtg_graphs import HLIGGraph
    except ImportError as e:
        print(f"Error: failed to import project modules: {e}", file=sys.stderr)
        print("Run from project root with: uv run python scripts/rerun_from_graph.py ...", file=sys.stderr)
        return 1

    try:
        hlig = HLIGGraph.from_persisted_file(graph_path)
    except Exception as e:
        print(f"Error loading graph: {e}", file=sys.stderr)
        return 1

    gen = DTGArtifactGenerator()

    try:
        if args.mode == "design_docs":
            date_dir = graph_path.parent
            out = gen.generate_design_docs_only(hlig, session_id, date_dir)
            print(f"Design docs written under: {out}")
        elif args.mode == "artifacts":
            date_dir = graph_path.parent
            out = gen.generate_all(hlig, session_id, date_dir)
            print(f"Artifacts written under: {out}")
            if out:
                _provision(hlig, out, session_id)
        else:
            # code
            outputs_dir.mkdir(parents=True, exist_ok=True)
            if args.yes_design_docs:
                has_design_docs = True
            elif args.no_design_docs:
                has_design_docs = False
            else:
                has_design_docs = _outputs_has_design_docs(outputs_dir)

            from core.dtg_artifact_generator import _write_interface_definitions

            _write_interface_definitions(hlig, outputs_dir)
            out = gen.generate_code_only(
                hlig,
                session_id,
                outputs_dir,
                has_design_docs=has_design_docs,
            )
            print(f"Code generation finished. Outputs: {out}")
            print(f"has_design_docs={has_design_docs}")
            if out:
                _provision(hlig, out, session_id)
    except LocalBuildFailedError as e:
        print(f"Local build failed after retries: {e}", file=sys.stderr)
        if getattr(e, "stderr", None):
            print("--- stderr (excerpt) ---", file=sys.stderr)
            print(str(e.stderr)[:4000], file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
