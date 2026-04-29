# HLIG/DTG Language Specification

**Version:** 1.1.0  
**Status:** Draft Standard  
**Encoding:** UTF-8, JSON  
**Date:** 2026

---

## 1. Introduction

This document defines the **HLIG (High-Level Intent Graph)** and **DTG (Detailed Task Graph)** language—a structured representation for decomposing software project intent into executable task graphs. The language is designed to be:

- **Deterministic**: Same input yields consistent structure
- **LLM-agnostic**: Consumable by any language model or agent
- **Tool-neutral**: No vendor-specific or model-specific constructs
- **Self-describing**: Each node carries sufficient context for independent execution

The specification is suitable for publication as a formal standard and for implementation by third-party systems.

**CVP (Causal Visual Programming):** PlainSpeak integrates causal semantics from CVP to reduce LLM hallucinations and improve robustness. Edges may carry `causal: true` to denote direct causation; agents receive context restricted to causal parents (Markov blanket); and artifacts record their causal path for traceability.

**Control vs data flow:** Edges may be classified with `edge_type` as **control** (execution order; used for topological sort) or **data** (dependency context only). This separation keeps execution order deterministic while allowing rich data-dependency information for code and design generation. DTG nodes may carry **node_type** (design, coding, evaluation, tool, reasoning) for clear role classification and visualization.

---

## 2. Conventions

### 2.1 Terminology

| Term | Definition |
|------|------------|
| **Graph** | A directed acyclic graph (DAG) of nodes and edges |
| **Node** | A vertex representing a unit of work or subsystem |
| **Edge** | A directed link expressing dependency or data flow |
| **Subsystem** | A cohesive unit of functionality (HLIG scope) |
| **Task** | A unit of work with defined inputs and outputs |
| **Artifact** | A concrete deliverable (document, code module, test suite) |

### 2.2 Nomenclature Rules

- **Identifiers**: Use hyphenated prefixes and numeric suffixes (e.g., `HLIG-1`, `DTG-2-3`)
- **Keys**: snake_case for all JSON property names
- **Values**: Strings, arrays, or objects; no custom types
- **Optional vs Required**: Required fields are marked `[required]`; others are optional

### 2.3 ID Conventions

| Prefix | Pattern | Example | Scope |
|--------|---------|---------|-------|
| HLIG | `HLIG-{N}` | `HLIG-1`, `HLIG-2` | Global, unique per project |
| DTG | `DTG-{H}-{N}` | `DTG-1-1`, `DTG-2-5` | Unique within parent HLIG node `HLIG-{H}` |
| DTG root | `DTG-{H}` | `DTG-1` | Logical root ID for the DTG of `HLIG-{H}` |

Where `{H}` and `{N}` are positive integers.

---

## 3. HLIG — High-Level Intent Graph

### 3.1 Definition

An **HLIG** is a directed graph whose nodes represent **subsystems** of a software project. Each subsystem has inputs, outputs, external interfaces, and an optional **DTG** that decomposes it into finer-grained tasks. Edges between HLIG nodes describe how subsystems communicate.

### 3.2 Purpose

- Capture high-level architecture from natural-language requirements
- Partition a project into independently buildable components
- Define interfaces between subsystems (API, DB, message, etc.)
- Serve as the root structure for DTG generation

### 3.3 Top-Level Schema

