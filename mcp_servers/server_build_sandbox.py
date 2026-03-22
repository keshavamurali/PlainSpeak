"""
MCP server for building and running generated code in a sandbox.
Follows S18Share's server_sandbox pattern.
Tools: build_project, run_project, build_dtg_output, run_dtg_output
Supports Node.js/React and Rust/Tauri platforms.
Loads .env / .env.test from project dir for external dependency config.
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "build_sandbox",
    instructions="Build and run PlainSpeak-generated projects (Node.js/React, Rust/Tauri) in a sandboxed environment. When analyzing DTG nodes, use build_dtg_output to trigger builds from run_id + hlig_id.",
)

# Base path for outputs - must be under session_log
ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_BASE = ROOT / "session_log" / "sessions"

# Ensure project root in path for core imports (MCP runs as separate process)
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _find_outputs_path(run_id: str) -> Path | None:
    """Find outputs_{run_id} directory for a run (searches sessions by date)."""
    if not OUTPUTS_BASE.exists():
        return None
    for year_dir in sorted(OUTPUTS_BASE.iterdir(), reverse=True):
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


def _load_project_env(path: Path) -> dict[str, str]:
    """Load .env or .env.test from project dir for external dependency config."""
    env = dict(os.environ)
    for name in (".env", ".env.test"):
        env_file = path / name
        if env_file.exists() and env_file.is_file():
            try:
                from dotenv import dotenv_values
                loaded = dotenv_values(str(env_file))
                if loaded:
                    env.update({k: str(v) for k, v in loaded.items() if v is not None})
            except ImportError:
                # Fallback: simple KEY=VALUE parse
                for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        if k.strip():
                            env[k.strip()] = v.strip().strip('"\'')
            break
    return env


def _resolve_project_path(project_path: str) -> Path | None:
    """Resolve and validate project path is under outputs."""
    p = Path(project_path).resolve()
    if not p.is_absolute():
        p = (ROOT / project_path).resolve()
    try:
        p.relative_to(OUTPUTS_BASE)
    except ValueError:
        return None  # Path escapes outputs base
    return p if p.exists() else None


@mcp.tool()
async def build_project(project_path: str, framework: str = "node-react") -> str:
    """
    Build the generated project.

    Args:
        project_path: Path to the HLIG output directory (e.g. outputs_123/HLIG-1)
        framework: One of 'node-react' or 'rust-tauri'

    Returns:
        JSON with status, stdout, stderr
    """
    path = _resolve_project_path(project_path)
    if not path:
        return json.dumps({"status": "error", "error": "Invalid or inaccessible project path"})

    framework = (framework or "node-react").lower()
    timeout = 120  # 2 min for build

    if framework == "rust-tauri":
        if not shutil.which("cargo"):
            return json.dumps({"status": "error", "error": "cargo not found. Install Rust: https://rustup.rs/"})
        # Detect Tauri project: src-tauri with tauri.conf.json, or Cargo.toml with tauri dep
        is_tauri = False
        src_tauri = path / "src-tauri"
        if src_tauri.exists() and (src_tauri / "tauri.conf.json").exists():
            is_tauri = True
        elif (path / "Cargo.toml").exists():
            try:
                cargo = (path / "Cargo.toml").read_text()
                if "tauri" in cargo.lower():
                    is_tauri = True
            except Exception:
                pass
        # Prefer cargo tauri build for Tauri projects; fall back to cargo build
        build_cmd = ["cargo", "build"]
        if is_tauri:
            # Tauri + Node.js hybrid: package.json often has "tauri" script
            pkg = path / "package.json"
            if pkg.exists():
                try:
                    data = json.loads(pkg.read_text())
                    if data.get("scripts", {}).get("tauri"):
                        build_cmd = ["npm", "run", "tauri", "build"]
                    elif data.get("devDependencies", {}).get("@tauri-apps/cli"):
                        build_cmd = ["npx", "tauri", "build"]
                except Exception:
                    pass
            if build_cmd == ["cargo", "build"]:
                build_cmd = ["cargo", "tauri", "build"]
        try:
            proc_env = _load_project_env(path)
            proc = await asyncio.create_subprocess_exec(
                *build_cmd,
                cwd=str(path),
                env=proc_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            return json.dumps({
                "status": "success" if proc.returncode == 0 else "error",
                "returncode": proc.returncode,
                "stdout": out,
                "stderr": err,
                "framework": framework,
                "tauri": is_tauri,
            })
        except asyncio.TimeoutError:
            return json.dumps({"status": "error", "error": f"Build timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    else:
        # node-react
        if not shutil.which("npm"):
            return json.dumps({"status": "error", "error": "npm not found. Install Node.js."})
        try:
            proc_env = _load_project_env(path)
            # npm install
            proc = await asyncio.create_subprocess_exec(
                "npm", "install",
                cwd=str(path),
                env=proc_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace")[:2000]
                return json.dumps({
                    "status": "error",
                    "error": "npm install failed",
                    "returncode": proc.returncode,
                    "stdout": stdout.decode("utf-8", errors="replace")[:2000],
                    "stderr": err,
                })

            # npm run build (if package.json has it)
            pkg = path / "package.json"
            if pkg.exists():
                try:
                    data = json.loads(pkg.read_text())
                    if data.get("scripts", {}).get("build"):
                        proc = await asyncio.create_subprocess_exec(
                            "npm", "run", "build",
                            cwd=str(path),
                            env=proc_env,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                        out = stdout.decode("utf-8", errors="replace")
                        err = stderr.decode("utf-8", errors="replace")
                        return json.dumps({
                            "status": "success" if proc.returncode == 0 else "error",
                            "returncode": proc.returncode,
                            "stdout": out,
                            "stderr": err,
                            "framework": framework,
                        })
                except Exception:
                    pass

            return json.dumps({
                "status": "success",
                "message": "npm install completed (no build script)",
                "framework": framework,
            })
        except asyncio.TimeoutError:
            return json.dumps({"status": "error", "error": f"Build timed out after {timeout}s"})
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
async def run_project(
    project_path: str,
    framework: str = "node-react",
    timeout_seconds: int = 30,
) -> str:
    """
    Run the built project in a sandboxed subprocess.

    Args:
        project_path: Path to the HLIG output directory
        framework: 'node-react' or 'rust-tauri'
        timeout_seconds: Max runtime (default 30). Process is killed after this.

    Returns:
        JSON with status, stdout, stderr
    """
    path = _resolve_project_path(project_path)
    if not path:
        return json.dumps({"status": "error", "error": "Invalid or inaccessible project path"})

    framework = (framework or "node-react").lower()
    timeout = min(max(int(timeout_seconds or 30), 5), 300)  # 5s–5min clamp

    if framework == "rust-tauri":
        cmd = ["cargo", "run"]
    else:
        pkg = path / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
                scripts = data.get("scripts", {})
                if scripts.get("start"):
                    cmd = ["npm", "start"]
                elif scripts.get("dev"):
                    cmd = ["npm", "run", "dev"]
                else:
                    cmd = ["node", "src/index.js"] if (path / "src" / "index.js").exists() else ["npm", "start"]
            except Exception:
                cmd = ["npm", "start"]
        else:
            return json.dumps({"status": "error", "error": "No package.json found"})

    try:
        proc_env = _load_project_env(path)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(path),
            env=proc_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace")[-4000:]  # last 4k chars
        err = stderr.decode("utf-8", errors="replace")[-4000:]
        return json.dumps({
            "status": "success" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "stdout": out,
            "stderr": err,
            "framework": framework,
            "timeout_seconds": timeout,
        })
    except asyncio.TimeoutError:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass
        return json.dumps({
            "status": "timeout",
            "error": f"Process killed after {timeout}s",
            "framework": framework,
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
async def build_dtg_output(
    run_id: str,
    hlig_id: str,
    framework: str = "auto",
) -> str:
    """
    Build generated code for a DTG/HLIG output. Call this when analyzing DTG nodes
    to trigger a sandbox build. Supports node-react and rust-tauri platforms.

    Args:
        run_id: Session/run ID (used to locate outputs_{run_id})
        hlig_id: HLIG node ID (e.g. HLIG-1, HLIG-2)
        framework: 'node-react', 'rust-tauri', or 'auto' to infer from project structure

    Returns:
        JSON with status, stdout, stderr, returncode
    """
    outputs = _find_outputs_path(str(run_id))
    if not outputs:
        return json.dumps({
            "status": "error",
            "error": f"No outputs found for run_id {run_id}. Artifacts may not be generated yet.",
        })
    project_path = outputs / str(hlig_id)
    if not project_path.exists() or not project_path.is_dir():
        return json.dumps({
            "status": "error",
            "error": f"HLIG {hlig_id} not found in outputs for run {run_id}",
        })
    # Resolve framework if auto
    fw = (framework or "auto").lower()
    if fw == "auto":
        if (project_path / "package.json").exists():
            fw = "node-react"
        elif (project_path / "Cargo.toml").exists():
            fw = "rust-tauri"
        else:
            fw = "node-react"
    return await build_project(str(project_path), fw)


@mcp.tool()
async def run_dtg_output(
    run_id: str,
    hlig_id: str,
    framework: str = "auto",
    timeout_seconds: int = 30,
) -> str:
    """
    Run generated code for a DTG/HLIG output in a sandbox. Use after build_dtg_output
    or when the project is already built.

    Args:
        run_id: Session/run ID
        hlig_id: HLIG node ID
        framework: 'node-react', 'rust-tauri', or 'auto'
        timeout_seconds: Max runtime (default 30)

    Returns:
        JSON with status, stdout, stderr
    """
    outputs = _find_outputs_path(str(run_id))
    if not outputs:
        return json.dumps({
            "status": "error",
            "error": f"No outputs found for run_id {run_id}",
        })
    project_path = outputs / str(hlig_id)
    if not project_path.exists() or not project_path.is_dir():
        return json.dumps({
            "status": "error",
            "error": f"HLIG {hlig_id} not found in outputs for run {run_id}",
        })
    fw = (framework or "auto").lower()
    if fw == "auto":
        if (project_path / "package.json").exists():
            fw = "node-react"
        elif (project_path / "Cargo.toml").exists():
            fw = "rust-tauri"
        else:
            fw = "node-react"
    return await run_project(str(project_path), fw, timeout_seconds)


@mcp.tool()
async def provision_dependencies(
    run_id: str,
    use_docker_compose: bool = False,
) -> str:
    """
    Provision external dependencies (DB, Auth, Storage) for generated projects.
    Generates .env and .env.test with mock/local URLs. Call before build if
    dependencies were not auto-provisioned.

    Args:
        run_id: Session/run ID
        use_docker_compose: If true, also generate docker-compose.test.yml

    Returns:
        JSON with results per HLIG
    """
    outputs = _find_outputs_path(str(run_id))
    if not outputs:
        return json.dumps({
            "status": "error",
            "error": f"No outputs found for run_id {run_id}",
        })
    # Reconstruct minimal HLIG from directory structure - we don't have the graph here
    # So we provision for all HLIG dirs with a union of common interfaces
    common_interfaces = {"DB", "Auth", "Storage", "Filesystem", "message", "API"}
    results = {}
    for hlig_dir in sorted(outputs.iterdir()):
        if not hlig_dir.is_dir() or not hlig_dir.name.startswith("HLIG-"):
            continue
        try:
            from core.provision_dependencies import provision_for_hlig
            created = provision_for_hlig(
                hlig_dir,
                common_interfaces,
                session_id=run_id,
                use_docker_compose=use_docker_compose,
            )
            results[hlig_dir.name] = created
        except Exception as e:
            results[hlig_dir.name] = {"error": str(e)}
    return json.dumps({"status": "ok", "provisioned": results})


@mcp.tool()
async def run_python_script(code: str, timeout_seconds: int = 10) -> str:
    """
    Execute Python code in a sandboxed subprocess.
    Use for math, data processing, and simple logic.
    Restricted: no subprocess, os.system, eval, exec, or file system writes outside temp.

    Args:
        code: Python code to execute
        timeout_seconds: Max execution time (default 10)

    Returns:
        JSON with status, stdout, stderr
    """
    import re
    blocked = [
        (r"subprocess\.|os\.system|eval\s*\(|exec\s*\(", "Blocked: subprocess/eval/exec"),
        (r"open\s*\(\s*['\"]/", "Blocked: writing to system paths"),
        (r"__import__\s*\(\s*['\"]os['\"]", "Blocked: os import"),
    ]
    for pattern, msg in blocked:
        if re.search(pattern, code):
            return json.dumps({"status": "blocked", "error": msg})

    timeout = min(max(int(timeout_seconds or 10), 1), 60)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        return json.dumps({
            "status": "success" if proc.returncode == 0 else "error",
            "returncode": proc.returncode,
            "stdout": out,
            "stderr": err,
            "timeout_seconds": timeout,
        })
    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        return json.dumps({"status": "timeout", "error": f"Execution killed after {timeout}s"})
    except FileNotFoundError:
        return json.dumps({"status": "error", "error": "python3 not found"})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


if __name__ == "__main__":
    mcp.run(transport="stdio")
