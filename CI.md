# CI Readiness — Mandatory Push/PR Harness

This file is the **single source of truth** for local CI readiness before any push or PR.

## 0) Docs / process-only shortcut

If your diff is **only** documentation or contributor-process files, you may
skip the code-quality and test commands below.

Examples of files that qualify:

- `AGENTS.md`
- `CI.md`
- `CONTRIBUTING.md`
- `README.md`
- `docs/**/*.md`
- `docs/**/*.mdx`
- `docs/docs.json`

You may use the shortcut only when **all** changed files are non-runtime and
non-executable. If the diff touches application code, tests, build tooling,
dependency manifests, CI workflows, scripts, or anything with runtime impact,
run the normal harness.

For docs/process-only changes, the minimum required local check is:

```bash
git status --short
```

If you are unsure whether the shortcut applies, do **not** use it — run the
standard checks below.

## 1) Mandatory baseline checks (every code change that is not docs/process-only)

Run all of these first:

1. Clean working tree

   ```bash
   git status --short
   ```

   - No accidental untracked files
   - Never commit `.env` or secrets

2. Lint

   ```bash
   make lint
   ```

3. Format check

   ```bash
   make format-check
   ```

   If it fails:

   ```bash
   make format && make format-check
   ```

4. Typecheck

   ```bash
   make typecheck
   ```

## 2) Mandatory test harness (scope by touched modules)

Pick a focused test command for the modules you changed — do **not** default to
the full unit suite.

Map changed paths to targets using the `PathRule` entries in
[`.github/ci/test_scope_rules.py`](.github/ci/test_scope_rules.py):

- Rules with `always_escalate=True` map to `make test-cov`
- All other rules list a `test_targets` tuple — run those with
  `uv run python -m pytest <targets>`
- Changed files under `tests/` with no app rule run as-is

Use a focused `-k` filter when you only need a subset of a package.

## 3) Escalation rules (must run full unit CI suite)

Run `make test-cov` (instead of only targeted tests) when any of these are true:

- Shared/core code changed (`core/state/`, `core/domain/types/`, `tools/investigation/`, `tools/investigation/stages/`)
- 3+ app areas changed in one diff
- New files with unclear blast radius
- Cross-cutting refactor
- You are unsure test scope is sufficient

```bash
make test-cov
```

## 4) Conditional checks

CI runs the fast registry smoke gate on every code change:

```bash
make verify-integrations-smoke
```

If integration config, integration wiring, or related tools changed, also run the live check against your local store and environment:

```bash
make verify-integrations
```

If Fargate CDK code, its deployment commands, or infrastructure tests changed,
also run:

## 5) Optional extra confidence

You may run `make check` as a final pass, but it is heavier (`test-full`) than the required harness.

## 6) Interactive-shell turn tests

Interactive-shell live turn tests always run with live coverage enabled. Do not use deselection filters like `-k "not live_llm"`. Fix failures by improving planner/tool correctness or updating fixtures only when behavior changes are explicitly approved.

For fast **local** iteration only, you can narrow the live suite with `--turn-select` (or the `TURN_SELECT` env var) without disabling live coverage:

- `--turn-select=complex:N` runs the N most complex scenarios (multi-step plans, `runs > 1`, gather contracts, and `@live` integrations score highest).
- `--turn-select=sample:N` runs a random N; add `--turn-select-seed` (or `TURN_SELECT_SEED`) for reproducibility.
- `N` may be a count (`5`), a fraction (`0.1`), or a percentage (`10%`); a bare `complex`/`sample` defaults to 5%.

```bash
# Most complex five scenarios
uv run python -m pytest tests/core/agent/test_turn_scenarios.py --turn-select=complex:5
# Random ~5% sample, reproducible
TURN_SELECT=sample:5% TURN_SELECT_SEED=7 uv run python -m pytest tests/core/agent/test_turn_scenarios.py
```

This is an iteration aid, not a substitute for full coverage: leave it unset for the pre-push/PR validation run, and never set it in CI (the sharded `turn-live` job runs every scenario).

In CI, [`.github/workflows/interactive-shell-live.yml`](.github/workflows/interactive-shell-live.yml) runs two jobs on same-repo PRs and post-merge `main` pushes: a no-LLM `turn-checks` gate (deterministic command detection + fixture integrity, `-m "not live_llm"`) and the sharded `turn-live` job (8 shards, live coverage). The no-LLM gate is a fast guardrail, not a substitute for live coverage.

`@live` gather scenarios **fail** (not skip) in GitHub Actions when integration credentials are missing; locally they may still skip. Natural-language investigation dispatch is **enabled** by default (`INTERACTIVE_SHELL_INVESTIGATION_ENABLED = True`). Investigation dispatch scenarios run in `turn-live`; if the flag is set to `False` for emergency rollback, those scenarios **skip** in live shards and `turn-checks` stays green. Require all `turn-checks` and `turn-live shard *` checks on `main` branch protection.

## 7) CI-only tests

Some paths require live infrastructure and are excluded from `make test-cov`:

- Kubernetes / EKS scenarios (`tests/e2e/`)
- Chaos Mesh workflows (`tests/chaos_engineering/`)
- Docker-dependent Grafana stack tests

Mark CI-only tests with the appropriate pytest marker or place them in the correct folder so they do not run locally by default.

## 8) After pushing / opening a PR

Passing local checks is necessary, not sufficient — a stale branch, a main-side
regression, or a Greptile finding can still block merge. After every push:

1. **Check CI status.**

   ```bash
   gh pr checks <PR#> --repo Tracer-Cloud/opensre
   ```

   For a failing job, pull its log before touching code:

   ```bash
   gh run view <run-id> --repo Tracer-Cloud/opensre --job <job-id> --log-failed
   ```

   Attribute root cause before fixing anything — **PR-caused** (your diff broke
   it: fix it) vs **pre-existing/main-side** (fails identically on a clean
   `upstream/main` checkout of the same file: do not "fix" it in your PR).
   Check `gh pr view <PR#> --json mergeStateStatus` first: `BEHIND` means your
   branch is stale, and GitHub Actions checks out the *synthetic merge* of
   your branch with current `main` (`refs/pull/<PR#>/merge`), not your raw
   branch head — a stale branch can produce a failure that reproduces on
   neither side alone. Merge (or rebase) the current base branch into yours
   and re-push before investigating further; that alone resolves most of
   these.

2. **Check for review feedback.** Greptile posts automatically; trigger or
   re-trigger it with a PR comment:

   ```
   @greptile review
   ```

   Read findings via `gh pr view <PR#> --json comments,reviews`, or the
   Greptile summary comment on the PR. See
   [CONTRIBUTING.md § Greptile Code Review](CONTRIBUTING.md#greptile-code-review)
   for the confidence-score bar (5/5, zero unresolved) and the
   [greploop skill](https://skills.sh/greptileai/skills/greploop) that
   automates the trigger/wait/fix/re-review loop.

3. **Fix, don't rubber-stamp.** Verify a flagged issue is real before patching
   — read the affected code path, don't just apply the suggested diff blind.
   Where a test can capture the defect, add or extend one — this doesn't apply
   to docs/process-only changes (§0) or config-only fixes with no unit-testable
   behavior (e.g. an `.importlinter.strict` allowlist entry). Re-run the
   relevant checks from §1–3 locally, push, and repeat from step 1 until CI is
   green and Greptile is 5/5 with no unresolved threads.

## Precedence

If readiness instructions conflict across docs, **this file wins** for push/PR checks.