```json
{
  "project": { ... },
  "interfaces": { ... },
  "hlig": {
    "nodes": [ ... ],
    "edges": [ ... ]
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | object | No | Project metadata |
| `interfaces` | object | No | Interface registry for reuse (see 3.6.2) |
| `hlig` | object | Yes | The HLIG graph |
| `hlig.nodes` | array | Yes | HLIG nodes |
| `hlig.edges` | array | Yes | HLIG edges |

### 3.4 Project Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Project name |
| `description` | string | No | One-paragraph project description |

### 3.5 HLIG Node Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier; pattern `HLIG-{N}` |
| `task` | string | Yes | High-level subsystem task description |
| `inputs` | array of string | Yes | List of inputs to this subsystem. SHOULD use **canonical artifact names** (e.g. snake_case identifiers like `http_requests`, `order_requests`) so that DTG nodes and data-flow edges can reference them exactly. |
| `outputs` | array of string | Yes | List of outputs from this subsystem. SHOULD use **canonical artifact names** (e.g. `web_content`, `order_api_spec`) so that downstream DTG nodes can refer to them in `inputs_required`. |
| `language` | string | No | Preferred implementation language (default: `"Rust, Tauri, React, CSS"`) |
| `external_interfaces` | array of string | No | External systems this subsystem interacts with |
| `dtg_root` | string | No | ID of the root of the corresponding DTG; pattern `DTG-{N}` |
| `dtg` | object | No | Embedded DTG (see Section 4); present when DTG is generated |

**Standard Values for `external_interfaces`:**

| Value | Meaning |
|-------|---------|
| `API` | REST, GraphQL, or RPC API |
| `DB` | Database (SQL, NoSQL) |
| `Filesystem` | Local or remote file storage |
| `Auth` | Authentication/authorization service |
| `message` | Message queue or pub/sub |
| `None` | No external interfaces |

Implementations may extend this set.

### 3.6 HLIG Edge Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from` | string | Yes | Source node ID (alias: `source`) |
| `to` | string | Yes | Target node ID (alias: `target`) |
| `edge_type` | string | No | **Control vs data**: `control` (execution order; default) or `data` (data dependency only). Only control edges participate in topological ordering; data edges are used for dependency context. |
| `interface_type` | string | No | How the two subsystems communicate |
| `causal` | boolean | No | **CVP**: If `true`, source is a *direct cause* of target (mechanistic dependency). When omitted, defaults to `true` for backward compatibility. Distinguishes data-flow from causal flow. |
| `interface_spec` | object | No | Inline interface definition (see 3.6.1) |
| `interface_ref` | string | No | Key in `interfaces` registry for reuse (see 3.6.2) |

**Standard Values for `interface_type`:**

| Value | Meaning |
|-------|---------|
| `API` | HTTP/RPC or similar API boundary |
| `DB` | Shared database |
| `message` | Async message queue |
| `dependency` | Generic ordering dependency |
| `Filesystem` | Shared filesystem |

Implementations may extend with domain-specific types.

#### 3.6.1 Interface Spec (Inline)

When `interface_spec` is provided on an edge, it defines the contract at that boundary. Use `interface_spec` for edge-specific definitions, or `interface_ref` to reference a shared definition from the registry.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | One of `api`, `database`, `message`, `file` |
| `description` | string | No | Human-readable description |
| `schema` | object | No | Inline schema (tables/columns for DB, etc.) |
| `endpoints` | array | No | For `api`: list of endpoints with method, path, request, response |
| `ref` | string | No | Reference to external spec (e.g., OpenAPI, JSON Schema) |

**Example (API):**

```json
{
  "from": "HLIG-2",
  "to": "HLIG-1",
  "interface_type": "API",
  "interface_spec": {
    "type": "api",
    "description": "Order management API",
    "endpoints": [
      {
        "method": "POST",
        "path": "/orders",
        "request": { "items": "array", "customer_id": "string" },
        "response": { "order_id": "string", "status": "string" }
      }
    ]
  }
}
```

**Example (Database):**

```json
{
  "from": "HLIG-2",
  "to": "HLIG-1",
  "interface_type": "DB",
  "interface_spec": {
    "type": "database",
    "description": "Shared order data",
    "schema": {
      "orders": {
        "id": "uuid",
        "customer_id": "uuid",
        "status": "string"
      }
    }
  }
}
```

#### 3.6.2 Interface Registry

