"""Compare a live (or fake) summary against the checked-in eval baseline floors."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

BASELINE_PATH = Path(__file__).parent / "baseline.json"
# Label for floors that no model produced (the checked-in offline floors).
OFFLINE_MODEL = "offline"


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def case_regressions(rows: list[dict[str, Any]], baseline: dict[str, Any]) -> list[str]:
    """Compare each scored row with baseline.json `per_case`.

    Held-out rows are reported here. Callers that tune or mine must drop them first.
    A scored row with no per_case entry is not a regression. A per_case entry marked
    `held_out` with no scored row is: a held-out case that was not scored must fail.
    """
    expected = baseline.get("per_case") or {}
    if not isinstance(expected, dict):
        return ["baseline per_case is not an object"]
    problems: list[str] = []
    scored = {str(row.get("id") or "") for row in rows}
    for case_id, spec in expected.items():
        if isinstance(spec, dict) and spec.get("held_out") is True and case_id not in scored:
            problems.append(f"{case_id} is held out in the baseline but has no scored row")
    for row in rows:
        case_id = str(row.get("id") or "")
        spec = expected.get(case_id)
        if not isinstance(spec, dict):
            continue
        if "gate_exact" in spec and bool(row.get("gate_exact")) != bool(spec["gate_exact"]):
            problems.append(f"{case_id} gate_exact={bool(row.get('gate_exact'))} expected {bool(spec['gate_exact'])}")
    return problems


def regressions(summary: dict[str, float], baseline: dict[str, Any]) -> list[str]:
    """Return human-readable regressions against floors/ceilings. Empty means pass."""
    problems: list[str] = []
    floors = dict(baseline.get("floors") or {})
    ceilings = dict(baseline.get("ceilings") or {})
    for key, floor in floors.items():
        value = float(summary.get(key, 0.0))
        if value + 1e-12 < float(floor):
            problems.append(f"{key}={value:.3f} below floor {float(floor):.3f}")
    for key, ceiling in ceilings.items():
        value = float(summary.get(key, 0.0))
        if value > float(ceiling) + 1e-12:
            problems.append(f"{key}={value:.3f} above ceiling {float(ceiling):.3f}")
    return problems


def strict_pairing() -> bool:
    """PAIRING_STRICT=1 turns a model mismatch from a warning into a failure."""
    return os.environ.get("PAIRING_STRICT") == "1"


def model_pairing(baseline: dict[str, Any], run_model: str | None, *, strict: bool) -> tuple[list[str], list[str]]:
    """Compare the model a baseline was measured on with the model of this run.

    Returns (warnings, problems). A mismatch is a warning, and a problem under strict.
    A baseline or run with no model is only a warning, strict or not: an old baseline
    must still read. Pure: it does not read the environment.
    """
    base_model = baseline.get("model")
    if not isinstance(base_model, str) or not base_model.strip():
        return ["baseline has no model field; the model it was measured on is unknown"], []
    if not isinstance(run_model, str) or not run_model.strip():
        return ["run has no model field; cannot compare it with the baseline"], []
    if base_model == run_model:
        return [], []
    text = f"baseline measured on {base_model!r}, this run used {run_model!r}; re-measure after a model change"
    return ([], [text]) if strict else ([text], [])


def main() -> int:
    ap = argparse.ArgumentParser(description="Check eval summary against evals/baseline.json")
    ap.add_argument(
        "--summary", required=True, help="Path to JSON with a top-level summary object, or the summary itself"
    )
    args = ap.parse_args()
    payload = json.loads(Path(args.summary).read_text(encoding="utf-8"))
    summary = payload.get("summary", payload)
    baseline = load_baseline()
    problems = regressions(summary, baseline)
    run_model = payload.get("model") if isinstance(payload, dict) else None
    warnings, model_problems = model_pairing(baseline, run_model, strict=strict_pairing())
    for warning in warnings:
        print(f"warning: {warning}")
    problems += model_problems
    if problems:
        print("baseline regression:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("baseline ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
