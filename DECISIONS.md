# Decisions

## 2026-11-13: Catch-up branch base

Context: `improve/tldr-2026-11-09` is two commits ahead of main and unmerged. One of those commits is `mine_failures`. The audit identity field is already on main.

Options: cut the catch-up branch from main and reimplement mining, or cut it from the unmerged branch.

Choice: `improve/tldr-catchup-2026-11-13` is based on `origin/improve/tldr-2026-11-09`.

Why: Reimplementing mining would duplicate `776b108`. The identity field is already on main (`caller_identity`) and is not redone.

## 2026-11-13: mine_failures already present

Context: The catch-up item asks for a CLI that reads the audit log and writes candidate eval cases to a review directory, never into the live set.

Evidence it is already done: `evals/mine_failures.py`, `tests/test_mine_failures.py`, and changelog line for `python -m evals.mine_failures`. The CLI writes the `--out` directory and the module docstring says candidates are not added to `evals/cases/` or `baseline.json`.

Choice: skip the item. Later held-out work adds an exclusion so mined rows marked held-out are dropped. That is new, not a second miner.

## 2026-11-13: Prefilter skip is opt-in and loses to the injection scanner

Context: A typed decision with confidence should skip the model above a threshold, except when the injection scanner flags the input.

Options: always install a default prefilter; treat a scanner hit as an immediate refuse; treat a scanner hit as "do not skip".

Choice: no prefilter unless `RunConfig.prefilter` and `prefilter_skip_threshold` are both set. A hit from `find_injection_markers` on the system prompt or the user message forces the model call. A prefilter exception becomes `PreFilterError` and fails the run without calling the model and without a retry. `dry_run` is unchanged and still returns the canned zero-confidence body when the prefilter does not skip.

Why: Fail closed on a broken prefilter. Do not add a dependency. Do not change runs that never opted in. The scanner override follows the request: never skip when the input is flagged.

## 2026-11-13: Held-out split is a case field, not a second suite

Context: Held-out cases need a split, per-case regression against `baseline.json`, and exclusion from tuning and mining.

Options: a separate directory the scorer never loads; a `split` field on the existing case files.

Choice: `split: held_out` on the case. `tuning_cases()` and `--sweep-effort` drop that split. `mine_failures` drops audit rows with `split: held_out` or `held_out: true` (including the same keys under `data`). `per_case` in `baseline.json` is optional per id. A listed id whose `gate_exact` disagrees is a regression, including held-out ids, so the report still sees them. Missing `per_case` entries are not regressions.

Why: One loader keeps the existing suite. Tuning and mining cannot use the held-out row. The baseline floors are unchanged.

## 2026-11-13: Model allowlist is config-driven

Context: Every audit row should carry the model id, and a model id off the allowlist should fail closed.

Evidence: `AgentResult.model` is already copied into the audit JSON. The field name stays `model`.

Options: reject every run until an allowlist file exists in the repo; reject only when the operator sets `allowed_models`.

Choice: an unset `allowed_models` does not add a second gate. A set list must contain `model`. An empty list rejects every model. No new model ids were added.

Why: Shipping an empty or guessed allowlist would fail every current caller, or would invent model ids. The operator's list is the fail-closed control.

## 2026-11-13: Side-effect double stays in tests

Context: Tests need a provider with success, timeout after commit, partial, latent, and duplicate outcomes, exercising idempotency keys and the lifecycle machine.

Options: a production provider interface plus a fake; a test-only double that calls the existing machine methods.

Choice: `tests/fake_effects.py` calls `perform_effect`, `commit_effect`, and `fail_effect`. Timeout is raised after commit so the key is already recorded. Partial uses `fail_effect` and does not commit. Latent commits and reports that the effect is not visible yet. A second apply with the same key returns duplicate because `perform_effect` returns false. No new dependency and no new production type.

Why: The lifecycle machine is already the owner of the key. A second implementation could drift from it.

## 2026-11-13: Category field already existed on PR-reviewer cases

