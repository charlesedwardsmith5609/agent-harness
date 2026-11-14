#!/usr/bin/env bash
# Deterministic agent-misbehavior heuristics over changed working-tree files (not diff text).
# Warns by default; set PAIRING_STRICT=1 to fail when findings exist.
# Usage: bash scripts/misbehavior-check.sh
# Expects to run from the project root (or pass ROOT as $1).
set -uo pipefail

root="${1:-.}"
cd "$root" || exit 2
root="$(pwd)"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "misbehavior: skip (not a git repository)"
  exit 0
fi

strict=0
[ "${PAIRING_STRICT:-}" = "1" ] && strict=1
label="WARN"
[ "$strict" -eq 1 ] && label="FAIL"

findings=0
report() {
  printf 'misbehavior: %s: %s\n' "$label" "$1"
  findings=1
}

# Changed set: modified and untracked-not-ignored files in the working tree, plus staged paths.
# Paths that no longer exist are dropped from content checks (kept in raw_file for gate checks).
changed_file=$(mktemp) || exit 2
raw_file=$(mktemp) || exit 2
allowed_file=$(mktemp) || exit 2
trap 'rm -f "$changed_file" "$raw_file" "$allowed_file"' EXIT
{
  git ls-files -m -o --exclude-standard 2>/dev/null || true
  git diff --cached --name-only 2>/dev/null || true
} | sed '/^$/d' | sort -u > "$raw_file"
while IFS= read -r path; do
  [ -e "$path" ] && printf '%s\n' "$path"
done < "$raw_file" > "$changed_file"

if [ ! -s "$raw_file" ]; then
  echo "misbehavior: OK (no changes)"
  exit 0
fi

is_test_path() {
  # In bash case patterns, * matches across '/'.
  case "$1" in
    *_test.go|test_*.py|*_test.py|*.test.ts|*.test.tsx|*.spec.ts|*.spec.tsx) return 0 ;;
    tests/*|*/tests/*|__tests__/*|*/__tests__/*) return 0 ;;
    *) return 1 ;;
  esac
}

is_scope_exempt() {
  case "$1" in
    plans/*|REQUIREMENTS.md|CLAUDE.md|AGENTS.md|.reviews/*|*.reviews/*) return 0 ;;
    .pairing-setup.log|.misbehavior-changed.*) return 0 ;;
    *) return 1 ;;
  esac
}

is_gate_path() {
  case "$1" in
    scripts/check.sh|scripts/misbehavior-check.sh|scripts/assertion-check.sh|scripts/review-call.sh|.pairing-profiles) return 0 ;;
    scripts/checks/*) return 0 ;;
    .github/workflows/*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- Gate files changed ---
while IFS= read -r path; do
  [ -n "$path" ] || continue
  if is_gate_path "$path"; then
    report "validation gate file changed: $path"
  fi
done < "$raw_file"

# --- Skipped tests / disabled validation markers in changed files ---
# Reads the content of changed test files and *.json / *.config.* files, not diff text.
skip_line_re='t\.Skip\(|\.skip\(|xit\(|xdescribe\(|test\.skip|it\.skip|describe\.skip|@pytest\.mark\.skip|passWithNoTests'
while IFS= read -r path; do
  [ -f "$path" ] || continue
  case "$path" in
    *.json|*.config.*) ;;
    *) is_test_path "$path" || continue ;;
  esac
  if grep -E "$skip_line_re" "$path" >/dev/null 2>&1; then
    report "changed file contains skipped/disabled test or validation marker: $path"
  fi
done < "$changed_file"

# --- Test-only changes (no matching source change) ---
has_test=0
has_source=0
while IFS= read -r path; do
  [ -n "$path" ] || continue
  is_scope_exempt "$path" && continue
  if is_test_path "$path"; then
    has_test=1
  else
    # Treat non-markdown, non-plan docs as source-ish; ignore pure docs? Plan says
    # "test changes with no matching source change" — any non-test path counts as source.
    has_source=1
  fi
done < "$changed_file"

if [ "$has_test" -eq 1 ] && [ "$has_source" -eq 0 ]; then
  report "test files changed with no matching source change"
fi

# --- Plan scope (only when ## Files is parseable) ---
plan=""
# shellcheck disable=SC2012
plan=$(ls plans/step-*.md 2>/dev/null | sort -V | tail -n 1 || true)
if [ -z "$plan" ] || [ ! -f "$plan" ]; then
  echo "misbehavior: plan-scope skipped (no plans/step-*.md)"
else
  # Extract bullets under ## Files until the next ## heading.
  files_list=$(awk '
    BEGIN { in_files=0; n=0 }
    /^## Files[[:space:]]*$/ { in_files=1; next }
    /^## / { if (in_files) exit }
    in_files && /^-[[:space:]]+/ {
      line=$0
      sub(/^-[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      if (line != "") { print line; n++ }
    }
    END { }
  ' "$plan")
  if [ -z "$files_list" ]; then
    echo "misbehavior: plan-scope skipped (no parseable ## Files)"
  else
    printf '%s\n' "$files_list" | sort -u > "$allowed_file"
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      is_scope_exempt "$path" && continue
      if ! grep -Fxq "$path" "$allowed_file"; then
        report "path outside plan ## Files: $path"
      fi
    done < "$changed_file"
  fi
fi

if [ "$findings" -eq 0 ]; then
  echo "misbehavior: OK"
  exit 0
fi

if [ "$strict" -eq 1 ]; then
  echo "misbehavior: FAILED (PAIRING_STRICT=1)"
  exit 1
fi
echo "misbehavior: warnings only (set PAIRING_STRICT=1 to fail)"
exit 0
