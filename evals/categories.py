"""Per-category rates for eval rows. Reporting only; floors stay in baseline.json."""

from __future__ import annotations

from typing import Any


def rates_by_category(rows: list[dict[str, Any]], hit_key: str) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = str(row.get("category") or "uncategorized")
        buckets.setdefault(name, []).append(row)
    report: dict[str, dict[str, float | int]] = {}
    for name in sorted(buckets):
        items = buckets[name]
        hits = sum(1 for item in items if item.get(hit_key))
        report[name] = {"cases": len(items), "rate": hits / len(items) if items else 0.0}
    return report