The top-level `interfaces` object allows reuse of interface definitions across multiple edges. Keys are interface identifiers; values follow the same structure as `interface_spec`.

```json
{
  "interfaces": {
    "OrderAPI": {
      "type": "api",
      "description": "Order management API",
      "endpoints": [ ... ]
    },
    "OrderDB": {
      "type": "database",
      "schema": { ... }
    }
  },
  "hlig": {
    "edges": [
      {
        "from": "HLIG-2",
        "to": "HLIG-1",
        "interface_type": "API",
        "interface_ref": "OrderAPI"
      }
    ]
  }
}
```

When both `interface_spec` and `interface_ref` are present, `interface_spec` takes precedence (inline overrides registry).

### 3.7 HLIG Graph Constraints

- **Acyclicity**: The graph MUST be acyclic.
- **Node Uniqueness**: Each `id` MUST be unique.
- **Edge Validity**: `from` and `to` MUST reference existing node IDs.
- **Connectivity**: Every node SHOULD have at least one incoming or outgoing edge (except single-node graphs).

### 3.8 HLIG Example

```json
{
  "project": {
    "name": "Coffee Shop Website",
    "description": "A web application for a coffee shop with content serving and order management."
  },
  "hlig": {
    "nodes": [
      {
        "id": "HLIG-1",
        "task": "Serve the coffee shop's website content, including web pages, styles, scripts, and media.",
        "inputs": ["HTTP Requests from Web Browser"],
        "outputs": ["Web Page Content (HTML, CSS, JS, Images)"],
        "language": "Rust, Tauri, React, CSS",
        "external_interfaces": ["Filesystem"],
        "dtg_root": "DTG-1"
      },
      {
        "id": "HLIG-2",
        "task": "Process and fulfill coffee orders via an API.",
        "inputs": ["Order requests from frontend"],
        "outputs": ["Order confirmation", "Inventory updates"],
        "language": "Rust, Tauri, React, CSS",
        "external_interfaces": ["API", "DB"],
        "dtg_root": "DTG-2"
      }
    ],
    "edges": [
      {
        "from": "HLIG-2",
        "to": "HLIG-1",
        "interface_type": "API",
        "interface_spec": {
          "type": "api",
          "description": "Order data API for frontend",
          "endpoints": [{ "method": "GET", "path": "/orders", "response": "Order list" }]
        }
      }
    ]
  }
}
```

---

## 4. DTG — Detailed Task Graph

### 4.1 Definition

A **DTG** is a directed acyclic graph that decomposes a single HLIG node into **atomic, executable subtasks**. Each DTG node has a `task_type` (design, code, test, etc.), defined inputs and outputs, and success criteria. DTG edges express ordering and data-flow dependencies.

### 4.2 Purpose

- Decompose a subsystem into implementation-ready tasks
- Support deterministic code generation and design documentation
- Enable parallel execution where dependencies allow
- Provide self-contained task descriptions for agents

### 4.3 Top-Level Schema

