#!/usr/bin/env bash
# Python checks for agent-harness: ruff lint, mypy, pytest with coverage floor, offline policy eval.
# Adapted from the kit default (profiles/python/check.sh, kit c8eabf8): tools come from PATH or
# .venv (not a required .venv), `ruff format --check` is dropped (the repo is not ruff-formatted),
# and mypy/pytest/eval use the repo's real configuration. Runs in the project directory.
set -u
fail=0
stage() { printf '\n== python: %s\n' "$1"; }
[ -x .venv/bin/python ] && PATH="$PWD/.venv/bin:$PATH"
for t in python3 ruff mypy pytest; do
  command -v "$t" >/dev/null || { echo "FAIL: $t not on PATH (activate the venv with the dev extras: pip install -e '.[dev]')"; exit 1; }
done

stage "ruff check"
if ruff check .; then echo "PASS"; else echo "FAIL"; fail=1; fi

stage "mypy"
if mypy harness agents; then echo "PASS"; else echo "FAIL"; fail=1; fi

stage "pytest (coverage floor 70% from pyproject addopts)"
python3 -m pytest -q
rc=$?
if [ "$rc" -eq 0 ]; then echo "PASS"
elif [ "$rc" -eq 5 ]; then echo "PASS (no tests yet)"
else echo "FAIL"; fail=1
fi

stage "policy eval"
if python3 -m evals.run_policy_eval >/dev/null; then echo "PASS"; else echo "FAIL"; fail=1; fi

exit "$fail"
