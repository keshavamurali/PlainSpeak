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

Pinned crate/npm versions and layout rules for the coder live in `agents/config/dependency_matrix.yaml` and are injected into the **implementation brief** at codegen time.
