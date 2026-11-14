"""Compare a live (or fake) summary against the checked-in eval baseline floors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

BASELINE_PATH = Path(__file__).parent / "baseline.json"


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def case_regressions(rows: list[dict[str, Any]], baseline: dict[str, Any]) -> list[str]:
    """Compare each scored row with baseline.json `per_case`.

    Held-out rows are reported here. Callers that tune or mine must drop them first.
    A missing per_case entry is not a regression.
    """
    expected = baseline.get("per_case") or {}
    if not isinstance(expected, dict):
        return ["baseline per_case is not an object"]
    problems: list[str] = []
    for row in rows:
        case_id = str(row.get("id") or "")
        spec = expected.get(case_id)
        if not isinstance(spec, dict):
            continue
        if "gate_exact" in spec and bool(row.get("gate_exact")) != bool(spec["gate_exact"]):
            problems.append(
                f"{case_id} gate_exact={bool(row.get('gate_exact'))} expected {bool(spec['gate_exact'])}"
            )
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
    if problems:
        print("baseline regression:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("baseline ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