```json
{
  "hlig_node_id": "HLIG-1",
  "nodes": [ ... ],
  "edges": [ ... ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `hlig_node_id` | string | Yes | ID of the parent HLIG node |
| `nodes` | array | Yes | DTG nodes |
| `edges` | array | Yes | DTG edges |

### 4.4 DTG Node Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier; pattern `DTG-{H}-{N}` where `HLIG-{H}` is parent |
| `title` | string | Yes | Short descriptive subtask name |
| `description` | string | Yes | Detailed, deterministic explanation of the subtask |
| `task_type` | string | Yes | One of the enumerated task types (see below) |
| `node_type` | string | No | **Node classification**: `reasoning` \| `design` \| `coding` \| `evaluation` \| `tool`. When omitted, implementations MAY infer from `task_type` (e.g. design/documentation/contract → design; scaffold/code/build/integration/verification → coding; test → evaluation). Used for visualization and policy. |
| `inputs_required` | array of string | Yes | **Canonical artifact names** this node requires. Each entry MUST exactly match an `outputs_produced` name from a node listed in `dependencies`. Use snake_case identifiers (e.g. `architecture_spec`, `api_handlers`). Enables deterministic dependency resolution and validation. |
| `outputs_produced` | array of string | Yes | **Canonical artifact names** this node produces. Use exact, referrable names (snake_case) so that downstream nodes can list them in `inputs_required`. SHOULD align with or refine parent HLIG `outputs` where applicable. |
| `output_descriptions` | object | No | Optional map from artifact name (key) to short human-readable description (value). Used for prompts and docs; dependency matching uses the names in `outputs_produced` only. |
| `dependencies` | array of string | Yes | IDs of DTG nodes that must complete before this one |
| `success_criteria` | array of string | Yes | Objective, measurable acceptance criteria |
| `execution_spec` | object | No | Execution instructions for LLM/agent (see 4.4.1) |
| `parent_hlig` | object | No | Runtime enrichment: parent HLIG context (see 4.6) |
| `language` | string | No | Runtime enrichment: preferred language from parent |

**Node Types (`node_type`):**

| Value | Meaning |
|-------|---------|
| `design` | Architectural or documentation tasks |
| `coding` | Implementation, build, integration, verification |
| `evaluation` | Tests and validation |
| `tool` | Tool execution (MCP, scripts) |
| `reasoning` | Pure analysis/planning without design or code artifacts |

**Task Types (`task_type`):**

| Value | Meaning |
|-------|---------|
| `design` | Architectural or design documentation |
| `contract` | API/schema artifacts (OpenAPI, JSON Schema); no implementation code |
| `scaffold` | Project layout, empty files, config shells (deterministic; before code) |
| `code` | Implementation of code module(s) |
| `test` | Unit or integration tests |
| `integration` | Wiring components together (legacy; often treated as `code` at runtime) |
| `documentation` | User or developer documentation |
| `verification` | Validation or quality checks (legacy; often treated as `code` at runtime) |
| `build` | Build, packaging, or deployment configuration |
| `review` | Review intent in graph; paired with code steps in the execution engine |

#### 4.4.1 Execution Spec (Optional)

`execution_spec` describes how a node should be executed by an LLM or agent. It is LLM-agnostic and self-contained.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | No | Execution context: `design_doc`, `code`, `test`, `documentation`, etc. |
| `framework` | string | No | For `code`: target stack (e.g., `node-react`, `rust-tauri`) |
| `format` | string | No | Output format: `markdown`, `json`, etc. |
| `prompts` | object | No | Named prompt templates or instructions |
| `constraints` | array of string | No | Execution rules (e.g., `output_only_content`, `no_preamble`) |

**Example:**

```json
{
  "id": "DTG-1-2",
  "title": "Implement File Reading Module",
  "task_type": "code",
  "execution_spec": {
    "type": "code",
    "framework": "node-react",
    "format": "markdown",
    "constraints": ["output_only_content", "no_preamble"],
    "prompts": {
      "system": "You generate production-ready code. Follow existing project structure.",
      "output_rules": "Return only the code block, no explanations."
    }
  },
  ...
}
```

Implementations may extend `execution_spec` with vendor-specific fields under a namespace prefix.

**Canonical artifact names:** For deterministic dependency resolution, every entry in a node's `inputs_required` MUST equal one of the `outputs_produced` names of a node in that node's `dependencies`. Implementations MAY validate this and reject or warn on mismatches. On data-flow edges, `data_spec.output_ref` and `data_spec.input_ref` MUST reference these same canonical names.

### 4.5 DTG Edge Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from` | string | Yes | Source node ID (alias: `source`) |
| `to` | string | Yes | Target node ID (alias: `target`) |
| `edge_type` | string | No | **Control vs data**: `control` (default) or `data`. Only **control** edges participate in topological ordering; **data** edges define data dependencies used to build dependency context for code/design generation but do not affect execution order. When `dependency_type` is `data-flow`, `edge_type` SHOULD be `data`. |
| `dependency_type` | string | No | Semantics of the dependency |
| `description` | string | No | Human-readable reason for the dependency |
| `data_spec` | object | No | For `data-flow`: contract defining what flows (see 4.5.1) |

