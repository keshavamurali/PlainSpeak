# HLIG/DTG Language Specification

**Version:** 2.0.0  
**Status:** Draft Standard  
**Encoding:** UTF-8, JSON  
**Date:** 2026

---

## 1. Introduction

This document defines the **HLIG (High-Level Intent Graph)** and **DTG (Detailed Task Graph)** language as a **recursive, contract-first graph architecture**.

The previous rigid two-tier structure is replaced with a unified node model:

- **HLIG nodes** are **composite containers** (`kind: "composite"`) that may hold child graphs.
- **DTG nodes** are **atomic executable tasks** (`kind: "atomic"`) that perform one concrete unit of work.
- **Contract nodes** are first-class interface definitions (`kind: "contract"`) that declare the source-of-truth boundary to be followed by implementation nodes.

This recursive model allows unlimited nesting: a composite HLIG can contain further HLIG composites, DTG atomics, and contract nodes at any depth.

The language is designed to be:

- **Deterministic**: Same input yields consistent structure
- **LLM-agnostic**: Consumable by any language model or agent
- **Tool-neutral**: No vendor-specific or model-specific constructs
- **Self-describing**: Each node carries sufficient context for independent execution

**CVP (Causal Visual Programming):** PlainSpeak continues to use causal semantics. Edges may carry `causal: true` to denote direct causation; execution engines may restrict context to causal parents (Markov blanket); artifacts may record causal path for traceability.

---

## 2. Conventions

### 2.1 Terminology

| Term | Definition |
|------|------------|
| **Graph** | A directed acyclic graph (DAG) of nodes and edges |
| **Node** | A vertex with `kind` = `composite`, `atomic`, or `contract` |
| **Composite Node** | An HLIG node that can contain a recursive `child_graph` |
| **Atomic Node** | A DTG node that executes one task and produces artifacts |
| **Contract Node** | A node that declares interface source-of-truth for implementations |
| **Edge** | Directed dependency (control/data/causal) between nodes |
| **Artifact** | Named output registered to a physical URI/path in `artifact_registry` |

### 2.2 Nomenclature Rules

- **Identifiers**: Use hyphenated path-like IDs to reflect hierarchy (see 2.3)
- **Node Kind**: Every node MUST include `kind`
- **Keys**: snake_case for all JSON property names
- **Artifact Names**: canonical snake_case in `inputs_required` and `outputs_produced`
- **Optional vs Required**: Required fields are marked `[required]`; others are optional

### 2.3 ID Conventions (Recursive)

IDs are hierarchical and encode lineage from root to leaf.

| Node Class | Pattern (conceptual) | Example |
|------------|----------------------|---------|
| Root HLIG | `HLIG-{N}` | `HLIG-1` |
| Nested HLIG | `{ancestor}-HLIG-{N}` | `HLIG-1-HLIG-2` |
| DTG (Atomic) | `{ancestor}-DTG-{N}` | `HLIG-1-HLIG-2-DTG-1` |
| Contract | `{ancestor}-CONTRACT-{N}` | `HLIG-1-HLIG-2-CONTRACT-1` |

Where `{ancestor}` is the full parent path and `{N}` is a positive integer unique within the parent `child_graph`.

### 2.4 Node Kind Classification

Every node MUST contain:

```json
{ "kind": "composite" | "atomic" | "contract" }
```

Interpretation:

- `composite` = HLIG container
- `atomic` = DTG executable leaf task
- `contract` = interface contract/source-of-truth boundary

---

## 3. HLIG — High-Level Intent Graph (Composite Container)

### 3.1 Definition

An **HLIG** is a **composite node** (`kind: "composite"`) representing a scoped intent container. It does not require a fixed DTG root. Instead, it can recursively contain child nodes via `child_graph`.

### 3.2 Purpose

- Capture high-level architecture from requirements
- Partition a project into recursively composable intent containers
- Co-locate implementation nodes (DTGs) with contract nodes in the same graph layer
- Enable n-level decomposition without changing schema shape

### 3.3 Top-Level Schema

