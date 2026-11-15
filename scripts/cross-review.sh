#!/usr/bin/env bash
# Cross-model review: send the current diff to a different AI provider for an independent review.
# Usage: bash scripts/cross-review.sh [--provider cursor|openai|anthropic|auto] [--model NAME] [--base REF]
#        [--include PATH]...
# Only tracked changes are sent. An untracked file is sent only when you name it with --include.
# Paths matching the deny list below (.env*, keys, credentials) are never sent, even if named.
# The request is scanned with gitleaks first. If gitleaks is missing or fails, nothing is sent.
# scripts/cross-review.ps1 must keep the same deny list and behavior.
# Env:   REVIEW_PROVIDER, CURSOR_REVIEW_MODEL, OPENAI_API_KEY / ANTHROPIC_API_KEY, REVIEW_MODEL,
#        OPENAI_BASE_URL, ANTHROPIC_BASE_URL. See scripts/review-call.sh for how a provider is chosen.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/review-call.sh

provider="${REVIEW_PROVIDER:-auto}"
model=""
explicit=0
base="HEAD"
base_given=0
includes=()
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) provider="$2"; explicit=1; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --base) base="$2"; base_given=1; shift 2 ;;
    --include) [ $# -ge 2 ] || { echo "--include needs a path" >&2; exit 2; }; includes+=("$2"); shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
review_resolve "$provider" "$model" || exit 2

# Deny list (keep identical to scripts/cross-review.ps1). Base names, then directories.
deny_names=('.env*' '*.pem' '*.key' '*.p12' '*.pfx' '*.jks' '*.keystore' '*.kdbx' 'id_rsa*' 'id_dsa*'
  'id_ecdsa*' 'id_ed25519*' '*credential*' '.netrc' '.npmrc' '.pypirc' '.git-credentials' '*.tfstate' '*.tfvars')
deny_dirs=('.aws' '.ssh' '.gnupg')
is_denied() {
  local p="/$1" pat
  for pat in "${deny_names[@]}"; do [[ "$p" == */$pat ]] && return 0; done
  for pat in "${deny_dirs[@]}"; do [[ "$p" == */$pat/* ]] && return 0; done
  return 1
}
excludes=(':(exclude)go.sum' ':(exclude)**/go.sum' ':(exclude)package-lock.json' ':(exclude)**/package-lock.json')
for pat in "${deny_names[@]}"; do excludes+=(":(exclude,glob)**/$pat"); done
for pat in "${deny_dirs[@]}"; do excludes+=(":(exclude,glob)**/$pat/**"); done

# Named untracked files, validated before anything is read.
root=$(pwd -P)
include_files=()
for f in ${includes[@]+"${includes[@]}"}; do
  if is_denied "$f"; then echo "Refusing --include $f: it matches the secret-file deny list. Nothing was sent." >&2; exit 2; fi
  if [ ! -f "$f" ] || [ -L "$f" ]; then echo "--include $f: not a regular file (symlinks are refused)." >&2; exit 2; fi
  full=$(cd "$(dirname "$f")" && pwd -P)/$(basename "$f")
  case "$full" in "$root"/*) ;; *) echo "--include $f: outside the repository." >&2; exit 2 ;; esac
  rel=${full#"$root"/}
  if is_denied "$rel"; then echo "Refusing --include $f: it matches the secret-file deny list. Nothing was sent." >&2; exit 2; fi
  if git ls-files --error-unmatch -- "$rel" >/dev/null 2>&1; then continue; fi  # tracked: already in the diff
  include_files+=("$rel")
done

git rev-parse -q --verify "$base^{commit}" >/dev/null || { echo "unknown base: $base" >&2; exit 2; }
# Tracked changes only. This does not touch the index, so untracked files stay untracked.
get_diff() { git diff --no-color "$1" -- . "${excludes[@]}"; }
diff=$(get_diff "$base")
# Nothing uncommitted and no --base given: review the last commit instead.
if [ -z "$diff" ] && [ "$base_given" = 0 ] && [ "${#include_files[@]}" = 0 ] && git rev-parse -q --verify HEAD~1 >/dev/null; then
  base="HEAD~1"
  echo "No uncommitted changes; reviewing the last commit: $(git log -1 --format='%h %s')" >&2
  echo "(If the milestone spans several commits, rerun with --base REF.)" >&2
  diff=$(get_diff "$base")
fi
for rel in ${include_files[@]+"${include_files[@]}"}; do
  extra=$(git diff --no-color --no-index -- /dev/null "$rel" || true)
  diff="${diff:+$diff
}$extra"
done
if [ -z "$diff" ]; then echo "No changes against $base. Nothing to review."; exit 0; fi
max=60000
if [ "${#diff}" -gt "$max" ]; then
  echo "WARNING: diff is ${#diff} chars; truncating to $max." >&2
  diff="${diff:0:$max}"
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# shellcheck disable=SC2016  # the backticks are literal markdown fences, not a command
printf 'Review this diff. Apply the security-review pass: SSRF, information exposure, and secret handling.\n\n```diff\n%s\n```\n' "$diff" > "$work/user.md"

export REVIEW_META_FILE="$work/meta"
review=$(review_call skills/review-checklist.md "$work/user.md" "$explicit")
read -r used_provider used_model < "$work/meta"

mkdir -p .reviews
out=".reviews/review-$(date +%Y%m%d-%H%M%S)-$used_provider.md"
printf '# Cross-model review (%s, %s, base %s)\n\n%s\n' "$used_provider" "$used_model" "$base" "$review" > "$out"
printf '%s\n\nSaved to %s\n' "$review" "$out"