**Edge Types (`edge_type`):**

| Value | Meaning |
|-------|---------|
| `control` | Execution-order dependency; used for topological sort |
| `data` | Data-flow only; used for dependency context, not for ordering |

**Dependency Types (`dependency_type`):**

| Value | Meaning |
|-------|---------|
| `strict` | Target cannot start until source completes |
| `soft` | Optional ordering preference |
| `data-flow` | Output of source is input to target |

### 4.6 Runtime Enrichment: `parent_hlig`

When a DTG is consumed by an agent, each node MAY be enriched with `parent_hlig` for self-contained execution:

```json
{
  "parent_hlig": {
    "id": "HLIG-1",
    "task": "...",
    "inputs": ["..."],
    "outputs": ["..."],
    "language": "Rust, Tauri, React, CSS",
    "external_interfaces": ["Filesystem"]
  },
  "language": "Rust, Tauri, React, CSS"
}
```

This allows a DTG node to be passed to an LLM or agent without additional lookups.

#### 4.6.1 Canonical formats for dependency context

When passing dependency context to design or code generators, implementations use **canonical JSON** so that LLMs receive a consistent structure. Values in the dependency map are one of:

| Type | `type` field | When used |
|------|----------------|-----------|
| **design_spec** | `"design_spec"` | Output of a design/documentation DTG node (architecture, implementation_instructions, constraints, outputs, interface_refs). |
| **code_output** | `"code_output"` | Output of a prior code DTG node (node_id, files with path and content_preview). |
| **dtg_node_ref** | `"dtg_node_ref"` | Lightweight reference when no design spec exists (e.g. `hlig_no_design_docs` pipeline): node_id, title, description, inputs_required, outputs_produced, success_criteria. |

Implementations MAY also build an **implementation_brief**—a single text block summarizing design steps, constraints, required interfaces, and compilation requirements—and pass it alongside dependency_context to improve coder clarity and compilability.

### 4.7 DTG Graph Constraints

- **Acyclicity**: The graph MUST be acyclic.
- **Node Uniqueness**: Each `id` MUST be unique within the DTG.
- **Edge Validity**: `from` and `to` MUST reference existing DTG node IDs.
- **Connectivity**: Every node SHOULD have at least one dependency or be the root.
- **ID Prefix**: DTG node IDs MUST share the prefix `DTG-{H}-` where `HLIG-{H}` is the parent.

### 4.8 DTG Example

```json
{
  "hlig_node_id": "HLIG-1",
  "nodes": [
    {
      "id": "DTG-1-1",
      "title": "Design Content Serving Architecture",
      "description": "Define the overall web server architecture, including URL routing strategy to filesystem paths, HTTP request parsing, response generation, and core error handling mechanisms.",
      "task_type": "design",
      "inputs_required": [],
      "outputs_produced": ["architecture_spec", "routing_spec"],
      "output_descriptions": {
        "architecture_spec": "Architectural design document",
        "routing_spec": "URL routing specification"
      },
      "dependencies": [],
      "success_criteria": ["Clear, secure, and scalable design is documented."]
    },
    {
      "id": "DTG-1-2",
      "title": "Implement File Reading Module",
      "description": "Develop a module for securely reading file content from the specified filesystem paths.",
      "task_type": "code",
      "inputs_required": ["architecture_spec", "routing_spec"],
      "outputs_produced": ["file_reader_module"],
      "output_descriptions": {
        "file_reader_module": "readFile(path) function returning file content or error"
      },
      "dependencies": ["DTG-1-1"],
      "success_criteria": ["Module correctly reads file content.", "Returns error for non-existent files."],
      "execution_spec": {
        "type": "code",
        "framework": "node-react",
        "format": "markdown",
        "constraints": ["output_only_content"]
      }
    }
  ],
  "edges": [
    {
      "from": "DTG-1-1",
      "to": "DTG-1-2",
      "edge_type": "control",
      "dependency_type": "strict",
      "description": "Implementation requires design specification.",
      "data_spec": {
        "output_ref": "architecture_spec",
        "input_ref": "architecture_spec"
      }
    }
  ]
}
```

