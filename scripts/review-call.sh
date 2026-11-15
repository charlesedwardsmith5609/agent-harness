# shellcheck shell=bash
# Shared by plan-review.sh and cross-review.sh: send a review request to the chosen provider.
# Source this file, then call:  review_call SYSTEM_FILE USER_FILE
# It prints the review on stdout and sets REVIEW_USED_PROVIDER and REVIEW_USED_MODEL.
#
# Providers:
#   cursor     Cursor's CLI agent (`agent -p --mode=ask`), billed to your Cursor plan.
#              Model from --model or CURSOR_REVIEW_MODEL (set it with: setreviewer cursor).
#   openai     OpenAI API with OPENAI_API_KEY. Model from --model or REVIEW_MODEL.
#   anthropic  Anthropic API with ANTHROPIC_API_KEY. Model from --model or REVIEW_MODEL.
#   auto       (default) cursor if it is configured, else openai, else anthropic.
# If cursor was chosen by REVIEW_PROVIDER or auto (not by --provider) and the call fails, the
# review falls back to openai when OPENAI_API_KEY is set.
#
# Before anything is sent, the request text is scanned with gitleaks (see review_scan_secrets).
# If the scan cannot run or fails, nothing is sent.

# Read the saved reviewer settings on every run, so a terminal opened before setreviewer or setkey
# changed them can't send a stale model or key. --provider and --model still override them.
_review_env="${PAIRING_KIT_ENV:-$HOME/.config/pairing-kit.env}"
# shellcheck source=/dev/null
[ -f "$_review_env" ] && . "$_review_env"

REVIEW_CURSOR_TIMEOUT="${REVIEW_CURSOR_TIMEOUT:-480}"
REVIEW_MAX_PROMPT_BYTES="${REVIEW_MAX_PROMPT_BYTES:-120000}"

_review_cursor_bin() {
  if [ -n "${CURSOR_AGENT_BIN:-}" ]; then echo "$CURSOR_AGENT_BIN"
  elif command -v agent >/dev/null 2>&1; then echo agent
  elif command -v cursor-agent >/dev/null 2>&1; then echo cursor-agent
  fi
}

# Resolve provider and model. $1 = provider (may be empty or auto), $2 = model (may be empty).
review_resolve() {
  local provider="${1:-auto}" model="${2:-}"
  if [ "$provider" = auto ]; then
    if [ -n "$(_review_cursor_bin)" ] && [ -n "${CURSOR_REVIEW_MODEL:-}" ]; then provider=cursor
    elif [ -n "${OPENAI_API_KEY:-}" ]; then provider=openai
    elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then provider=anthropic
    else
      echo "No reviewer configured. Run 'setreviewer cursor' (uses your Cursor plan) or 'setkey' (OpenAI API key)." >&2
      return 2
    fi
  fi
  case "$provider" in
    cursor) model="${model:-${CURSOR_REVIEW_MODEL:-}}" ;;
    openai) model="${model:-${REVIEW_MODEL:-gpt-6.1-sol}}" ;;
    anthropic) model="${model:-claude-sonnet-5-5}" ;;
    *) echo "provider must be cursor, openai, anthropic or auto" >&2; return 2 ;;
  esac
  REVIEW_USED_PROVIDER="$provider" REVIEW_USED_MODEL="$model"
}

