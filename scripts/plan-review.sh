#!/usr/bin/env bash
# Plan review: send a plan, the agreed requirements and the working agreements to a different AI
# provider for an independent review before any code is written.
# Usage: bash scripts/plan-review.sh [plans/step-N.md] [--provider cursor|openai|anthropic|auto] [--model NAME]
#        With no plan file, reviews the highest-numbered plans/step-*.md.
# Env:   REVIEW_PROVIDER, CURSOR_REVIEW_MODEL, OPENAI_API_KEY / ANTHROPIC_API_KEY, REVIEW_MODEL,
#        OPENAI_BASE_URL, ANTHROPIC_BASE_URL. See scripts/review-call.sh for how a provider is chosen.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/review-call.sh

provider="${REVIEW_PROVIDER:-auto}"
model=""
explicit=0
plan=""
while [ $# -gt 0 ]; do
  case "$1" in
    --provider) provider="$2"; explicit=1; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    -*) echo "unknown argument: $1" >&2; exit 2 ;;
    *) plan="$1"; shift ;;
  esac
done
review_resolve "$provider" "$model" || exit 2

if [ -z "$plan" ]; then
  # shellcheck disable=SC2012  # plan names are plans/step-N.md, never odd characters
  plan=$(ls plans/step-*.md 2>/dev/null | sort -V | tail -n 1 || true)
  [ -n "$plan" ] || { echo "No plan found. Write the plan to plans/step-N.md first." >&2; exit 2; }
fi
[ -f "$plan" ] || { echo "Plan file not found: $plan" >&2; exit 2; }
[ -f REQUIREMENTS.md ] || { echo "REQUIREMENTS.md not found. Write the agreed requirements there first; the reviewer checks the plan against them." >&2; exit 2; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
python3 - "$plan" "$work/user.md" <<'PY'
import glob, re, sys

def read(path, limit=None):
    text = open(path, encoding="utf-8").read()
    if limit and len(text) > limit:
        text = text[:limit] + "\n[truncated]\n"
    return text

def step_no(path):
    m = re.search(r"step-(\d+)", path)
    return int(m.group(1)) if m else 0

plan, out = sys.argv[1], sys.argv[2]
parts = [
    "Agreed requirements (REQUIREMENTS.md):\n\n" + read("REQUIREMENTS.md", 20000),
    "Working agreements (CLAUDE.md):\n\n" + read("CLAUDE.md", 20000),
]
earlier = sorted((p for p in glob.glob("plans/step-*.md") if step_no(p) < step_no(plan)), key=step_no)
if earlier:
    text = "\n\n".join("--- " + p + " ---\n" + read(p, 8000) for p in earlier)
    if len(text) > 20000:
        text = "[older plans omitted]\n" + text[-20000:]
    parts.append("Earlier approved plans, for context only:\n\n" + text)
parts.append("Plan to review (" + plan + "):\n\n" + read(plan, 30000))
open(out, "w", encoding="utf-8").write("\n\n==========\n\n".join(parts))
PY

echo "Plan review of $plan" >&2
export REVIEW_META_FILE="$work/meta"
review=$(review_call skills/plan-review-checklist.md "$work/user.md" "$explicit")
read -r used_provider used_model < "$work/meta"

mkdir -p .reviews
name=$(basename "$plan" .md)
out=".reviews/plan-review-$name-$(date +%Y%m%d-%H%M%S)-$used_provider.md"
printf '# Plan review of %s (%s, %s)\n\n%s\n' "$plan" "$used_provider" "$used_model" "$review" > "$out"
printf '%s\n\nSaved to %s\n' "$review" "$out"