```json
{
  "project": { ... },
  "artifact_registry": { ... },
  "graph": {
    "nodes": [ ... ],
    "edges": [ ... ]
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | object | No | Project metadata |
| `artifact_registry` | object | Yes | Global artifact name -> physical location registry (see Section 7) |
| `graph` | object | Yes | Root recursive graph |
| `graph.nodes` | array | Yes | Root-level nodes (`composite`, `atomic`, `contract`) |
| `graph.edges` | array | Yes | Root-level edges |

### 3.4 Project Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Project name |
| `description` | string | No | One-paragraph project description |

### 3.5 HLIG Composite Node Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Hierarchical ID, e.g. `HLIG-1-HLIG-2` |
| `kind` | enum | Yes | MUST be `composite` |
| `task` | string | Yes | High-level intent this container owns |
| `inputs_required` | array of string | Yes | Canonical artifact names required by this container |
| `outputs_produced` | array of string | Yes | Canonical artifact names promised by this container |
| `language` | string | No | Preferred implementation language/stack |
| `child_graph` | object | No | Recursive sub-graph with `nodes` and `edges` (replaces `dtg_root`) |

`child_graph` shape:

```json
{
  "nodes": [ ... ],
  "edges": [ ... ]
}
```

`child_graph` MAY contain any node kind (`composite`, `atomic`, `contract`) and MAY recurse indefinitely.

### 3.6 HLIG Edge Schema (Simplified)

Interfaces are no longer stored on edge metadata. Edges only express dependency semantics.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from` | string | Yes | Source node ID |
| `to` | string | Yes | Target node ID |
| `edge_type` | string | No | `control` (default) or `data` |
| `dependency_type` | string | No | `strict`, `soft`, or `data-flow` |
| `causal` | boolean | No | Whether source is a direct cause of target |
| `description` | string | No | Human-readable dependency reason |
| `data_spec` | object | No | Artifact reference map (`output_ref`, `input_ref`) |

### 3.7 Contract Node Schema (First-Class)

Contract nodes replace edge-level interface payloads.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Hierarchical ID, e.g. `HLIG-1-CONTRACT-1` |
| `kind` | enum | Yes | MUST be `contract` |
| `title` | string | Yes | Contract name |
| `contract_type` | string | Yes | e.g. `api`, `database`, `event`, `file`, `auth` |
| `source_of_truth` | object | Yes | Canonical definition location (OpenAPI, schema file, etc.) |
| `inputs_required` | array of string | No | Inputs required to define/update this contract |
| `outputs_produced` | array of string | Yes | Canonical contract artifacts produced |
| `implemented_by` | array of string | No | Node IDs required to adhere to this contract |
| `validation_rules` | array of string | No | Rules used to verify contract compliance |

`source_of_truth` minimum shape:

```json
{
  "uri": "specs/order_api/openapi.yaml",
  "format": "openapi",
  "version": "3.1.0"
}
```

### 3.8 HLIG Graph Constraints

- **Acyclicity**: Each graph scope MUST be acyclic.
- **Node Uniqueness**: Each `id` MUST be globally unique.
- **Edge Validity**: `from` and `to` MUST reference valid node IDs in the same graph scope.
- **Kind Validity**: Every node MUST declare `kind`.
- **Recursive Validity**: If node kind is `composite`, `child_graph` MAY exist and must follow the same constraints recursively.
- **Input Lineage**: Every `inputs_required` artifact SHOULD be produced by an upstream node in the same lineage, or explicitly pre-seeded in `artifact_registry`.

### 3.9 HLIG Composite Example

```json
{
  "id": "HLIG-1",
  "kind": "composite",
  "task": "Deliver coffee shop platform",
  "inputs_required": [],
  "outputs_produced": ["system_release_bundle"],
  "child_graph": {
    "nodes": [
      {
        "id": "HLIG-1-DTG-1",
        "kind": "atomic",
        "title": "Create design system specification",
        "description": "Produce the canonical design system tokens/components spec used by frontend work.",
        "task_type": "design",
        "implementation": {
          "language": "markdown",
          "framework": "n/a",
          "runtime": "n/a",
          "target": "docs/design_system_spec.md"
        },
        "inputs_required": [],
        "outputs_produced": ["design_system_spec"],
        "dependencies": [],
        "success_criteria": ["Design system spec is complete and versioned"]
      },
      {
        "id": "HLIG-1-HLIG-1",
        "kind": "composite",
        "task": "Frontend subsystem",
        "inputs_required": ["design_system_spec"],
        "outputs_produced": ["frontend_build"],
        "child_graph": { "nodes": [], "edges": [] }
      },
      {
        "id": "HLIG-1-CONTRACT-1",
        "kind": "contract",
        "title": "Order API Contract",
        "contract_type": "api",
        "source_of_truth": {
          "uri": "contracts/order_api/openapi.yaml",
          "format": "openapi",
          "version": "3.1.0"
        },
        "outputs_produced": ["order_api_contract"]
      }
    ],
    "edges": [
      {
        "from": "HLIG-1-DTG-1",
        "to": "HLIG-1-HLIG-1",
        "edge_type": "data",
        "dependency_type": "data-flow",
        "causal": true,
        "data_spec": {
          "output_ref": "design_system_spec",
          "input_ref": "design_system_spec"
        }
      },
      {
        "from": "HLIG-1-CONTRACT-1",
        "to": "HLIG-1-HLIG-1",
        "edge_type": "data",
        "dependency_type": "data-flow",
        "causal": true
      }
    ]
  }
}
```

