---
description: Get an independent review of a plan from a different AI provider before writing code
argument-hint: "[plans/step-N.md] [--provider cursor|openai|anthropic]"
---
Run the plan review: `bash scripts/plan-review.sh $ARGUMENTS`, with a 10-minute timeout (a
review can take a few minutes). With no plan file it reviews the
highest-numbered `plans/step-*.md`. It needs `REQUIREMENTS.md`; if that is missing, write the
agreed requirements there first and ask the human to confirm them.

Then present, as lists:
- the plan's key points in a few lines;
- each reviewer finding with your assessment: AGREE or DISAGREE, with the reason, and the change
  you would make to the plan if the human accepts it. Check claims against the requirements and
  the plan itself; label anything you could not verify as unverified.

Do not change the plan or write any code. Wait for the human to approve, with or without changes.
When they approve with changes, update the plan file to match before implementing.