---

## 5. Combined Graph Schema

When HLIG and DTG are persisted together, the structure is:

```json
{
  "interfaces": { ... },
  "nodes": [
    {
      "id": "HLIG-1",
      "task": "...",
      "inputs": [...],
      "outputs": [...],
      "language": "...",
      "external_interfaces": [...],
      "dtg_root": "DTG-1",
      "dtg": {
        "hlig_node_id": "HLIG-1",
        "nodes": [...],
        "edges": [...]
      }
    }
  ],
  "edges": [
    { "from": "HLIG-X", "to": "HLIG-Y", "interface_type": "API" }
  ]
}
```

- The optional `interfaces` registry holds reusable interface definitions for edges.
- Top-level `nodes` and `edges` form the HLIG.
- Each HLIG node may contain an embedded `dtg` object.
- Top-level `edges` are HLIG edges only; DTG edges appear only inside `dtg.edges`.

---

## 6. Data Types Reference

| Type | JSON Representation | Notes |
|------|---------------------|-------|
| `string` | `"..."` | UTF-8; no leading/trailing whitespace required |
| `array` | `[...]` | Ordered list; elements per field spec |
| `object` | `{...}` | Key-value; keys are property names |
| `identifier` | `string` | Matches ID pattern for context |
| `enum` | `string` | One of specified literal values |

---

## 7. External Dependency Provisioning

Before build and test, implementations SHOULD provision config for `external_interfaces` declared on HLIG nodes. PlainSpeak does this by default:

1. **Collect interfaces** from all HLIG nodes (`DB`, `Auth`, `Storage`, `Filesystem`, `message`, `API`).
2. **Generate `.env` and `.env.test`** with mock/local values (e.g. `DATABASE_URL=sqlite:///./data/app.db`, `AUTH_DISABLED=true`).
3. **Create directories** (`data/`, `storage/`, `uploads/`) for local persistence.
4. **Optional**: Generate `docker-compose.test.yml` for real services (Postgres, Redis, MinIO).

The build sandbox loads these env vars when executing `npm install`, `npm run build`, `cargo build`, etc., so generated code can connect to mock services. Generated code SHOULD use `process.env.DATABASE_URL` (or equivalent) so it works with provisioned config.

## 8. PlainSpeak Implementation Notes

This section describes how PlainSpeak implements the HLIG/DTG language. Other implementations may differ.

### 8.1 Pipelines

- **hlig_full**: Planner → Designer → Design Doc Generator → Design Reviewer → Coder → Code Reviewer → Builder → Testers. Design documents are generated and stored under each HLIG output directory; coders receive **design_spec** in dependency context.
- **hlig_no_design_docs**: Planner → Designer → Coder → Code Reviewer → Builder → Testers. No design document generation; coders receive **dtg_node_ref** and an **implementation_brief** derived from DTG metadata and interfaces. The generator explicitly skips loading from `designs/` when this pipeline is active (`has_design_docs` false).

### 8.2 Deterministic output and validation

