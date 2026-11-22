# Step 7 — baseline records its model, dependency cooldown

Two items from the TLDR 2026-11-20 request, run unattended. Both have a default stated in the request, so both are built on that default and flagged "implemented on the stated default, pending owner confirmation". Open questions are at the end, each with a recommendation.

Base: `improve/tldr-2026-11-17` (step 6, plan only), which sits on `improve/tldr-2026-11-16`. Line numbers below are from that base.

## Item 1 — `evals/baseline.json` records the model it was measured on

### Today (cited)

- `evals/baseline.json` has `agent`, `chosen_effort`, `baseline_effort`, `source`, `summary`, `floors`, `ceilings`, `per_case`. No model id. `source` says "offline floors + effort_defaults.json; tighten after a live --sweep-effort", so the checked-in floors were not measured on any model.
- The sweep does not write `baseline.json`. `--sweep-effort` prints a payload with `results`, `chosen_effort`, `baseline_effort` (`evals/run_eval.py:262-273`). README says to copy the winner by hand (`README.md:224-227`).
- The live model is a literal in two places: `_live_review` (`evals/run_eval.py:199`) and `review_diff`'s default `RunConfig` (`agents/pr_reviewer.py:271`), both `claude-sonnet-5-5`. No flag or env var changes it.
- `check_baseline.regressions` compares floors and ceilings only (`evals/check_baseline.py:44-57`). `case_regressions` compares `per_case` (`:17-41`). Neither reads a model.
- `PAIRING_STRICT` is read only by the shell gate today (`scripts/check.sh`). No Python reads it.

### Change

Types and functions:

- `agents/pr_reviewer.py`: `REVIEW_MODEL = "claude-sonnet-5-5"`, used at the `review_diff` default (`:271`). Same value; one source.
- `evals/run_eval.py`: `_live_review` uses `REVIEW_MODEL` instead of the literal (`:199`). New `live_settings(effort: str | None) -> dict[str, Any]` returns `{"max_tokens": 2048, "min_confidence": 0.65, "max_cost_usd": 0.30, "reasoning_effort": effort}`, the values `_live_review` already passes (`:197-204`), and `_live_review` builds its `RunConfig` from it so the two cannot drift.
- `evals/check_baseline.py`:
  - `OFFLINE_MODEL = "offline"`.
  - `strict_pairing() -> bool`: `os.environ.get("PAIRING_STRICT") == "1"`.
  - `model_pairing(baseline: dict[str, Any], run_model: str | None, *, strict: bool) -> tuple[list[str], list[str]]` returns `(warnings, problems)`. Pure; does not read the environment.
    - Baseline has no `model` key, or it is not a non-empty string: one warning, "baseline has no model field; the model it was measured on is unknown". A warning under `strict` too: the request says an old baseline warns, and failing it is left to the owner (Q2).
    - `run_model` is None (a summary file with no `model`): one warning, "run has no model field". A warning under `strict` too (Q2).
    - Both present and equal: no warning.
    - Both present and different: one warning naming both, for example "baseline measured on 'offline', this run used 'claude-sonnet-5-5'; re-measure after a model change". Under `strict`, the same text is a problem instead.
  - `main()`: reads top-level `model` from the `--summary` payload, calls `model_pairing(..., strict=strict_pairing())`, prints warnings, adds problems to the regression list (exit 1). An old baseline loads exactly as now.
- `evals/run_eval.py` `main()`:
  - Single run: payload gains `"model": REVIEW_MODEL` and `"settings": live_settings(args.effort)`.
  - Sweep: payload gains `"model": REVIEW_MODEL` and `"settings": live_settings(chosen)`, so a person copying the winner into `baseline.json` copies the model too.
  - Both paths call `model_pairing(load_baseline(), REVIEW_MODEL, strict=strict_pairing())`. Warnings print through `_report` (stderr under `--json`, stdout otherwise), on every live run. Problems exit 1 only with `--check-baseline`, the same rule as floor regressions (`:242-248`).
- `evals/baseline.json`: add `"model": "offline"` and `"settings": {"max_tokens": 2048, "min_confidence": 0.65, "max_cost_usd": 0.30, "reasoning_effort": "medium"}`. Only `model` is compared (Q3). `chosen_effort` stays.

