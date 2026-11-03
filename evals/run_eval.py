"""Offline-scorable eval for the PR reviewer.

Usage:  python -m evals.run_eval            (needs ANTHROPIC_API_KEY, costs a few cents)
        python -m evals.run_eval --json     (machine-readable)

Each case in evals/cases/*.json holds a seeded-bug (or clean) diff, the gate the
reviewer should return, and the finding category it should report.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).parent / "cases"
BLOCKING = {"BLOCK", "REQUEST_CHANGES"}


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(CASES_DIR.glob("*.json"))]


def score_case(case: dict[str, Any], data: dict[str, Any] | None) -> dict[str, Any]:
    """Score one review. data=None means the run failed closed (counts as a miss)."""
    data = data or {}
    gate = data.get("gate_decision")
    cats = {f.get("category") for f in data.get("findings", [])}
    buggy = case["expected_gate"] in BLOCKING
    flagged = gate in BLOCKING
    return {
        "id": case["id"],
        "buggy": buggy,
        "flagged": flagged,
        "gate_exact": gate == case["expected_gate"],
        "category_hit": bool(buggy and case["expected_category"] in cats),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    tp = sum(r["buggy"] and r["flagged"] for r in rows)
    fp = sum((not r["buggy"]) and r["flagged"] for r in rows)
    fn = sum(r["buggy"] and not r["flagged"] for r in rows)
    buggy = sum(r["buggy"] for r in rows)
    return {
        "cases": len(rows),
        "precision": tp / (tp + fp) if (tp + fp) else 1.0,
        "recall": tp / (tp + fn) if (tp + fn) else 1.0,
        "gate_exact_rate": sum(r["gate_exact"] for r in rows) / len(rows) if rows else 0.0,
        "category_rate": sum(r["category_hit"] for r in rows) / buggy if buggy else 1.0,
    }


def run(review: Callable[[str], dict[str, Any] | None]) -> tuple[list[dict[str, Any]], dict[str, float]]:
    rows = [score_case(c, review(c["diff"])) for c in load_cases()]
    return rows, summarize(rows)


def _live_review(diff: str) -> dict[str, Any] | None:
    from agents.pr_reviewer import review_diff

    result = review_diff(diff)
    return result.data if result.success else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows, summary = run(_live_review)
    if args.json:
        print(json.dumps({"rows": rows, "summary": summary}, indent=2))
    else:
        for r in rows:
            print(
                f"{r['id']:<22} flagged={r['flagged']!s:<5} gate_exact={r['gate_exact']!s:<5} cat={r['category_hit']}"
            )
        print({k: round(v, 2) if isinstance(v, float) else v for k, v in summary.items()})
