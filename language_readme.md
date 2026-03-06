# HLIG/DTG Language Specification

**Version:** 1.0.0  
**Status:** Draft Standard  
**Encoding:** UTF-8, JSON  
**Date:** 2025

---

## 1. Introduction

This document defines the **HLIG (High-Level Intent Graph)** and **DTG (Detailed Task Graph)** language—a structured representation for decomposing software project intent into executable task graphs. The language is designed to be:

- **Deterministic**: Same input yields consistent structure
- **LLM-agnostic**: Consumable by any language model or agent
- **Tool-neutral**: No vendor-specific or model-specific constructs
- **Self-describing**: Each node carries sufficient context for independent execution

The specification is suitable for publication as a formal standard and for implementation by third-party systems.

**CVP (Causal Visual Programming):** PlainSpeak integrates causal semantics from CVP to reduce LLM hallucinations and improve robustness. Edges may carry `causal: true` to denote direct causation; agents receive context restricted to causal parents (Markov blanket); and artifacts record their causal path for traceability.

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
| `inputs` | array of string | Yes | List of inputs to this subsystem |
| `outputs` | array of string | Yes | List of outputs from this subsystem |
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
| `inputs_required` | array of string | Yes | List of required inputs (references or literal descriptions) |
| `outputs_produced` | array of string | Yes | Concrete artifacts produced |
| `dependencies` | array of string | Yes | IDs of DTG nodes that must complete before this one |
| `success_criteria` | array of string | Yes | Objective, measurable acceptance criteria |
| `execution_spec` | object | No | Execution instructions for LLM/agent (see 4.4.1) |
| `parent_hlig` | object | No | Runtime enrichment: parent HLIG context (see 4.6) |
| `language` | string | No | Runtime enrichment: preferred language from parent |

**Task Types (`task_type`):**

| Value | Meaning |
|-------|---------|
| `design` | Architectural or design documentation |
| `code` | Implementation of code module(s) |
| `test` | Unit or integration tests |
| `integration` | Wiring components together |
| `documentation` | User or developer documentation |
| `verification` | Validation or quality checks |
| `build` | Build, packaging, or deployment configuration |
| `review` | Code or design review |

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

### 4.5 DTG Edge Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from` | string | Yes | Source node ID (alias: `source`) |
| `to` | string | Yes | Target node ID (alias: `target`) |
| `dependency_type` | string | No | Semantics of the dependency |
| `description` | string | No | Human-readable reason for the dependency |
| `data_spec` | object | No | For `data-flow`: contract defining what flows (see 4.5.1) |

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
      "inputs_required": ["HLIG-1 task description", "External interface: Filesystem"],
      "outputs_produced": ["Architectural design document", "URL routing specification"],
      "dependencies": [],
      "success_criteria": ["Clear, secure, and scalable design is documented."]
    },
    {
      "id": "DTG-1-2",
      "title": "Implement File Reading Module",
      "description": "Develop a module for securely reading file content from the specified filesystem paths.",
      "task_type": "code",
      "inputs_required": ["Architectural design from DTG-1-1"],
      "outputs_produced": ["readFile(path) function returning file content or error"],
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
      "dependency_type": "strict",
      "description": "Implementation requires design specification."
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

## 8. Versioning and Extensibility

- **Version**: Spec version is indicated in the document header. Implementations MAY include a `version` or `spec_version` field in the root object.
- **Extension**: Implementations MAY add fields with a namespace prefix (e.g., `x_custom_field`) or in a reserved `extensions` object. Unknown fields MUST be ignored by strict parsers.
- **Backward Compatibility**: New minor versions add optional fields or enumerated values; they do not remove or change semantics of existing fields.

---

## 9. References

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
from, to, interface_type, interface_spec, interface_ref
```

### DTG Node Fields
```
id, title, description, task_type, inputs_required, outputs_produced,
dependencies, success_criteria, execution_spec, parent_hlig, language
```

### DTG Edge Fields
```
from, to, dependency_type, description
```

### Enumerations
- **external_interfaces**: API, DB, Filesystem, Auth, message, None
- **interface_type**: API, DB, message, dependency, Filesystem
- **interface_spec.type**: api, database, message, file
- **task_type**: design, code, test, integration, documentation, verification, build, review
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
          "task_type": { "enum": ["design", "code", "test", "integration", "documentation", "verification", "build", "review"] },
          "inputs_required": { "type": "array", "items": { "type": "string" } },
          "outputs_produced": { "type": "array", "items": { "type": "string" } },
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
          "dependency_type": { "enum": ["strict", "soft", "data-flow"] },
          "description": { "type": "string" }
        }
      }
    }
  }
}
```
