"""Artifacts router - build and run generated code."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.state import get_multi_mcp, PROJECT_ROOT

router = APIRouter()

SESSIONS_BASE = PROJECT_ROOT / "session_log" / "sessions"


def _find_outputs_path(run_id: str) -> Path | None:
    """Find outputs_{run_id} directory for a run."""
    if not SESSIONS_BASE.exists():
        return None
    for year_dir in sorted(SESSIONS_BASE.iterdir(), reverse=True):
        if not year_dir.is_dir():
            continue
        for month_dir in sorted(year_dir.iterdir(), reverse=True):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir(), reverse=True):
                if not day_dir.is_dir():
                    continue
                outputs = day_dir / f"outputs_{run_id}"
                if outputs.exists():
                    return outputs
    return None


class BuildRequest(BaseModel):
    run_id: str
    hlig_id: str
    framework: str = "node-react"


class RunRequest(BaseModel):
    run_id: str
    hlig_id: str
    framework: str = "node-react"
    timeout_seconds: int = 30


class BuildAllRequest(BaseModel):
    run_id: str


@router.post("/artifacts/build")
async def build_artifact(req: BuildRequest):
    """Build generated code for an HLIG output."""
    outputs = _find_outputs_path(req.run_id)
    if not outputs:
        raise HTTPException(status_code=404, detail=f"No outputs found for run {req.run_id}")
    project_path = outputs / req.hlig_id
    if not project_path.exists() or not project_path.is_dir():
        raise HTTPException(status_code=404, detail=f"HLIG {req.hlig_id} not found in outputs")

    mcp = get_multi_mcp()
    try:
        result = await mcp.route_tool_call("build_project", {
            "project_path": str(project_path),
            "framework": req.framework,
        })
        if hasattr(result, "content") and result.content:
            text = result.content[0].text if result.content else ""
        else:
            text = str(result)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": "ok", "output": text}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/artifacts/build-all")
async def build_all_artifacts(req: BuildAllRequest):
    """Build all HLIG outputs for a run via MCP sandbox (Node.js/React, Tauri)."""
    outputs = _find_outputs_path(req.run_id)
    if not outputs:
        raise HTTPException(status_code=404, detail=f"No outputs found for run {req.run_id}")
    mcp = get_multi_mcp()
    results = []
    for hlig_dir in sorted(outputs.iterdir()):
        if not hlig_dir.is_dir() or not hlig_dir.name.startswith("HLIG-"):
            continue
        try:
            result = await mcp.route_tool_call("build_dtg_output", {
                "run_id": req.run_id,
                "hlig_id": hlig_dir.name,
                "framework": "auto",
            })
            if hasattr(result, "content") and result.content:
                text = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
            else:
                text = str(result)
            try:
                results.append({"hlig_id": hlig_dir.name, **json.loads(text)})
            except json.JSONDecodeError:
                results.append({"hlig_id": hlig_dir.name, "output": text[:500]})
        except Exception as e:
            results.append({"hlig_id": hlig_dir.name, "status": "error", "error": str(e)})
    return {"results": results}


@router.post("/artifacts/run")
async def run_artifact(req: RunRequest):
    """Run generated code in sandbox."""
    outputs = _find_outputs_path(req.run_id)
    if not outputs:
        raise HTTPException(status_code=404, detail=f"No outputs found for run {req.run_id}")
    project_path = outputs / req.hlig_id
    if not project_path.exists() or not project_path.is_dir():
        raise HTTPException(status_code=404, detail=f"HLIG {req.hlig_id} not found in outputs")

    mcp = get_multi_mcp()
    try:
        result = await mcp.route_tool_call("run_project", {
            "project_path": str(project_path),
            "framework": req.framework,
            "timeout_seconds": req.timeout_seconds,
        })
        if hasattr(result, "content") and result.content:
            text = result.content[0].text if result.content else ""
        else:
            text = str(result)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"status": "ok", "output": text}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
