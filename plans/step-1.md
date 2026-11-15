# Step 1 - HIGH: injection coverage (findings 1, 2)

## Findings covered
1 (prefilter ignores prior_messages), 2 (scanner evaded by case, whitespace, zero-width).
Status: both open. Reads: `agent.py:290` passes only `system_prompt, user_message`; `_prefilter_decision` (`agent.py:452`) scans only those two.

## Types
- `find_injection_markers(text: str) -> list[str]` unchanged signature. Adds a private `_normalize(text: str) -> str`.
- `_prefilter_decision(system, user, history)` gains the prior messages (list of message dicts). Message `content` may be a str or a list of blocks; only text is scanned, any other shape is treated as text via `str()` so nothing is skipped silently.

## Shared state ownership
- `harness/injection.py` owns normalization and the marker lists. Callers do not normalize.
- `harness/agent.py` owns the transcript; it hands the scanner a read-only copy.
- The scanner returns the original canary text where possible and the phrase otherwise, so the existing `refuse_if_replicated` messages stay stable.

## Assumptions
- A hit in any prior turn, including assistant turns, forces the model call (safer reading). Skip eligibility requires a clean full history.
- Normalization: NFKC, casefold, remove zero-width/format chars (U+200B-U+200D, U+2060, U+FEFF), collapse all whitespace runs to one space.

## Tests (written failing-first)
1. A marker only in `prior_messages[0]` prevents a prefilter skip; the model stub is called.
2. A marker in an assistant prior turn also prevents the skip.
3. List-of-blocks content with a marker is detected.
4. Clean history still lets a confident prefilter skip (no regression).
5. Table of positive variants: upper-case, mixed case, tabs/newlines between words, double spaces, zero-width characters inside words, a canary with zero-width inside - each flagged.
6. Table of near-misses that must not flag: ordinary review text, "ignore" and "findings" in unrelated sentences, partial phrases.
7. `refuse_if_replicated` refuses a spaced/cased variant.

## Files
- harness/injection.py
- harness/agent.py
- tests/test_prefilter.py
- tests/test_store_budget_injection.py
- CHANGELOG.md
