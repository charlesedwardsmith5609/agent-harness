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
