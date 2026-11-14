#!/usr/bin/env bash
# Warn when a test function has no assertion. Scans test files on disk, not diffs.
# Warns by default. Set PAIRING_STRICT=1 to fail.
# Usage: bash scripts/assertion-check.sh [root]
set -uo pipefail

if [ "${BASH_VERSINFO[0]}" -lt 4 ] || { [ "${BASH_VERSINFO[0]}" -eq 4 ] && [ "${BASH_VERSINFO[1]}" -lt 4 ]; }; then
  echo "assertions: ERROR: needs bash 4.4 or newer (found $BASH_VERSION)" >&2
  exit 2
fi

root="${1:-.}"
if [ ! -d "$root" ]; then
  echo "assertions: ERROR: root is not a directory: $root" >&2
  exit 2
fi
cd "$root" || exit 2

strict=0
[ "${PAIRING_STRICT:-}" = "1" ] && strict=1
label="WARN"
[ "$strict" -eq 1 ] && label="FAIL"

findings=0
report() {
  printf 'assertions: %s: %s\n' "$label" "$1"
  findings=1
}

errfile=$(mktemp) || { echo "assertions: ERROR: mktemp failed" >&2; exit 2; }
trap 'rm -f "$errfile"' EXIT

# Exclusions are decided from paths relative to the root (find starts at "."), so where the
# project lives on disk never changes the result.
prune_names=(.git node_modules vendor .venv venv site-packages __pycache__ .tox .nox build dist target)
prune_expr=()
for n in "${prune_names[@]}"; do
  prune_expr+=(-name "$n" -o)
done
# Any directory holding pyvenv.cfg is a virtualenv, whatever its name.
while IFS= read -r -d '' cfg; do
  d="${cfg%/pyvenv.cfg}"
  d="${d//\\/\\\\}"; d="${d//\*/\\*}"; d="${d//\?/\\?}"; d="${d//\[/\\[}"
  prune_expr+=(-path "$d" -o)
done < <(find . -name pyvenv.cfg -type f -print0 2>/dev/null)
unset 'prune_expr[${#prune_expr[@]}-1]'

mapfile -d '' files < <(find . \( "${prune_expr[@]}" \) -prune -o -type f \
  \( -name '*_test.go' -o -name 'test_*.py' -o -name '*_test.py' \
     -o -name '*.test.ts' -o -name '*.test.tsx' -o -name '*.spec.ts' -o -name '*.spec.tsx' \) \
  -print0 2>"$errfile")

# Fail closed: paths the scan could not read are reported, and fail under PAIRING_STRICT=1.
if [ -s "$errfile" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    report "could not scan: ${line#find: }"
  done < "$errfile"
fi

scan_go() {
  local file="$1"
  local hits
  hits=$(awk '
    function flush() {
      if (name != "" && asserted == 0) print name
    }
    /^func Test/ {
      flush()
      name = $2
      sub(/\(.*/, "", name)
      # Only a func taking *testing.T is a test; TestMain and Test-prefixed helpers are not.
      if (name == "TestMain" || $0 !~ /\*testing\.T/) name = ""
      asserted = 0
      next
    }
    /^func / { flush(); name = "" }
    {
      line = $0
      gsub(/"([^"\\]|\\.)*"/, "\"\"", line)
      sub(/\/\/.*/, "", line)
    }
    name != "" && line ~ /t\.(Error|Fatal|Fail)|assert\.|require\.|cmp\.(Diff|Equal)|[Aa]ssert[A-Za-z]*\(/ { asserted = 1 }
    END { flush() }
  ' "$file")
  local name
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    report "${file#./}: $name has no assertion"
  done <<< "$hits"
}

scan_py() {
  local file="$1"
  local hits
  hits=$(awk '
    function flush() {
      if (name != "" && asserted == 0) print name
    }
    /^[[:space:]]*def test_/ {
      flush()
      name = $2
      sub(/\(.*/, "", name)
      asserted = 0
      next
    }
    /^[[:space:]]*def / { flush(); name = "" }
    {
      line = $0
      gsub(/"([^"\\]|\\.)*"/, "\"\"", line)
      gsub(/\047([^\047\\]|\\.)*\047/, "\047\047", line)
      sub(/#.*/, "", line)
    }
    name != "" && line ~ /assert |assert\(|pytest\.raises|pytest\.fail|self\.assert/ { asserted = 1 }
    END { flush() }
  ' "$file")
  local name
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    report "${file#./}: $name has no assertion"
  done <<< "$hits"
}

scan_js() {
  local file="$1"
  # Comment text does not count as an assertion.
  if ! sed -E 's://.*$::' "$file" | grep -Eq 'expect\(|expect\.(assertions|hasAssertions)|assert[[:space:]]*\(|assert\.'; then
    report "${file#./}: no expect or assert call"
  fi
}

for file in "${files[@]}"; do
  [ -n "$file" ] || continue
  if [ ! -r "$file" ]; then
    report "could not scan: ${file#./}: not readable"
    continue
  fi
  case "$file" in
    *_test.go) scan_go "$file" ;;
    *.py) scan_py "$file" ;;
    *) scan_js "$file" ;;
  esac
done

if [ "$findings" -eq 0 ]; then
  echo "assertions: OK"
  exit 0
fi
if [ "$strict" -eq 1 ]; then
  echo "assertions: FAILED (PAIRING_STRICT=1)"
  exit 1
fi
echo "assertions: warnings only (set PAIRING_STRICT=1 to fail)"
exit 0
