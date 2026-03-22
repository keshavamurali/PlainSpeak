# Prompts

Add agent prompts here as `.md` files. Each file corresponds to an agent type (e.g. `planner.md`, `code_generator.md`, `reviewer.md`). The pipeline `coder` step uses `code_generator.md` (loaded by `DTGArtifactGenerator` for DTG codegen).

Reference these in `agents/config/agents.yaml` via the `prompt_file` config key.

## Local HLIG build / lint (code generation)

`DTGArtifactGenerator._run_local_build` may run extra checks after `cargo check` / before final build:

| Variable | Default | Effect |
|----------|---------|--------|
| `ENABLE_LOCAL_CLIPPY` | `1` | Run `cargo clippy --all-targets` (Rust). |
| `CLIPPY_DENY_WARNINGS` | `0` | If `1`, append `-- -D warnings` to clippy. |
| `ENABLE_LOCAL_TSC` | `1` | Run `npm exec -- tsc --noEmit` when `tsconfig.json` exists, `typescript` is in `package.json`, and `src/` has checkable sources. |
| `ENABLE_LOCAL_ESLINT` | `1` | Run `npm exec -- eslint .` when `eslint` is in `package.json` **and** a config exists (`eslint.config.*` or `.eslintrc.*` / `eslintConfig`). |
| `ESLINT_MAX_WARNINGS_ZERO` | `0` | If `1`, pass `--max-warnings 0` to eslint. |
| `PER_NODE_BUILD_RETRIES` | `2` | Extra codegen attempts after a failed local build (default **3** tries total per DTG code node). |
| `BUILD_RETRY_FILES_MAX_TOTAL_CHARS` | `100000` | Max combined size of `previous_attempt_files` sent to the LLM on retry (full sources + manifests). Use `0` for no limit. |

Pinned crate/npm versions and layout rules for the coder live in `agents/config/dependency_matrix.yaml` and are injected into the **implementation brief** at codegen time.

On build failure, the coder receives **`compile_errors`** (stdout/stderr + hints) and **`previous_attempt_files`** (full file contents from disk for the last generated files, capped by `BUILD_RETRY_FILES_MAX_TOTAL_CHARS`) so the model can apply targeted fixes.