---

## 4. DTG — Detailed Task Graph (Atomic Execution Node)

### 4.1 Definition

A **DTG** is represented as an **atomic node** (`kind: "atomic"`). It is a leaf-level executable task with deterministic inputs, outputs, dependencies, and success criteria.

In the recursive architecture, DTG nodes do not require a separate top-level container. They live directly inside a `child_graph` of an HLIG (or nested HLIG).

### 4.2 Purpose

- Execute one concrete implementation, validation, or build step
- Consume canonical artifact names from the registry
- Produce canonical artifact names and register physical locations
- Serve as the unit of retry for self-healing execution

### 4.3 DTG Atomic Node Schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Hierarchical DTG ID (e.g. `HLIG-1-HLIG-2-DTG-1`) |
| `kind` | enum | Yes | MUST be `atomic` |
| `title` | string | Yes | Short deterministic task name |
| `description` | string | Yes | Detailed executable instructions |
| `task_type` | string | Yes | `design`, `contract`, `scaffold`, `code`, `test`, `verification`, `build`, etc. |
| `implementation` | object | No | Implementation stack/profile for this node (language, framework, runtime, target). RECOMMENDED for `code`, `build`, `test`, and `verification` nodes. |
| `inputs_required` | array of string | Yes | Canonical artifact names required from `artifact_registry` |
| `outputs_produced` | array of string | Yes | Canonical artifact names produced and registered |
| `dependencies` | array of string | Yes | IDs of prerequisite nodes |
| `success_criteria` | array of string | Yes | Objective acceptance checks |
| `execution_spec` | object | No | Optional execution instructions |
| `on_failure` | object | No | Optional retry policy override (see 8.2) |

Recommended `implementation` shape:

```json
{
  "implementation": {
    "language": "typescript",
    "framework": "react",
    "runtime": "node20",
    "build_tool": "vite",
    "package_manager": "npm",
    "target": "apps/frontend"
  }
}
```

### 4.4 DTG Edge Semantics

DTG nodes use the same edge schema as Section 3.6.

For deterministic artifact flow:

- `inputs_required` MUST reference names that exist in global `artifact_registry`.
- `outputs_produced` MUST be registered in `artifact_registry` after successful execution.
- If `dependency_type` is `data-flow`, `data_spec.output_ref` and `data_spec.input_ref` MUST match canonical artifact names.

### 4.5 DTG Atomic Example

```json
{
  "id": "HLIG-1-HLIG-2-DTG-1",
  "kind": "atomic",
  "title": "Implement order API handlers",
  "description": "Generate handler code conforming to Order API contract.",
  "task_type": "code",
  "implementation": {
    "language": "typescript",
    "framework": "express",
    "runtime": "node20",
    "build_tool": "tsc",
    "package_manager": "npm",
    "target": "services/order-api"
  },
  "inputs_required": ["order_api_contract", "backend_architecture_spec"],
  "outputs_produced": ["order_api_handlers"],
  "dependencies": ["HLIG-1-HLIG-2-CONTRACT-1", "HLIG-1-HLIG-2-DTG-0"],
  "success_criteria": ["Handlers compile", "All contract routes implemented"]
}
```

### 4.6 Test Nodes (Atomic) and Execution Semantics

Test nodes are normal atomic nodes with `task_type: "test"` (or `verification` for non-test checks).

Recommended test-specific fields inside the node:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `test_scope` | string | No | `unit`, `integration`, `e2e`, `contract`, `performance` |
| `target_node_ids` | array of string | No | IDs of implementation nodes being validated |
| `failure_log_artifact` | string | No | Canonical artifact name used when tests fail (e.g., `test_failure_log`) |

