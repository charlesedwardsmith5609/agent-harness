"""Turn recurring audit-log failures into candidate eval cases.

Reads a JSONL audit log, keeps the last row for each run, and groups four
failure classes once they recur:

- injection hold (`data.comment_blocked`)
- schema violation (`data.evidence_error`)
- retries exhausted (lifecycle ended `failed` after a retry, or an effect hit its cap)
- gate block (a successful run refused by the confidence or cost gate)

Candidates are written for a person to review. They are not added to
`evals/cases/` or `evals/baseline.json`.

Usage: python -m evals.mine_failures --audit-log PATH --out evals/cases_mined/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MIN_OCCURRENCES = 2
# First match wins when one row fits more than one class.
FAILURE_KINDS = (
    "injection-hold",
    "schema-violation",
    "retries-exhausted",
    "gate-block",
)
# Whole values: model reasoning and anything that could be posted as a PR comment.
_COMMENT_FIELDS = frozenset(
    {
        "reasoning",
        "summary_for_pr_comment",
        "body",
        "comment",
        "comment_blocked",
        "pr_comment",
    }
)
# Finding text is copied into the PR comment by format_github_comment.
_FINDING_TEXT = frozenset({"title", "description", "suggestion"})
# Identifier containing key/token/secret/password, then = or :, then a quoted or bare value.
_SECRET_NAME = r"[\w.-]*(?:api[_-]?key|token|secret|password)[\w.-]*"
_QUOTED_SECRET = re.compile(
    rf"(?i)({_SECRET_NAME}\s*[\"']?\s*[=:]\s*)([\"'])(.*?)(\2)",
)
_BARE_SECRET = re.compile(
    rf"(?i)({_SECRET_NAME}\s*[=:]\s*)([^\s,}}\]]+)",
)
_TOKENS = re.compile(r"AKIA[0-9A-Z]{16}|(?:sk-ant-|ghp_|github_pat_)[A-Za-z0-9_\-]+")


def read_audit_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, skipped). Blank lines are ignored. Bad lines are counted and skipped."""
    rows: list[dict[str, Any]] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(item, dict):
            skipped += 1
            continue
        rows.append(item)
    return rows, skipped


def collapse_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the last row for each (agent_name, run_id). Earlier snapshots must not count twice."""
    order: list[tuple[str, str]] = []
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("agent_name") or ""), str(row.get("run_id") or ""))
        if key not in latest:
            order.append(key)
        latest[key] = row
    return [latest[key] for key in order]


def classify(record: dict[str, Any]) -> str | None:
    """Return the failure class for one final audit row, or None when it is not a mined failure."""
    if _injection_hold(record):
        return "injection-hold"
    if _schema_violation(record):
        return "schema-violation"
    if _retries_exhausted(record):
        return "retries-exhausted"
    if _gate_block(record):
        return "gate-block"
    return None


def redact(value: Any, *, key: str | None = None, in_findings: bool = False) -> Any:
    """Drop comment text and scrub secrets. Comment fields are replaced, not partially kept."""
    if key in _COMMENT_FIELDS or (in_findings and key in _FINDING_TEXT):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(name): redact(item, key=str(name), in_findings=in_findings) for name, item in value.items()
        }
    if isinstance(value, list):
        nested = in_findings or key == "findings"
        return [redact(item, in_findings=nested) for item in value]
    if isinstance(value, str):
        return _scrub_secrets(value)
    return value


def mine(audit_log: Path, out: Path) -> tuple[list[Path], int]:
    """Write one candidate file per recurring failure class. Returns (paths, skipped lines)."""
    rows, skipped = read_audit_rows(audit_log)
    grouped: dict[str, list[dict[str, Any]]] = {kind: [] for kind in FAILURE_KINDS}
    for record in collapse_runs(rows):
        if _is_held_out_record(record):
            continue
        kind = classify(record)
        if kind is not None:
            grouped[kind].append(record)

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for kind in FAILURE_KINDS:
        path = out / f"{kind}.json"
        items = grouped[kind]
        if len(items) < MIN_OCCURRENCES:
            if path.exists():
                path.unlink()
            continue
        case = {
            "id": kind,
            "failure_kind": kind,
            "occurrences": len(items),
            "run_ids": sorted({str(item.get("run_id") or "") for item in items}),
            "redacted_example": redact(items[-1]),
            "review_status": "candidate",
        }
        path.write_text(json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mine recurring audit failures into candidate eval cases")
    parser.add_argument("--audit-log", required=True, help="JSONL audit log to read")
    parser.add_argument("--out", required=True, help="Directory for candidate cases (not the baseline)")
    args = parser.parse_args(argv)
    path = Path(args.audit_log)
    if not path.is_file():
        print(f"audit log not found: {path}", file=sys.stderr)
        return 1
    written, skipped = mine(path, Path(args.out))
    if skipped:
        print(f"skipped {skipped} malformed audit line(s)", file=sys.stderr)
    for item in written:
        print(item)
    return 0


def _is_held_out_record(record: dict[str, Any]) -> bool:
    """Held-out eval rows must not become tuning or mining output."""
    if record.get("split") == "held_out" or record.get("held_out") is True:
        return True
    data = record.get("data")
    if isinstance(data, dict) and (data.get("split") == "held_out" or data.get("held_out") is True):
        return True
    return False


def _injection_hold(record: dict[str, Any]) -> bool:
    data = record.get("data")
    if not isinstance(data, dict):
        return False
    blocked = data.get("comment_blocked")
    return isinstance(blocked, str) and bool(blocked.strip())


def _schema_violation(record: dict[str, Any]) -> bool:
    data = record.get("data")
    if not isinstance(data, dict):
        return False
    error = data.get("evidence_error")
    return isinstance(error, str) and bool(error.strip())


def _retries_exhausted(record: dict[str, Any]) -> bool:
    life = record.get("lifecycle")
    if not isinstance(life, dict) or life.get("outcome") != "failed":
        return False
    history = life.get("history")
    events: list[str] = []
    if isinstance(history, list):
        for step in history:
            if isinstance(step, dict) and isinstance(step.get("event"), str):
                events.append(step["event"])
    attempts = life.get("effect_attempts")
    try:
        effect_cap = int(life.get("max_effect_attempts", 2))
    except (TypeError, ValueError):
        effect_cap = 2
    if isinstance(attempts, dict):
        for value in attempts.values():
            try:
                used = int(value)
            except (TypeError, ValueError):
                continue
            if used >= effect_cap:
                return True
    # A later non-retryable error is not "retries exhausted" unless the model cap was hit.
    if "fail_retryable" in events and "fail" in events:
        try:
            used_attempts = int(life.get("model_attempt") or 0)
            model_cap = int(life.get("max_attempts") or 2)
        except (TypeError, ValueError):
            return False
        return used_attempts >= model_cap
    return False


def _gate_block(record: dict[str, Any]) -> bool:
    """Confidence or cost refusal of a successful model call. Dry runs and crashes are not gate blocks."""
    if record.get("success") is not True or record.get("gate_passed") is not False:
        return False
    reason = record.get("gate_reason")
    if not isinstance(reason, str):
        return False
    return reason.startswith("Confidence ") or reason.startswith("Cost $")


def _scrub_secrets(text: str) -> str:
    scrubbed = _QUOTED_SECRET.sub(r"\1\2[REDACTED]\2", text)
    scrubbed = _BARE_SECRET.sub(r"\1[REDACTED]", scrubbed)
    return _TOKENS.sub("[REDACTED]", scrubbed)


if __name__ == "__main__":
    raise SystemExit(main())
