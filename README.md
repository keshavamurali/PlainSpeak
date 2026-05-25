# PlainSpeak

**PlainSpeak** turns natural-language software requests into **structured graphs** and **generated artifacts** (design specs, code, tests) using a pipeline of LLM agents. You describe what you want in plain language; the system builds a **recursive High-Level Intent Graph (HLIG)** of composite, atomic, and contract nodes, then runs designers, coders, reviewers, and optional local builds so outputs land under `session_log/` for inspection and iteration.

The project is aimed at **LLM-agnostic**, **deterministic** graph formats and **traceable** runs (causal edges, dependency matrix pins, build logs) so generated code stays aligned with a single specification.

---

## Language specification (HLIG / DTG)

The full formal schema—recursive `graph`/`child_graph`, node `kind`, `task_type`, CVP (`causal`), contract nodes, artifact registry, and validation rules—is defined here:

**[language_readme.md](language_readme.md)** — *HLIG/DTG Language Specification* (authoritative reference).

Use that document whenever you need exact field names, JSON shapes, or graph semantics beyond this README.

---

## How to use PlainSpeak

### 1. Prerequisites

- **Python 3.10+** with **[uv](https://github.com/astral-sh/uv)** (recommended) for dependencies  
- **Node.js** (for the web UI in `frontend/`)  
- An **LLM** — default is Google **Gemini**; **Ollama** is supported for local models (see below)

### 2. Configure the LLM

PlainSpeak uses an LLM for agents (planner, designer, coder, reviewers, etc.).

**Gemini** (default):

```bash
export GEMINI_API_KEY="your-key"
# Or create .env with GEMINI_API_KEY=...
```

**Ollama** (local):

```bash
# Set in config/settings.json or environment, e.g.:
# PLAINSPEAK_MODEL_PROVIDER=ollama
# PLAINSPEAK_MODEL=phi4
ollama run phi4   # Ensure the model is pulled
```

### 3. Run the API (backend)

From the repository root:

```bash
uv sync
uv run uvicorn api:app --host 0.0.0.0 --port 8000
# Alternative:
# uv run python api.py
```

The HTTP API exposes runs (e.g. create a run, stream events, send clarification). Default pipeline and agent steps are configured in **`agents/config/agents.yaml`**.

### 4. Run the web UI (frontend)

**Development** (Vite dev server, typically proxying to the API):

```bash
cd frontend && npm install && npm run dev
```

Open **http://localhost:5173**

**Production-style** (build + static serve):

```bash
cd frontend && npm install && npm run build && npm run serve
```

### 5. Create a run via the API

Example: **`POST /api/runs`** with a JSON body:

```json
{
  "query": "Describe your project in natural language…",
  "pipeline": "hlig_no_design_docs"
}
```

- **`query`** — User request (required).  
- **`pipeline`** — Optional. If omitted, the default in `agents/config/agents.yaml` is used (e.g. `hlig_no_design_docs`). Other examples: `hlig_full`, `minimal`.

Session artifacts, graphs, and generated code are written under **`session_log/sessions/…`** (see `session/manager.py` and your run id).

### 6. Pipelines and cost

- **`hlig_full`** — Planner → Designer (`child_graph`) → Design doc generator → Design reviewer → Coder → … (full recursive pipeline).  
- **`hlig_no_design_docs`** — Skips separate design-doc LLM steps; faster / lower cost (common default).  
- Pipelines are listed in **`agents/config/agents.yaml`** under `pipelines:`.

For env toggles (local `cargo`/`npm` checks, truncation, cost limits), see **`prompts/README.md`** and the **Cost Optimizations** section below.

### 7. Where to look next

| Topic | Location |
|--------|----------|
| HLIG/DTG JSON schema & rules | **[language_readme.md](language_readme.md)** |
| Agent steps & pipeline names | `agents/config/agents.yaml` |
| Pinned dependency / codegen rules | `agents/config/dependency_matrix.yaml` |
| Codegen / local build env vars | `prompts/README.md` |
| Tools / scripts | `tools/README.md` |

---

## External dependencies (DB, Auth, Storage)

PlainSpeak can auto-provision mock config for external interfaces declared on composite HLIG nodes (`DB`, `Auth`, `Storage`, `Filesystem`, `API`, etc.). After artifact generation, you may see:

- **`.env`** / **`.env.test`** — mock URLs (e.g. SQLite, local paths)  
- **`data/`**, **`storage/`** — local storage dirs  

For real services, see project notes on `provision_dependencies` / Docker Compose in your deployment docs.

---

## Pipeline overview (example: HLIG full)

A typical full pipeline runs agents in order, for example:

1. **Planner** — Builds recursive HLIG graph from the user request  
2. **Designer** — Builds `child_graph` (atomic DTGs + contract nodes) per HLIG composite  
3. **Design doc generator** — (if pipeline includes it) design JSON for design-type DTG nodes  
4. **Design reviewer** — Reviews graphs  
5. **Coder** — Generates design artifacts and code per DTG  
6. **Code reviewer** — Reviews generated code  
7. **Builder** — Builds via MCP sandbox (if configured)  
8. **Unit / integration / system testers** — As configured  

Set `default_pipeline` in `agents/config/agents.yaml` or pass **`pipeline`** on the run request to choose a shorter graph (e.g. `minimal`).

---

## Design specs (LLM-oriented)

Design-type nodes can produce **canonical `design_spec` JSON** (architecture, `implementation_instructions`, constraints). Downstream codegen consumes that JSON. Paths often look like `…/designs/<node>_<title>.json`. See **[language_readme.md](language_readme.md)** for structure.

---

## Cost optimizations

- **`hlig_no_design_docs`** — Fewer LLM steps; set as `default_pipeline` or pass in the run body.  
- **`pipeline`** per run — `POST /api/runs` with `"pipeline": "hlig_no_design_docs"`.  
- **Designer limits** — `max_design_nodes` / `max_code_nodes` in `agents.yaml` under the designer step.  
- **Context truncation** — `DESIGN_CONTEXT_MAX_CHARS`, `CODE_CONTEXT_MAX_CHARS`, `BUILD_RETRY_FILES_MAX_TOTAL_CHARS` (see `prompts/README.md`).  
- **`COST_LIMIT_USD`** — Stops the run when exceeded; set to `0` to disable.  

---

## CVP (Causal Visual Programming)

PlainSpeak attaches **causal** semantics to some HLIG edges, scopes agent context to causal parents where applicable, and can emit **`causal_path.json`** in artifact dirs for traceability. Details and edge rules are specified in **[language_readme.md](language_readme.md)** (CVP sections).

---

## Requirements / roadmap

High-level goals for the project (not all may be implemented in a single release):

1. Take natural language and infer requirements  
2. Ask clarifications when needed  
3. Build a high-level plan graph (e.g. backend, frontend, APIs)  
4. Confirm with the user where appropriate  
5. Expand each subsystem into a detailed task graph  
6. Attach unit-test and integration-test steps to the graph  
7. Re-plan or retry when nodes fail  
8. Visualize / track graph state and progress  
9. Edit and re-run parts of the graph  
10. Automated dependency installation where applicable  
11. Clearly document what is supported vs out of scope  