Behavior:

1. Test node consumes implementation artifacts (e.g., `order_api_handlers`).
2. On success, it produces test report artifacts (e.g., `unit_test_report`).
3. On failure, it registers `failure_log_artifact` and triggers `on_failure` retry flow against causal parent implementation node.

Example (code node + test node):

```json
{
  "nodes": [
    {
      "id": "HLIG-1-HLIG-2-DTG-2",
      "kind": "atomic",
      "title": "Implement order API handlers",
      "task_type": "code",
      "implementation": {
        "language": "typescript",
        "framework": "express",
        "runtime": "node20",
        "build_tool": "tsc",
        "package_manager": "npm",
        "target": "services/order-api"
      },
      "inputs_required": ["order_api_contract"],
      "outputs_produced": ["order_api_handlers"],
      "dependencies": ["HLIG-1-HLIG-2-CONTRACT-1"],
      "success_criteria": ["Code compiles", "Routes implemented"]
    },
    {
      "id": "HLIG-1-HLIG-2-DTG-3",
      "kind": "atomic",
      "title": "Run order API unit tests",
      "task_type": "test",
      "implementation": {
        "language": "typescript",
        "framework": "jest",
        "runtime": "node20",
        "package_manager": "npm",
        "target": "services/order-api"
      },
      "test_scope": "unit",
      "target_node_ids": ["HLIG-1-HLIG-2-DTG-2"],
      "inputs_required": ["order_api_handlers"],
      "outputs_produced": ["unit_test_report"],
      "failure_log_artifact": "test_failure_log",
      "dependencies": ["HLIG-1-HLIG-2-DTG-2"],
      "success_criteria": ["All unit tests pass", "Coverage >= 80%"],
      "on_failure": {
        "strategy": "retry_causal_parent",
        "max_retries": 2,
        "inject_error_input_as": "test_failure_log",
        "target_task_types": ["test", "verification"]
      }
    }
  ],
  "edges": [
    {
      "from": "HLIG-1-HLIG-2-DTG-2",
      "to": "HLIG-1-HLIG-2-DTG-3",
      "edge_type": "data",
      "dependency_type": "data-flow",
      "causal": true,
      "data_spec": {
        "output_ref": "order_api_handlers",
        "input_ref": "order_api_handlers"
      }
    }
  ]
}
```

---

## 5. Recursive Combined Graph Schema

When persisted, HLIG and DTG share one recursive graph model:

```json
{
  "project": { ... },
  "artifact_registry": { ... },
  "graph": {
    "nodes": [
      {
        "id": "HLIG-1",
        "kind": "composite",
        "task": "...",
        "inputs_required": [...],
        "outputs_produced": [...],
        "child_graph": {
          "nodes": [
            { "id": "HLIG-1-HLIG-1", "kind": "composite", ... },
            { "id": "HLIG-1-CONTRACT-1", "kind": "contract", ... },
            { "id": "HLIG-1-DTG-1", "kind": "atomic", ... }
          ],
          "edges": [ ... ]
        }
      }
    ],
    "edges": [ ... ]
  }
}
```

Notes:

- No `dtg_root` field is used in v2.
- `child_graph` is recursive and optional on any `composite` node.
- Contracts are represented as `kind: "contract"` nodes, not edge metadata.
- DTGs remain DTGs conceptually, but are encoded as `kind: "atomic"` nodes.

---

## 6. Data Types Reference

| Type | JSON Representation | Notes |
|------|---------------------|-------|
| `string` | `"..."` | UTF-8 |
| `array` | `[...]` | Ordered list |
| `object` | `{...}` | Key-value map |
| `identifier` | `string` | Must follow recursive ID pattern |
| `enum` | `string` | One of documented literal values |

---

## 7. Artifact Registry

The `artifact_registry` is the global source for resolving canonical artifact names to physical locations. Nodes must not assume where files were generated.

### 7.1 Registry Shape

```json
{
  "artifact_registry": {
    "entries": {
      "order_api_contract": {
        "uri": "contracts/order_api/openapi.yaml",
        "producer_node_id": "HLIG-1-CONTRACT-1",
        "media_type": "application/yaml",
        "checksum": "sha256:...",
        "created_at": "2026-05-11T09:30:00Z"
      }
    }
  }
}
```

