"""Durable snapshots of RunMachine state, keyed by run_id.

A crash mid-gated or mid-approved must not invent a new lifecycle. Callers
save after every transition and load before accepting further events.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from harness.lifecycle import RunMachine


class LifecycleStore:
    """JSON files under a directory. One file per run_id; writes are atomic replace."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in run_id)
        if not safe:
            raise ValueError("run_id is required")
        return self.root / f"{safe}.json"

    def save(self, machine: RunMachine) -> Path:
        path = self.path_for(machine.run_id)
        payload = json.dumps(machine.to_dict(), indent=2, sort_keys=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return path

    def load(self, run_id: str) -> RunMachine | None:
        path = self.path_for(run_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"lifecycle snapshot for {run_id!r} is not an object")
        return RunMachine.from_dict(data)

    def exists(self, run_id: str) -> bool:
        return self.path_for(run_id).exists()