_review_cursor() {
  local system_file="$1" user_file="$2" bin prompt out rc
  bin=$(_review_cursor_bin)
  [ -n "$bin" ] || { echo "Cursor CLI not found. Install it: curl https://cursor.com/install -fsS | bash" >&2; return 1; }
  [ -n "$REVIEW_USED_MODEL" ] || { echo "No Cursor review model set. Run: setreviewer cursor" >&2; return 1; }
  case "$(printf '%s' "$REVIEW_USED_MODEL" | tr '[:upper:]' '[:lower:]')" in
    *claude*|*sonnet*|*opus*|*haiku*)
      echo "WARNING: $REVIEW_USED_MODEL is an Anthropic model, the same family as your coding agent. Pick a different provider's model for an independent review." >&2 ;;
  esac
  prompt=$(
    printf 'You are an independent reviewer. Do not modify any files and do not run commands. You may read files in this repository for context. Reply with the review only.\n\n'
    cat "$system_file"
    printf '\n\n==========\n\n'
    cat "$user_file"
  )
  if [ "${#prompt}" -gt "$REVIEW_MAX_PROMPT_BYTES" ]; then
    echo "WARNING: review input is ${#prompt} chars; truncating to $REVIEW_MAX_PROMPT_BYTES." >&2
    prompt="${prompt:0:$REVIEW_MAX_PROMPT_BYTES}
[truncated]"
  fi
  local trust=()
  [ "${CURSOR_REVIEW_TRUST:-0}" = 1 ] && trust=(--trust)
  local errf said
  errf=$(mktemp)
  rc=0
  out=$(timeout -k 5 "$REVIEW_CURSOR_TIMEOUT" "$bin" -p --mode=ask --output-format text \
    "${trust[@]}" --model "$REVIEW_USED_MODEL" "$prompt" </dev/null 2>"$errf") || rc=$?
  said="$(cat "$errf")"; rm -f "$errf"
  if [ "$rc" -ne 0 ] || [ -z "$(printf '%s' "$out" | tr -d '[:space:]')" ]; then
    [ "$rc" = 124 ] && echo "Cursor review timed out after ${REVIEW_CURSOR_TIMEOUT}s." >&2
    echo "Cursor review failed (exit $rc). Cursor said:" >&2
    printf '%s\n%s\n' "$said" "$out" | grep -v '^[[:space:]]*$' | tail -n 5 >&2
    if printf '%s %s' "$said" "$out" | grep -qi 'trust'; then
      echo "Fix: trust your projects folder once: cd $(dirname "$PWD") && $bin  (accept, then exit)." >&2
      echo "Or set CURSOR_REVIEW_TRUST=1 to pass --trust on every review." >&2
    else
      echo "Check: $bin status   and   $bin models" >&2
    fi
    return 1
  fi
  printf '%s\n' "$out"
}

_review_api() {
  local system_file="$1" user_file="$2" key url body resp
  case "$REVIEW_USED_PROVIDER" in
    openai) key="${OPENAI_API_KEY:-}"; url="${OPENAI_BASE_URL:-https://api.openai.com}/v1/chat/completions" ;;
    anthropic) key="${ANTHROPIC_API_KEY:-}"; url="${ANTHROPIC_BASE_URL:-https://api.anthropic.com}/v1/messages" ;;
  esac
  [ -n "$key" ] || { echo "missing API key for $REVIEW_USED_PROVIDER" >&2; return 2; }
  body=$(python3 - "$system_file" "$user_file" "$REVIEW_USED_PROVIDER" "$REVIEW_USED_MODEL" <<'PY'
import json, sys
system = open(sys.argv[1], encoding="utf-8").read()
user = open(sys.argv[2], encoding="utf-8").read()
provider, model = sys.argv[3], sys.argv[4]
if provider == "openai":
    req = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
else:
    req = {"model": model, "max_tokens": 2000, "system": system, "messages": [{"role": "user", "content": user}]}
print(json.dumps(req))
PY
)
  if [ "$REVIEW_USED_PROVIDER" = openai ]; then
    resp=$(printf '%s' "$body" | curl -sS --connect-timeout 15 --max-time "${REVIEW_CURL_MAX_TIME:-300}" "$url" -H "Authorization: Bearer $key" -H "Content-Type: application/json" --data-binary @-)
  else
    resp=$(printf '%s' "$body" | curl -sS --connect-timeout 15 --max-time "${REVIEW_CURL_MAX_TIME:-300}" "$url" -H "x-api-key: $key" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" --data-binary @-)
  fi
  REVIEW_RESP="$resp" python3 - <<'PY'
import json, os, sys
try:
    r = json.loads(os.environ["REVIEW_RESP"])
except ValueError:
    sys.exit("API returned a non-JSON response")
if "error" in r:
    sys.exit("API error: " + json.dumps(r["error"]))
if "choices" in r:
    print(r["choices"][0]["message"]["content"])
else:
    print("".join(b.get("text", "") for b in r.get("content", [])))
PY
}

