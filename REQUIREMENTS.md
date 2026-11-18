## Review fixes 2026-11-14

(REQUIREMENTS.md did not exist; this file was created with only this section.)

- R1. The prefilter and the injection scanner see the full message history (prior turns plus the new message plus the system prompt). A marker in any turn forces the model call.
- R2. The injection scanner matches after normalization (casefold, collapsed whitespace, zero-width characters removed). Canary patterns are matched on the normalized text too.
- R3. The held-out flag comes only from the harness. Model output (`data`) can never set it. A held-out case with no scored row fails.
- R4. The model allowlist is checked at call time as well as at construction.
- R5. The audit row records the model that actually ran, and records that no model ran when the prefilter skipped it.
- R6. The fake side-effect provider has a real DUPLICATE case and a modeled LATENT case, each with a test that can fail.
- R7. The poisoned-memory eval uses the case `memory` field.
- R8. Sweep mode reports held-out regressions without tuning on them.
- R9. `--json` output on stdout is pure JSON; diagnostics go to stderr.
- R10. When the prefilter and the payload disagree on confidence, the lower one gates.
- R11. Each item above has a failing-first test.

## TLDR 2026-11-16

Agreed for this step. Items 1 and 3 are questions and documents. Item 2 is a tested contract. No new dependencies.

- A1. Add `docs/always-on-agents.md`. Map Anthropic's five always-on primitives (event inbox, durable timers, persistent memory, permission system, outbound outbox) to this harness. Cite code for every claim that the harness has something. Say plainly what is missing. Inbox and timers look missing; confirm from the code. Do not build them. End by asking whether an approval that sits too long should expire (a lifecycle wait-until timer) or whether that is out of scope.
- A2. README says a missing key, SDK error, API error, malformed JSON, or unpriced model produces a failed, gated result (BLOCK or HOLD). Write a table per agent (`pr-reviewer`, `deploy-risk`, `runbook-gen`): what downstream sees, and the exit code, when the model is unavailable, rate limited, over budget, or returns bad JSON. One offline test per agent per condition, asserting the result and the exit code. Every wait has a timeout; these tests do not wait. README links the table. Ask whether HOLD on a model outage is the intended default for deploy-risk, or whether an optional rules-only fallback is wanted. Do not build the fallback.
- A3. `max_cost_usd` is per run. Report whether anything caps spend across runs. Write a short design note for a rolling daily cap kept in the store that pauses runs when reached. Name the owner of the counter: one mutex or one writer, not both. Ask. Put the note in `plans/step-4.md`. Build nothing.

## Answers 2026-11-17

- A1 answer: yes. A gated human approval expires. Build a lifecycle wait-until. No background thread and no sleep.
- A2 answer: HOLD is the deploy-risk outage default. Do not build a rules-only fallback.
- A3 answer: the opt-in audit-log refusal is enough. Do not build the store counter.
