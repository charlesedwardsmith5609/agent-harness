# Contributing

Issues and pull requests are welcome. Keep changes small and focused, and explain the why in the PR description.

## Before opening a PR

Run the same checks CI runs:

```bash
pip install -e ".[dev,otel]"
ruff check .
mypy
pytest
python -m evals.run_policy_eval
```

`bash scripts/check.sh` runs the same stages locally. The repo is not ruff-formatted, so do not run `ruff format` over it. pytest rewrites the tracked `.coverage` file; restore it with `git checkout .coverage` before committing.

## Guidelines

- Add or update tests with behavior changes.
- Do not commit secrets, tokens, or generated artifacts.
- If a change touches AI-generated code, review it as you would any other contribution.