# Record which provider and model actually answered. review_call usually runs inside $(...), so
# callers read this file (REVIEW_META_FILE) rather than variables.
_review_meta() { [ -z "${REVIEW_META_FILE:-}" ] || printf '%s %s\n' "$REVIEW_USED_PROVIDER" "$REVIEW_USED_MODEL" > "$REVIEW_META_FILE"; }

# Run gitleaks on FILE. Prints the tool's output.
# Returns 0 when the text is clean, 7 when it matches, 127 when gitleaks is not installed,
# or gitleaks' own exit code when the scanner fails.
# `gitleaks stdin` is the current form; older 8.x releases only have `detect --pipe`.
review_gitleaks_scan() {
  local file="$1" out rc=0
  if ! command -v gitleaks >/dev/null 2>&1; then
    return 127
  fi
  if gitleaks stdin --help 2>&1 | grep -q 'gitleaks stdin'; then
    out=$(gitleaks stdin --no-banner --no-color --redact --verbose --exit-code 7 < "$file" 2>&1) || rc=$?
  else
    out=$(gitleaks detect --pipe --no-banner --no-color --redact --verbose --exit-code 7 < "$file" 2>&1) || rc=$?
  fi
  printf '%s\n' "$out"
  return "$rc"
}

# Scan the text about to leave this machine (the diff, or the plan plus requirements) for secrets.
# Fails closed: a finding, a missing gitleaks, or a scanner failure all refuse the review and
# nothing is sent (install: https://github.com/gitleaks/gitleaks).
# Exit code 7 is ours: it tells "secrets found" apart from gitleaks failing for another reason.
review_scan_secrets() {
  local file="$1" out rc=0
  out=$(review_gitleaks_scan "$file") || rc=$?
  if [ "$rc" -eq 127 ]; then
    echo "gitleaks is not installed, so the secret scan cannot run. Nothing was sent." >&2
    return 1
  fi
  [ "$rc" -eq 0 ] && return 0
  printf '%s\n' "$out" | tail -n 25 >&2
  if [ "$rc" -eq 7 ]; then
    echo "Secret scan found possible secrets (redacted above). Nothing was sent. Remove them from the change and rerun." >&2
  else
    echo "Secret scan failed (gitleaks exit $rc). Nothing was sent." >&2
  fi
  return 1
}

# review_call SYSTEM_FILE USER_FILE [explicit]   explicit=1 means --provider was given (no fallback)
review_call() {
  local system_file="$1" user_file="$2" explicit="${3:-0}"
  review_scan_secrets "$user_file" || return 1
  echo "Requesting review from $REVIEW_USED_PROVIDER ($REVIEW_USED_MODEL)..." >&2
  if [ "$REVIEW_USED_PROVIDER" = cursor ]; then
    if _review_cursor "$system_file" "$user_file"; then _review_meta; return 0; fi
    if [ "$explicit" = 1 ] || [ -z "${OPENAI_API_KEY:-}" ]; then return 1; fi
    REVIEW_USED_PROVIDER=openai REVIEW_USED_MODEL="${REVIEW_MODEL:-gpt-6.1-sol}"
    case "$REVIEW_USED_MODEL" in gpt-*|o[0-9]*) ;; *) REVIEW_USED_MODEL=gpt-6.1-sol ;; esac
    echo "Falling back to openai ($REVIEW_USED_MODEL)..." >&2
  fi
  _review_api "$system_file" "$user_file" || return 1
  _review_meta
}