- **Design nodes**: LLM output is validated for a top-level `type` field; canonical **design_spec** is expected. On parse failure, a `design_json_validation_error` event is logged and raw output may be used as fallback.
- **Code nodes**: LLM output is validated for a top-level `files` array. On validation failure, a `code_json_validation_error` event is logged and the node produces no files.
- **Compilation**: Generated code is required to compile. After writing files, PlainSpeak runs a local build (`cargo build` or `npm run build`) per HLIG directory. If the build fails, one retry is performed with **compile_errors** (stdout/stderr) passed back to the code generator. Build output is appended to **build.log** in each HLIG output directory. Set `ENABLE_LOCAL_BUILD=0` to disable local build when cargo/npm are unavailable.

### 8.3 Cost limit and observability

- **Cost limit**: Session LLM cost is accumulated; when it exceeds `COST_LIMIT_USD` (default 0.25), a **CostLimitExceeded** exception is raised and the run stops. Before each LLM call, the current session cost is checked and the call is skipped if the limit is already exceeded.
- **LLM input logging**: The input payload sent to each LLM (structured input_data) is logged to the session debug log under a clear **INPUT TO LLM** header for debugging.
- **Graph execution state**: A central **GraphExecutionState** records per-node execution (plan steps and optionally HLIG/DTG nodes): status, started_at, ended_at, inputs, outputs, error. It is attached to the run context and serialized in `ExecutionContext.to_dict()` for inspection and tracing.

### 8.4 Graph viewer

The optional **graph-viewer** (e.g. `tools/graph-viewer.html`) loads HLIG/DTG JSON and visualizes control and data edges. DTG nodes are color-coded by **node_type** (design, coding, evaluation, tool, reasoning). Edges with **edge_type** `data` (or **dependency_type** `data-flow`) are rendered as dashed grey lines; control edges are solid.

### 8.5 Graph spec validation

PlainSpeak provides **core/graph_spec_validator.py** to check that a generated graph conforms to this specification. It validates HLIG (node IDs, required fields, edge validity, acyclicity, optional `external_interfaces` and `edge_type`) and embedded DTGs (node IDs, required fields, `inputs_required` vs `outputs_produced` of dependencies, acyclicity). Run: `python3 -m core.graph_spec_validator <path-to-graph.json>`. Use `--strict` to require HLIG `inputs`/`outputs` to use canonical snake_case names.

## 9. Versioning and Extensibility

- **Version**: Spec version is indicated in the document header. Implementations MAY include a `version` or `spec_version` field in the root object.
- **Extension**: Implementations MAY add fields with a namespace prefix (e.g., `x_custom_field`) or in a reserved `extensions` object. Unknown fields MUST be ignored by strict parsers.
- **Backward Compatibility**: New minor versions add optional fields or enumerated values; they do not remove or change semantics of existing fields.

---

## 10. References

- JSON: [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)
- Directed Acyclic Graph: Standard graph-theoretic definition

---

## Appendix A: Quick Reference

### HLIG Node Fields
```
id, task, inputs, outputs, language, external_interfaces, dtg_root, dtg
```

### HLIG Edge Fields
```
from, to, edge_type, interface_type, causal, interface_spec, interface_ref
```

### DTG Node Fields
```
id, title, description, task_type, node_type, type (optional logical mirror of task_type),
inputs_required, outputs_produced, output_descriptions, dependencies, success_criteria,
files_owned, expansion_strategy, section, execution_spec, parent_hlig, language
```
(Canonical names: `inputs_required` and `outputs_produced` use exact snake_case identifiers; `output_descriptions` maps name → human description.)

### DTG Edge Fields
```
from, to, edge_type, dependency_type, description, data_spec
```

### Enumerations
- **external_interfaces**: API, DB, Filesystem, Auth, message, None
- **interface_type**: API, DB, message, dependency, Filesystem
- **interface_spec.type**: api, database, message, file
- **edge_type** (HLIG/DTG): control, data
- **node_type** (DTG): reasoning, design, coding, evaluation, tool
- **task_type**: design, contract, scaffold, code, review, test, integration, documentation, verification, build
- **dependency_type**: strict, soft, data-flow

---

## Appendix B: JSON Schema (Informative)