### 7.2 Rules

- Every `outputs_produced` artifact name MUST be canonical snake_case.
- On success, producing nodes MUST create or update registry entries for each output.
- Consuming nodes MUST resolve each `inputs_required` name through `artifact_registry`.
- `uri` MAY be local path (`./runs/123/...`) or remote URI (`s3://...`) depending on runtime.
- Multiple versions MAY exist; implementations SHOULD define deterministic resolution (latest-successful, pinned-run, or explicit version).

### 7.3 Why This Exists

This decouples task dependency from physical file layout. Nodes reference logical names, not hardcoded paths.

---

## 8. PlainSpeak Implementation Notes

This section describes how PlainSpeak implements HLIG/DTG recursion. Other implementations may differ.

### 8.1 Pipelines

- Pipelines may still include planner/designer/coder/tester roles.
- The planner emits recursive composite trees instead of fixed two-tier HLIG->DTG mappings.
- Contracts are emitted as first-class nodes and linked via edges to dependent implementation nodes.

### 8.2 Deterministic Output, Validation, and Self-Healing

- **Design/Code output validation**: Outputs SHOULD be schema-validated per `task_type`.
- **Artifact registration**: Successful nodes MUST register `outputs_produced` in `artifact_registry`.
- **Self-healing loop (`on_failure`)**:
  1. Detect failure at verification/test node (`task_type: test` or `verification`).
  2. Identify causal parent implementation node (nearest upstream `kind: "atomic"` code/build node with `causal: true` path).
  3. Capture error log artifact (e.g. `compile_error_log`, `test_failure_log`) and register it.
  4. Retry causal parent with injected input artifact name in `inputs_required`.
  5. Re-run failed verification/test node after parent succeeds or retry budget is exhausted.

Recommended `on_failure` shape on atomic nodes:

```json
{
  "on_failure": {
    "strategy": "retry_causal_parent",
    "max_retries": 2,
    "inject_error_input_as": "test_failure_log",
    "target_task_types": ["test", "verification"]
  }
}
```

### 8.3 Cost Limit and Observability

- Session cost limits and call gating remain implementation-defined.
- Execution state SHOULD record node inputs, outputs, artifact URIs, retries, and failure lineage.
- Logs SHOULD include causal retry decisions for auditability.

### 8.4 Graph Viewer

- Viewers SHOULD color nodes by `kind` (`composite`, `atomic`, `contract`).
- Viewers SHOULD render recursive `child_graph` boundaries and allow drill-down.
- Data edges and control edges SHOULD remain visually distinct.

### 8.5 Graph Spec Validation

Validators SHOULD enforce:

- recursive graph shape for every `child_graph`
- `kind` presence and validity
- absence of deprecated `dtg_root`
- canonical artifact naming
- `inputs_required` resolvability through `artifact_registry`
- contract node `source_of_truth` completeness
- acyclicity per graph scope

---

## 9. Versioning and Extensibility

- **Version**: Spec version is in the document header (`2.0.0`).
- **Extension**: Implementations MAY add namespaced fields (e.g., `x_custom_field`) or `extensions`.
- **Backward Compatibility**: v2 introduces structural change (`child_graph`, `kind`, contract nodes, artifact registry). v1 compatibility may require migration tooling.

---

## 10. Migration Checklist (v1 -> v2)

Use this checklist when converting legacy two-tier HLIG/DTG graphs to the recursive contract-first model.