Context: Per-category reporting for the injection and scope evals. Some category usage already existed in `tests/test_policy_eval.py` (`category_rate`).

Evidence: every file in `evals/cases/` has `expected_category` (null on clean diffs). No file in `evals/cases_scope/` had a category field.

Choice: keep `expected_category` on the PR-reviewer cases and group injection hold rate by it. Add `category` to each scope case. Add one scope case, `05-poisoned-memory`, whose remembered shell command is refused. The new report is counts and rates. Baseline floors are unchanged.

Why: Do not rename a field the scorer already uses. Do not add a second poisoned case in the injection suite when one case was requested.

## 2026-11-14: Held-out comes from the harness only; missing held-out row fails

Context: Review findings 3, 8, 9. The earlier held-out entry (2026-11-13) let mining drop rows whose model-supplied `data` carried `held_out` or `split`. Real audit rows never carried a top-level flag, so exclusion was dead for them. A baseline-listed held-out id with no scored row passed. `--sweep-effort` never checked held-out cases.

Supersedes: the `data.*` part of "Held-out split is a case field" and the sentence "Missing `per_case` entries are not regressions" for held-out ids. The rest of that entry stands.

Choice: `RunConfig.held_out` (set by the harness owner) is written by `audit()` as top-level `held_out`. Mining reads only top-level `split`/`held_out`; `data.*` is ignored. In `baseline.json`, a `per_case` entry with `held_out: true` and no scored row is a problem. A scored row with no `per_case` entry still passes. After choosing an effort, a sweep scores the held-out cases once at that effort and reports problems (exit 1 with `--check-baseline`); the held-out cases never take part in the choice.

Why: Model output cannot be trusted to mark or unmark its own row. Failing on a missing row closes the silent-pass hole without breaking cases that have no baseline entry.

## 2026-11-14: Call-time allowlist, `model_ran`, fake provider semantics, memory eval

Context: Review findings 4, 5, 6, 7.

Choice:
- Finding 4: `check_model_allowed(model, allowed)` runs at construction and at the top of `_call_api`, before the dry-run branch and before any client is built. A config changed after construction fails closed.
- Finding 5: `model` keeps meaning the configured request (budget and mining readers are unchanged). New `AgentResult.model_ran: str | None` is the model that actually answered; it is `None` for a prefilter skip, a dry run, and a run whose call never completed. It is written on every audit row.
- Finding 6: the fake provider tracks what the outside system saw. DUPLICATE means the outside system already holds the key (lost reply): the effect does not run again and the machine records it as landed. LATENT commits under the key but is not visible until `reconcile(key)`. This replaces the 2026-11-13 wording "Latent commits and reports that the effect is not visible yet" only by making visibility checkable; tests assert execution counts and visibility.
- Finding 7: a scope case with `memory` models an agent that obeys it: `memory_actions` join `proposed`. Without `memory` they do not, so the row's refused tools change and the case stops matching. A policy that allows the remembered tool fails the case.

Why: Smallest change that keeps existing consumers working and makes each test able to fail.

## 2026-11-14: Confidence disagreement, and `--json` scope

Context: Review findings 10 and 11. Finding 11 does not say which confidences disagree.

Choice for 11 (conservative reading): on a prefilter-skipped run, the gate uses the lower of the prefilter decision's confidence and the payload's `confidence`. A payload with no `confidence` still parses as 0.0, so it stays fail-closed (the earlier plan draft would have fallen back to the decision's value; that is less safe and was not adopted). Runs that call the model are unchanged.

Choice for 10: with `--json`, stdout is exactly one JSON document for `evals.run_eval` (single run and `--sweep-effort`) and for `evals.run_policy_eval`. Regression and failure text goes to stderr in that mode; without `--json` it stays on stdout. `run_eval` and `run_policy_eval` gained `main(argv)` so this is testable.

Why: Lower-of-two never raises trust. Machine consumers can parse stdout without stripping text.
