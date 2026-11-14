# Step 3 - LOW / QUALITY (findings 10, 11, 12)

## Findings covered
10 `--json` stdout corrupted, 11 confidence disagreement, 12 coverage gaps.
Status: 10 open (`run_eval.py` prints "baseline regression:" and "per-case regression:" to stdout after the JSON). 11 open and under-specified. 12 partial (tests exist for the earlier features, none for items above).

## Types
- `_report(problems, label, *, json_mode)` writes diagnostics to stderr when `--json` is set.
- Gate confidence: `min(decision.confidence, data["confidence"])` for prefilter-skipped runs; if the payload has no confidence, use the decision's.

## Shared state ownership
- `run_eval.py` main owns stdout; nothing else prints while `--json` is active.
- `_parse_response` stays the only place that reads payload confidence.

## Assumptions
- Finding 11 does not say which confidences disagree. Safer reading: the prefilter decision and the payload it supplies. The lower one gates, and a payload without confidence no longer defaults to 0.0 silently on skipped runs. If the owner meant model-vs-gate confidence, this is the part to revisit.
- Finding 12 is satisfied by the per-item tests in steps 1 and 2 plus the tests here; no separate coverage threshold is added.

## Tests (failing-first)
1. `python -m evals.run_eval --json --check-baseline` with a forced regression: stdout parses as one JSON document; the regression text is on stderr.
2. Same for `--sweep-effort --json`.
3. Prefilter confidence 0.95 with payload confidence 0.2 -> gate uses 0.2 and blocks.
4. Prefilter 0.95, payload without confidence -> uses 0.95 only if explicitly equal to the decision; otherwise documented behavior is asserted.
5. Coverage: the suite fails if the new branches in steps 1 and 2 lose their tests (run pytest with `--cov` as CI does; no threshold change).

## Files
- evals/run_eval.py
- harness/agent.py
- tests/test_evals.py
- tests/test_prefilter.py
- CHANGELOG.md

## As built (deviations from the text above)
- Finding 11: lower of decision and payload confidence; a payload with no confidence stays 0.0 (fail closed), not the decision's value. Test 4 is therefore pinned: skipped run, no payload confidence, gate blocks.
- Finding 10 also covers `evals.run_policy_eval --json` (diagnostics to stderr). `run_eval.main(argv)` and `run_policy_eval.main(argv)` added for testing.
- Files also touched: evals/run_policy_eval.py, tests/test_evals.py.
