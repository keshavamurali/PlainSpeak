# Interface Contracts & Data-Flow Specs — Design

This document addresses gaps identified in the graph/session output: missing interface definitions, backend testing coverage, and data-flow contracts.

---

## 1. Current State & Gaps

### 1.1 HLIG Edge: Interface Type Without Definition

**Current:** HLIG edges have `interface_type` (e.g., `"API"`) but no contract. Example:

```json
{
  "from": "HLIG-2",
  "to": "HLIG-1",
  "interface_type": "API",
  "causal": true
}
```

**Gap:** There is no API definition (endpoints, request/response schemas) that Frontend and Backend can both use.

**Spec exists:** `language_readme.md` §3.6.1 defines `interface_spec` and `interface_ref` for HLIG edges, but the Planner prompt does not ask for them.

### 1.2 Backend (HLIG-2) Testing

**Current:** DTG generator prompt asks for "unit tests, integration, validation" but does not require them for backend/data subsystems. Some runs may omit test nodes.

**Gap:** Backend subsystems (data model, API, DB) should always include: unit tests, integration tests, and verification nodes.

### 1.3 DTG Edge: data-flow Without Contract

**Current:** DTG edges with `dependency_type: "data-flow"` only have `description`. Example:

```json
{
  "from": "DTG-1-2",
  "to": "DTG-1-14",
  "dependency_type": "data-flow",
  "description": "Documentation includes initial project setup details."
}
```

**Gap:** There is no schema or contract defining *what* flows from source to target (e.g., which outputs_produced map to which inputs_required).

**Spec:** `language_readme.md` §4.5 does not define a `data_spec` field for DTG edges.

---

## 2. Proposed Solution

### 2.1 HLIG Interface Definitions

**Approach:** Use the existing `interface_spec` / `interface_ref` from the language spec. The Planner will produce them.

**Planner output (edges):**

```json
{
  "from": "HLIG-2",
  "to": "HLIG-1",
  "interface_type": "API",
  "causal": true,
  "interface_spec": {
    "type": "api",
    "description": "Data API for menu, about us, and contact details",
    "endpoints": [
      {
        "method": "GET",
        "path": "/api/menu",
        "response": { "items": "array of {id, name, price, category}" }
      },
      {
        "method": "GET",
        "path": "/api/about",
        "response": { "content": "string" }
      },
      {
        "method": "GET",
        "path": "/api/contact",
        "response": { "address": "string", "phone": "string", "email": "string" }
      }
    ]
  }
}
```

**Shared file:** Extract interface definitions from the graph and write to `outputs_{session_id}/shared/interfaces.json`. Both Frontend (HLIG-1) and Backend (HLIG-2) can read this file during code generation.

**Structure of `shared/interfaces.json`:**

```json
{
  "by_edge": {
    "HLIG-2→HLIG-1": {
      "type": "api",
      "description": "...",
      "endpoints": [...]
    }
  },
  "by_interface_ref": {}
}
```

### 2.2 Backend Testing in DTG

**Approach:** Strengthen the DTG generator prompt so that backend/data subsystems (nodes with `external_interfaces` including `API`, `DB`, etc.) **must** include:

- At least one `task_type: "test"` node (unit tests)
- At least one `task_type: "integration"` or `task_type: "verification"` node
- Edges from implementation nodes to these test nodes

### 2.3 DTG data-flow Contract

**Approach:** Extend the DTG edge schema with an optional `data_spec` when `dependency_type` is `data-flow`:

```json
{
  "from": "DTG-1-2",
  "to": "DTG-1-14",
  "dependency_type": "data-flow",
  "description": "Documentation includes initial project setup details.",
  "data_spec": {
    "output_ref": "Initialized project repository",
    "input_ref": "Project setup documentation",
    "schema": { "structure": "description of data shape" }
  }
}
```

| Field        | Type   | Description                                                |
|-------------|--------|------------------------------------------------------------|
| `output_ref`| string | Reference to `outputs_produced` of source node             |
| `input_ref` | string | Reference to `inputs_required` of target node              |
| `schema`    | object | Optional schema or description of the data flowing          |

---

## 3. Implementation Summary

| Component              | Change                                                                 |
|------------------------|-----------------------------------------------------------------------|
| `prompts/planner.md`   | Add `interface_spec` to edge schema; require it for API/DB/message    |
| `prompts/dtg_generator.md` | Add `data_spec` for data-flow edges; require backend test nodes |
| `language_readme.md`   | Add `data_spec` to DTG edge schema (§4.5)                             |
| `core/dtg_artifact_generator.py` | Write `shared/interfaces.json` from graph edges when present   |
| `prompts/design_doc_generator.md` | Reference interfaces when available                        |
| `prompts/code_generator.md`       | Reference interfaces when available                        |

---

## 4. Where Contracts Are Captured

| Contract Type        | Location                                      | Consumed By                    |
|----------------------|-----------------------------------------------|--------------------------------|
| HLIG API/DB contract | Edge `interface_spec` + `shared/interfaces.json` | Design docs, Coder, Integration tester |
| DTG data-flow        | Edge `data_spec`                              | Design docs, Coder             |
| Interface registry   | Top-level `interfaces` in graph (optional)     | Edges via `interface_ref`      |
