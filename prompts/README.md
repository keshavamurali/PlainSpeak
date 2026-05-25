# Prompts

Add agent prompts here as `.md` files. Each file corresponds to an agent type (e.g. `planner.md`, `code_generator.md`, `reviewer.md`). The pipeline `coder` step uses `code_generator.md` (loaded by `DTGArtifactGenerator` for atomic DTG codegen within the recursive graph model).

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
| `ABORT_ON_LOCAL_BUILD_FAILURE` | `1` | If `1` (default), stop the HLIG pipeline when local `cargo`/`npm` build still fails after retries (`LocalBuildFailedError`). Set `0` / `false` / `no` to **continue** with remaining DTG nodes. |
| `BUILD_RETRY_FILES_MAX_TOTAL_CHARS` | `100000` | Max combined size of `previous_attempt_files` sent to the LLM on retry (full sources + manifests). Use `0` for no limit. |

## Mechanical pre-build checks (per-file, before IG reviewer / disk write)

Rust `rustfmt` runs on a temporary `.rs` file. It must use the same **edition** as the HLIG `Cargo.toml` or it will falsely reject `async fn` and similar syntax.

| Variable | Default | Effect |
|----------|---------|--------|
| `ENABLE_MECHANICAL_VALIDATE` | `1` | If `0`, skip mechanical validation (not recommended). |
| `MECHANICAL_RUST_EDITION` | *(unset)* | Force rustfmt edition (e.g. `2021`). If unset, edition is read from `Cargo.toml` under the HLIG dir, else `2021`. |
| `MECHANICAL_RUSTFMT_AUTOFORMAT` | `1` | If `1`, when `rustfmt --check` fails, run rustfmt in place and use the formatted source so trivial layout issues do not trigger an LLM retry. |
| `MECHANICAL_PRETTIER` | `1` | For `.ts` / `.tsx` / `.jsx` (and optionally `.js` after `node --check`), run Prettier when the HLIG has Prettier in `package.json` or a Prettier config file. |
| `MECHANICAL_PRETTIER_AUTOFORMAT` | `1` | If `1`, run `prettier --write` on the temp file when `--check` fails (same idea as rustfmt). |
| `MECHANICAL_PRETTIER_JS` | `1` | If `1`, run Prettier on `.js`/`.mjs`/`.cjs` after a successful `node --check`. |
| `MECHANICAL_PRETTIER_ALLOW_NPX` | `1` | If `1`, use `npx --yes prettier` when `node_modules/prettier` is missing (needed before the first `npm install` in that HLIG). Set `0` for offline-only use after `npm install`. |
| `ENABLE_RUST_WORKSPACE_COHERENCE` | `1` | Before `cargo` on Rust HLIGs, scan `src/main.rs` + `src/lib.rs` for `use crate::…` vs files under `src/`, and for `log::` / `env_logger::` / `anyhow::` / `clap::` vs `Cargo.toml`. On mismatch, skip the build and retry codegen with a coherence error (set `0` to disable). |
| `IG_INCREMENTAL_CARGO_CHECK` | `0` | If `1`, run `cargo check` after each Rust file lands (heavy). |
| `ENABLE_TSC_MECHANICAL` / `ENABLE_CLIPPY_MECHANICAL` | `0` | Optional project-level checks from `extra_toolchain_validate`. |

New Rust HLIG scaffolds that create `Cargo.toml` also create `rust-toolchain.toml` with `channel = "stable"` for a consistent local toolchain.

New Node HLIG scaffolds that create `package.json` add `prettier` in `devDependencies` and a minimal `.prettierrc.json` so React/Vite-style trees can use the same mechanical formatting path as Rust.

Pinned crate/npm versions and layout rules for the coder live in `agents/config/dependency_matrix.yaml` and are injected into the **implementation brief** at codegen time.

On build failure, the coder receives **`compile_errors`** (stdout/stderr + hints) and **`previous_attempt_files`** (full file contents from disk for the last generated files, capped by `BUILD_RETRY_FILES_MAX_TOTAL_CHARS`) so the model can apply targeted fixes.