Consequence, stated in the docs: until a live sweep replaces `"offline"`, every live run warns, and a live `--check-baseline` under `PAIRING_STRICT=1` fails. That is the rule working: the floors were not measured on that model.

CI: `.github/workflows/ci.yml` "Baseline file is well-formed" step is unchanged. It calls `regressions` only and does not set `PAIRING_STRICT`.

Shared state: none. Single-threaded CLI; the environment is read once per `main`.

### Tests (new file `tests/test_baseline_model.py`; offline, no waits)

Every test sets or deletes `PAIRING_STRICT` with `monkeypatch`, so a gate run under `PAIRING_STRICT=1` does not change the outcome. Baselines are dicts or `tmp_path` files; `run_eval.load_baseline` and `check_baseline.load_baseline` are monkeypatched by name (`run_eval` imports the name at `:23`).

1. Baseline model `A`, run model `B`, not strict: one warning that names `A` and `B`; no problem.
2. Same model: no warning, no problem.
3. Mismatch with `strict=True`: one problem, no warning.
4. Baseline without `model`, not strict: one warning, no exception. Same with `strict=True`: still one warning, no problem.
5. `check_baseline.main` on a summary file without `model` and an old baseline without `model`: returns 0 and prints the warnings (no crash).
6. `check_baseline.main` with `PAIRING_STRICT=1` and a mismatched summary model: returns 1.
7. `run_eval.main(["--json", "--check-baseline"])` with a perfect fake review and a baseline on another model, not strict: no `SystemExit`, stdout parses as one JSON document whose `model` is `REVIEW_MODEL`, and the warning is on stderr.
8. Same with `PAIRING_STRICT=1`: `SystemExit(1)`, stdout still one JSON document, problem text on stderr.
9. `run_eval.main(["--sweep-effort", "--json"])` with a perfect fake review: payload has `model` and `settings.reasoning_effort == payload["chosen_effort"]`.
10. The checked-in `evals/baseline.json` has `model == "offline"`.

Existing test to update: `tests/test_evals.py` `test_run_eval_json_per_case_regression_goes_to_stderr` asserts `set(payload) == {"rows", "summary", "mean_cost_usd"}`. It becomes `{"rows", "summary", "mean_cost_usd", "model", "settings"}`. That test also sees the new model warning on stderr; its assertions are about "per-case regression" text, so they hold.

### Docs

- `DECISIONS.md` entry "2026-11-20: Baseline records its model": the rule (warn by default, fail under `PAIRING_STRICT=1`), `offline` meaning no model, an old baseline still reads, and "implemented on the stated default, pending owner confirmation". Reason: behaviour after a model swap can change on the same instructions (single-source claims in the request; not verified here).
- `README.md` reviewer-eval section: one sentence after `:226-227` saying the baseline names its model and what a mismatch does.
- `CHANGELOG.md`: one line.

### Done when

- Tests 1-10 fail before the change and pass after. `bash scripts/check.sh` passes (all stages), and passes again with `PAIRING_STRICT=1`.
- `bash scripts/cross-review.sh` run on the diff, findings dispositioned in this plan.

## Item 2 — Dependabot cooldown

### Docs check (done while planning, 2026-11-21)

Read `https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference` (first 100,000 of about 186,000 characters). What the page says:

- `cooldown` applies to version updates only: "The `cooldown` option is only available for *version* updates, not *security* updates." The request's belief is confirmed.
- Options: `default-days`, `semver-major-days`, `semver-minor-days`, `semver-patch-days`, `include` and `exclude` (up to 150 items each, wildcards allowed; `exclude` wins over `include`). Unset semver days fall back to `default-days`.
- `pip` supports `default-days` and the semver days. `github-actions` supports `default-days` only.
- The page says Dependabot applies a default 3-day cooldown to version updates when none is set, and that this does not apply to security updates.

Not confirmed: a minimum or maximum for the day values (not stated in the part read), and anything in the unread second half of the page. The implementer re-reads the page and records the date. If the page cannot be read, say so in the commit and the report.

### Change

`.github/dependabot.yml`: add to both entries

