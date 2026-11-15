# Step 2b - MEDIUM (findings 4, 5, 6, 7)

Split from the original step 2; findings 3, 8, 9 are in step-2a.md. Decision: `model_ran` instead of `model: null` (DECISIONS.md 2026-11-14).

## Findings covered
3 held-out source of truth, 4 call-time allowlist, 5 audit model, 6 fake provider, 7 memory eval, 8 sweep held-out regression, 9 missing held-out row.
Status: all open. Evidence: `mine_failures._is_held_out_record` trusts `data`; the audit row never writes `split`/`held_out`; `RunConfig.__post_init__` is the only allowlist check; `AgentResult.model = config.model` even when `llm_skipped`; `score_scope_case` never reads `memory`; the `--sweep-effort` branch never calls `case_regressions`; `case_regressions` iterates scored rows only.

## Types
- `RunConfig.held_out: bool = False` (harness-set) written by `audit()` as top-level `held_out`. `AgentResult.model: str | None` (None when skipped) plus `model_requested`.
- `check_model_allowed(model, allowed)` helper, called in `_call_api` immediately before the request.
- `case_regressions(rows, baseline, required_ids=...)` returns a problem for each held-out id with no scored row.
- `FakeEffectProvider` gains explicit scenario semantics (see tests).

## Shared state ownership
- Held-out: the eval loader owns `split`; `RunConfig` owns the audit flag. Only top-level audit keys are read by mining; `data.*` is ignored.
- Model: `RunConfig.model` is the request; the result records what ran.
- Fake provider: `RunMachine` stays the source of truth for effects/keys; the provider holds only its script and call log.

## Assumptions
- DECISIONS.md (repo, 2026-11-13) says missing `per_case` entries are not regressions. D11 says a missing held-out row fails. Resolved as: a baseline `per_case` id marked held-out with no scored row fails; a scored row with no `per_case` entry still passes.
- This supersedes the data-key part of the 2026-11-13 held-out decision; a new dated entry will be appended to DECISIONS.md.
- Skipped runs: `model` is recorded as null with `llm_skipped: true`. Consumers that read `model` (budget rollups, mining) must tolerate null; check in the step.

## Tests (failing-first)
1. A row with `data: {"held_out": true}` and no top-level flag is NOT dropped by mining; a row with harness `held_out: true` is dropped.
2. A real `audit()` row from a `RunConfig(held_out=True)` run is dropped by mining (end-to-end, not hand-built).
3. Changing `config.model` to an off-list value after construction makes `_call_api` raise before any client call.
4. Audit row for a prefilter-skipped run has `model` null and `llm_skipped` true; for a real run it has the model that ran.
5. Fake provider: a DUPLICATE script entry on a first call really exercises the duplicate path and the test fails if `perform_effect` is bypassed; LATENT returns before the commit is visible and a later reconcile sees it (test fails if LATENT behaves like SUCCESS).
6. Poisoned-memory case: score changes when `memory` is removed; a policy that obeys the memory fails the case.
7. Sweep run with a held-out regression in the mocked rows exits non-zero / reports it, and the tuning rows still exclude held-out.
8. Baseline lists a held-out id; no row scored for it -> problem reported. Row present and exact -> no problem.

## Files
- harness/agent.py
- harness/budget.py
- evals/mine_failures.py
- evals/check_baseline.py
- evals/run_eval.py
- evals/run_policy_eval.py
- evals/cases_scope/05-poisoned-memory.json
- tests/fake_effects.py
- tests/test_fake_effects.py
- tests/test_held_out.py
- tests/test_mine_failures.py
- tests/test_model_allowlist.py
- tests/test_prefilter.py
- tests/test_policy_eval.py
- DECISIONS.md
- CHANGELOG.md

## As built (deviations from the text above)
- `AgentResult.model` is unchanged; `model_ran: str | None` is added (not `model: null`, no `model_requested`).
- Memory eval: `memory_actions` on the case join `proposed` only when `memory` is non-empty.
- Fake provider: `executed`, `is_visible`, `reconcile`; DUPLICATE = key already held outside, no second execution.
- Findings 3, 8, 9 moved to step 2a; `budget.py`, `run_eval.py`, `run_policy_eval.py` score-side changes not needed beyond the above.