The following JSON Schema can be used for machine validation. It is informative and may not cover all extensions.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://plainspeak.dev/schemas/hlig-dtg-1.0.json",
  "title": "HLIG/DTG Language",
  "description": "High-Level Intent Graph and Detailed Task Graph",
  "type": "object",
  "properties": {
    "project": {
      "type": "object",
      "properties": {
        "name": { "type": "string" },
        "description": { "type": "string" }
      }
    },
    "interfaces": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "properties": {
          "type": { "type": "string", "enum": ["api", "database", "message", "file"] },
          "description": { "type": "string" },
          "schema": { "type": "object" },
          "endpoints": { "type": "array" },
          "ref": { "type": "string" }
        }
      }
    },
    "hlig": {
      "type": "object",
      "required": ["nodes", "edges"],
      "properties": {
        "nodes": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["id", "task", "inputs", "outputs"],
            "properties": {
              "id": { "type": "string", "pattern": "^HLIG-[0-9]+$" },
              "task": { "type": "string" },
              "inputs": { "type": "array", "items": { "type": "string" } },
              "outputs": { "type": "array", "items": { "type": "string" } },
              "language": { "type": "string" },
              "external_interfaces": { "type": "array", "items": { "type": "string" } },
              "dtg_root": { "type": "string", "pattern": "^DTG-[0-9]+$" }
            }
          }
        },
        "edges": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["from", "to"],
            "properties": {
              "from": { "type": "string" },
              "to": { "type": "string" },
              "edge_type": { "type": "string", "enum": ["control", "data"] },
              "interface_type": { "type": "string" },
              "interface_spec": {
                "type": "object",
                "properties": {
                  "type": { "type": "string", "enum": ["api", "database", "message", "file"] },
                  "description": { "type": "string" },
                  "schema": { "type": "object" },
                  "endpoints": { "type": "array" },
                  "ref": { "type": "string" }
                }
              },
              "interface_ref": { "type": "string" }
            }
          }
        }
      }
    }
  }
}
```

For DTG validation:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["hlig_node_id", "nodes", "edges"],
  "properties": {
    "hlig_node_id": { "type": "string", "pattern": "^HLIG-[0-9]+$" },
    "nodes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id", "title", "description", "task_type", "inputs_required", "outputs_produced", "dependencies", "success_criteria"],
        "properties": {
          "id": { "type": "string", "pattern": "^DTG-[0-9]+-[0-9]+$" },
          "title": { "type": "string" },
          "description": { "type": "string" },
          "task_type": { "enum": ["design", "contract", "scaffold", "code", "review", "test", "integration", "documentation", "verification", "build"] },
          "node_type": { "type": "string", "enum": ["reasoning", "design", "coding", "evaluation", "tool"] },
          "inputs_required": { "type": "array", "items": { "type": "string" }, "description": "Canonical artifact names; must match outputs_produced of dependency nodes" },
          "outputs_produced": { "type": "array", "items": { "type": "string" }, "description": "Canonical artifact names (snake_case)" },
          "output_descriptions": { "type": "object", "additionalProperties": { "type": "string" }, "description": "Optional name -> human description" },
          "dependencies": { "type": "array", "items": { "type": "string" } },
          "success_criteria": { "type": "array", "items": { "type": "string" } },
          "execution_spec": {
            "type": "object",
            "properties": {
              "type": { "type": "string" },
              "framework": { "type": "string" },
              "format": { "type": "string" },
              "prompts": { "type": "object" },
              "constraints": { "type": "array", "items": { "type": "string" } }
            }
          }
        }
      }
    },
    "edges": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["from", "to"],
        "properties": {
          "from": { "type": "string" },
          "to": { "type": "string" },
          "edge_type": { "type": "string", "enum": ["control", "data"] },
          "dependency_type": { "enum": ["strict", "soft", "data-flow"] },
          "description": { "type": "string" }
        }
      }
    }
  }
}
```