```yaml
    cooldown:
      default-days: 7
```

No semver split, no include/exclude: one number is easier to reason about, and `github-actions` does not take the semver keys. Security updates are not affected, per the page above, so no "shorter for security" setting is needed or possible.

`DECISIONS.md` entry "2026-11-20: New releases wait 7 days": provenance is not safety; a release from the real publisher, with valid provenance, can still be malicious; so version updates wait 7 days before Dependabot proposes them, while security updates are not delayed. Name the days (7, pending owner confirmation). Cite the request's sources as claims, not verified facts.

`CHANGELOG.md`: one line.

### YAML check

No existing check parses `.github/dependabot.yml`: `scripts/check.sh` runs ruff, mypy, pytest and the policy eval, and `.github/workflows/ci.yml` has no YAML step. The standard library has no YAML parser, and the repo declares none (`pyproject.toml`).

- Repeatable test, no new dependency: `tests/test_dependabot_cooldown.py` reads the file as text, splits it into `- package-ecosystem:` blocks, and asserts there are exactly the two ecosystems (`pip`, `github-actions`) and that each block has a `cooldown:` line followed by `default-days: 7` at deeper indentation. It fails before the change and passes after. It is a text check, not a YAML parse.
- One-off parse: PyYAML installed with `uv` in the session scratchpad venv (the run's brief allows scratchpad-only Python deps; it is not added to `pyproject.toml` and not used by any repo check) loads the file and asserts `cooldown["default-days"] == 7` for both entries. Recorded in the commit message.
- GitHub validates the file on push; that is not run here (no push).

### Done when

- Both ecosystems have `cooldown.default-days: 7`, `tests/test_dependabot_cooldown.py` failed first and now passes, the one-off parse passes, the DECISIONS paragraph names 7 days and the reason, gate passes.

## Unattended policy for this step

- Item 1: built on the stated default (warn, fail under `PAIRING_STRICT=1`). Pending owner confirmation.
- Item 2: built on the proposed 7 days. Pending owner confirmation.
- One commit per item. No push.

## Open questions for the owner

- C1-Q1. Should the sweep write `baseline.json` itself (a `--write-baseline` flag)? Recommendation: no. Keep the copy by hand (README), now with `model` and `settings` in the payload.
- C1-Q2. Under `PAIRING_STRICT=1`, should a baseline or summary with no `model` fail? Recommendation: yes, later (an unknown pairing is not a verified one). Not implemented: the request says an old baseline warns, so it stays a warning in both modes until the owner says otherwise.
- C1-Q3. Compare `settings` (effort, max_tokens) as well as `model`? Recommendation: no for now. Record them; compare only `model`.
- C1-Q4. Make the live model configurable (flag or env)? Recommendation: not in this step. Today it is a constant, so the check fires only when the constant changes or the baseline says `offline`.
- C2-Q1. 7 days for both ecosystems? Recommendation: yes. Optionally shorter for `github-actions` patch releases; not done.

## Review

`bash scripts/plan-review.sh plans/step-7.md` ran on 2026-11-21 with provider openai (model `gpt-6.1-sol`). Output saved locally to `.reviews/plan-review-step-7-20261121-225521-openai.md` (git-ignored). Three findings, all MEDIUM:

- Strict failure on a baseline with no `model` breaks the "old baseline warns" promise (WRONG): agree. Missing `model` is a warning in both modes; strict failure for it stays open question C1-Q2.
- PyYAML in the scratchpad is an unapproved dependency (SCOPE): partly disagree. The run's brief allows scratchpad-only Python deps, and it is not added to the repo or any repo check. The repeatable check no longer needs it (next item).
- No repeatable, failing-first test for the cooldown (MISSING): agree. Added `tests/test_dependabot_cooldown.py`, a text check with no new dependency.

## Files

- agents/pr_reviewer.py
- evals/run_eval.py
- evals/check_baseline.py
- evals/baseline.json
- tests/test_baseline_model.py
- tests/test_evals.py
- tests/test_dependabot_cooldown.py
- .github/dependabot.yml
- README.md
- DECISIONS.md
- CHANGELOG.md
- REQUIREMENTS.md
- plans/step-7.md