| v1 Concept/Field | v2 Replacement | Required Migration Action |
|------------------|----------------|---------------------------|
| Top-level `hlig` object | Top-level `graph` object | Move `hlig.nodes` -> `graph.nodes` and `hlig.edges` -> `graph.edges`. |
| `interfaces` registry and edge-level interface payloads | First-class `kind: "contract"` nodes | Create contract nodes for each interface boundary and link with edges. |
| HLIG as subsystem-only node | HLIG as `kind: "composite"` container | Add `kind: "composite"` to HLIG nodes and treat them as recursive containers. |
| DTG as separate embedded structure | DTG as `kind: "atomic"` leaf node | Flatten DTG nodes into containing `child_graph.nodes` with `kind: "atomic"`. |
| `dtg_root` | `child_graph` | Remove `dtg_root`; attach descendant nodes under `child_graph`. |
| `dtg` embedded object on HLIG | Recursive `child_graph` | Convert `dtg.nodes/edges` into `child_graph.nodes/edges`. |
| Edge `interface_type` | Contract node + plain dependency edge | Replace semantic interface type on edge with node-level contract definition. |
| Edge `interface_spec` | Contract node `source_of_truth` | Move inline schema/spec into contract node `source_of_truth`. |
| Edge `interface_ref` | Edge to contract node ID | Replace registry reference with explicit dependency on contract node. |
| Mixed HLIG/DTG ID patterns (`HLIG-1`, `DTG-1-2`) | Hierarchical IDs (`HLIG-1-HLIG-2-DTG-1`) | Re-key all nodes and update edge/dependency references accordingly. |
| Optional node categories (`node_type`) | Required node `kind` | Add `kind` to every node (`composite`, `atomic`, or `contract`). |
| Artifact handoff implicit in dependencies | Global `artifact_registry` lookup | Resolve all `inputs_required` through registry entries by canonical artifact name. |
| `outputs_produced` without location binding | `artifact_registry.entries.<name>.uri` | Register each produced artifact to path/URI after node success. |
| Failure handling as ad-hoc retries | `on_failure` self-healing contract | Add retry policy that targets causal parent and injects failure log artifact. |

### 10.1 Step-by-Step Migration Flow

1. Rename root shape: `hlig` -> `graph`; add `artifact_registry`.
2. Add `kind` to all nodes:
   - HLIG -> `composite`
   - DTG -> `atomic`
   - Interface definitions -> `contract`
3. Replace each HLIG `dtg`/`dtg_root` with recursive `child_graph`.
4. Convert interface metadata on edges into contract nodes with `source_of_truth`.
5. Rebuild IDs using hierarchical lineage and rewrite all references.
6. Enforce canonical snake_case artifact names in `inputs_required` and `outputs_produced`.
7. Register outputs into `artifact_registry.entries` with `uri` and `producer_node_id`.
8. Add `on_failure` policies for `test`/`verification` atomics.
9. Validate acyclicity and reference integrity at each graph scope.

### 10.2 Deprecated in v2

- `dtg_root`
- Edge `interface_type`, `interface_spec`, and `interface_ref` as the primary interface mechanism
- Any schema that omits node `kind`

---

## 11. References

- JSON: [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259)
- Directed Acyclic Graph: Standard graph-theoretic definition

---

## Appendix A: Quick Reference

### Node Kinds
```
composite (HLIG), atomic (DTG), contract (interface/source-of-truth)
```

### Composite (HLIG) Fields
```
id, kind, task, inputs_required, outputs_produced, language, child_graph
```

### Atomic (DTG) Fields
```
id, kind, title, description, task_type, inputs_required, outputs_produced,
implementation, test_scope, target_node_ids, failure_log_artifact,
dependencies, success_criteria, execution_spec, on_failure
```

### Contract Fields
```
id, kind, title, contract_type, source_of_truth, inputs_required, outputs_produced,
implemented_by, validation_rules
```

### Edge Fields
```
from, to, edge_type, dependency_type, causal, description, data_spec
```

### Artifact Registry Fields
```
artifact_registry.entries.<artifact_name>.uri
artifact_registry.entries.<artifact_name>.producer_node_id
artifact_registry.entries.<artifact_name>.media_type
artifact_registry.entries.<artifact_name>.checksum
artifact_registry.entries.<artifact_name>.created_at
```

### Enumerations
- **kind**: composite, atomic, contract
- **edge_type**: control, data
- **dependency_type**: strict, soft, data-flow
- **task_type**: design, contract, scaffold, code, review, test, integration, documentation, verification, build

---

## Appendix B: JSON Schema (Informative, Abbreviated)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["artifact_registry", "graph"],
  "properties": {
    "artifact_registry": { "type": "object" },
    "graph": {
      "type": "object",
      "required": ["nodes", "edges"],
      "properties": {
        "nodes": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["id", "kind"],
            "properties": {
              "id": { "type": "string" },
              "kind": { "type": "string", "enum": ["composite", "atomic", "contract"] },
              "child_graph": { "$ref": "#/$defs/graph" }
            }
          }
        },
        "edges": { "type": "array" }
      }
    }
  },
  "$defs": {
    "graph": {
      "type": "object",
      "required": ["nodes", "edges"],
      "properties": {
        "nodes": { "type": "array" },
        "edges": { "type": "array" }
      }
    }
  }
}
```
