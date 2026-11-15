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
