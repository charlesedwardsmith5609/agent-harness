---
description: Run the validation gate for every language in the project and report per stage
---
Run `bash scripts/check.sh`.

Report PASS or FAIL for each language and each stage within it, and say whether the Go race
detector ran if this project has Go. If anything fails, diagnose the cause and propose the
smallest fix, but do not apply it until the human approves.
