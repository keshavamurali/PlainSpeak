# PlainSpeak
Build Softwares using Natural Language instructions

## LLM Setup

PlainSpeak uses an LLM for agents (planner, coder, reviewer). Configure via:

**Gemini** (default):
```bash
export GEMINI_API_KEY="your-key"
# Or create .env with GEMINI_API_KEY=...
```

**Ollama** (local):
```bash
# Set in config/settings.json or env:
# PLAINSPEAK_MODEL_PROVIDER=ollama
# PLAINSPEAK_MODEL=phi4
ollama run phi4  # Ensure model is pulled
```

## Quick Start

### Backend (FastAPI)
```bash
uv sync
uv run python api.py
```

### Frontend

**Development** (Vite dev server with API proxy):
```bash
cd frontend && npm install && npm run dev
```
Open http://localhost:5173

**Production** (Node server serving built assets):
```bash
cd frontend && npm install && npm run build && npm run serve
```
Open http://localhost:5173

## External Dependencies (DB, Auth, Storage)

PlainSpeak auto-provisions mock config for external interfaces declared in HLIG nodes (`DB`, `Auth`, `Storage`, `Filesystem`, `message`, `API`). After artifact generation, it creates:

- **`.env`** and **`.env.test`** – Mock/local URLs (SQLite, local paths, dev secrets)
- **`data/`, `storage/`** – Directories for SQLite and file storage

The build sandbox loads these env vars when building and running. Use SQLite for DB and local paths for storage in generated code so builds/tests run without real services.

For real Postgres/Redis/MinIO, use `provision_dependencies` MCP tool with `use_docker_compose: true` to generate `docker-compose.test.yml`, then run `docker compose -f docker-compose.test.yml up -d` before build.

## Pipeline (HLIG Full)

The default pipeline runs 9 agents in order:

1. **Planner** — Build HLIG from user requirements
2. **Designer** — Build DTG for each HLIG node
3. **Design Reviewer** — Review graphs for correctness, dependencies, inconsistencies
4. **Coder** — Generate design docs and code per DTG node
5. **Code Reviewer** — Review generated code
6. **Builder** — Build generated code via MCP sandbox
7. **Unit Tester** — Generate and run unit tests
8. **Integration Tester** — Test interfaces between HLIG nodes
9. **System Tester** — Full system-level testing

Set `default_pipeline: default` in `agents/config/agents.yaml` to use the shorter pipeline (planner, clarification, coder, reviewer).

## CVP (Causal Visual Programming)

PlainSpeak integrates causal semantics to reduce hallucinations and improve robustness:

- **Causal edges**: HLIG edges may set `causal: true` to denote direct causation (Planner prompt).
- **Markov blanket scoping**: Agent context is restricted to outputs from causal parents only.
- **Causal path traceability**: Each HLIG output directory includes `causal_path.json` listing the chain of nodes that led to it (for audit and explainability).

See `language_readme.md` for the full schema.

# Requirements
1. Need to be able to take the Natural Language Input and understand the requirement
2. Ask for Clarification, to understand what user means by the query, as needed.
3. Create a high level Plan Graph. E.g: "Create a Backend Server, a Front End, Business Logic part, all connected together with APIs".
4. Get confirmation from the user to make sure that it is correct.
5. For each of the nodes, create a detailed graph. It can be a sub-graph of the main graph.
6. Attach the Test step for each of the sub-graphs (Unit test)
7. Attach the Test case node (Integration test cases) to the end of the graph.
8. If any node fails, add the re-plan
9. Display of the Graph in each step, and ability to track what is going on.
10. Ability to edit and re-run the part of the graphs.
11. Automated Dependency installer for Libraries
12. Clearly provide what is possible and what is not.

