---
description: Get an independent review of the current diff from a different AI provider
argument-hint: "[--provider cursor|openai|anthropic] [--base REF]"
---
Run the cross-model review: `bash scripts/cross-review.sh $ARGUMENTS`, with a 10-minute timeout
(a review can take a few minutes). On Windows without WSL, use
`pwsh -NoProfile -File scripts/cross-review.ps1` with `-Provider` and `-Base` instead; it supports
the openai and anthropic providers only.

With no uncommitted changes and no base given, the script reviews the last commit and says so.
If the milestone spans more than one commit, rerun it with a base that covers all of them.

Then, for each finding, give a one-line assessment: AGREE or DISAGREE, with the reason, and the
fix you would make if the human accepts it. Do not change any code. The human decides which
findings to act on.

Judge findings in the context of the whole package, not just the files in the diff. Never infer
whether code builds or tests pass: run `go build ./...` or the check script and report what it
actually says. Label anything you could not verify as unverified.
