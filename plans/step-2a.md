# Step 2a - MEDIUM: held-out (findings 3, 8, 9)

Step 2 of the original plan is split: 2a (3, 9, 8) here, 2b (4, 5, 6, 7) in step-2b.md. See DECISIONS.md 2026-11-14.

## Findings covered
3 held-out source of truth, 8 sweep skips held-out regression, 9 missing held-out row passes.

## Types
- `RunConfig.held_out: bool = False`; `audit()` writes top-level `held_out`.
- `case_regressions` also fails a `per_case` entry with `held_out: true` that has no scored row.
- `run_eval.held_out_cases()` and `held_out_problems(review, baseline)`.

## Shared state ownership
- The harness owns the held-out audit flag. Mining reads only top-level keys. The eval loader owns case `split`.

## Assumptions
- Missing-row failure applies only to baseline entries marked `held_out: true`; scored rows with no entry pass.
- Supersedes the `data.*` part of the 2026-11-13 held-out decision (D11 wins).

## Tests (failing-first)
1. `data.held_out` / `data.split` do not drop a row from mining.
2. A real `audit()` row from `RunConfig(held_out=True)` is dropped by mining; a normal run's row is mined.
3. Baseline held-out id with no scored row is a problem; with an exact row, none.
4. Scored row without baseline entry passes.
5. Sweep helper reports a held-out regression while tuning rows never review held-out cases.

## Files
- harness/agent.py
- evals/mine_failures.py
- evals/check_baseline.py
- evals/run_eval.py
- tests/test_held_out.py
- DECISIONS.md
- CHANGELOG.md
