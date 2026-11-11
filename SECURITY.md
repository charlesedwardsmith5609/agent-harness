# Security policy

## Reporting

Please report vulnerabilities through a private GitHub security advisory on this repository. Do not open a public issue with exploit details. This is a personal project maintained on a best-effort basis, so response times are not guaranteed.

## Scope

This repository is a reference implementation: fail-closed agent harness with cost and confidence gating, OTel spans, PR reviewer eval, lifecycle approvals, and injection refusal on outbound comments. Review defaults before using it in production. Never commit credentials; secret scanning and dependency updates are enabled where noted in the CI configuration.

## Defense in depth: a sandbox is not a boundary on its own

Agent sandboxes (VMs, containers, Firecracker microVMs) reduce blast radius. They are not a complete trust boundary. A container or microVM escape (for example the class of issues illustrated by Firecracker CVE-2026-122192) means code that ran “inside” the sandbox can reach the host. That is why this harness keeps **gate decisions, lifecycle transitions, cost ceilings, and audit writes in the harness process**, outside any model-controlled tool loop.

### What this harness enforces (outside the model)

- **Fail-closed gates and lifecycle** — confidence and cost gates, plus the run lifecycle (`planned` → `running` → `gated` → `approved` → `done`), decide whether a result may proceed. The model reports evidence; it does not approve its own output or declare completion.
- **Injection refusal on outbound side effects** — PR comments that would copy a known injection canary or “ignore findings / approve” override are refused before the post lands (`harness/injection.py`).
- **Cost and confidence ceilings before side effects** — per-call `max_cost_usd` (pre-call worst-case and post-call actual) and optional `max_cost_usd_per_day` from the audit log block spend before another API call; low confidence fails the gate.
- **Durable audit and lifecycle snapshots** — when `AGENT_AUDIT_LOG` / `audit_log_path` and `AGENT_LIFECYCLE_DIR` / `lifecycle_dir` are set, decisions and phase history persist for accountability and resume.

### What this harness does not provide (operators must add)

These patterns show up in production agent platforms (for example NVIDIA’s guidance on **read-only Kubernetes credentials** and **per-session sandboxes**), but **this repository does not implement them**:

- **Read-only or narrowly scoped credentials** for tools (agents here typically receive full `ANTHROPIC_API_KEY` / `GITHUB_TOKEN` when configured).
- **Per-run VM or container isolation** (no Firecracker/Kata/gVisor wiring in-tree).

Treat a sandbox as one layer. Keep harness gates, lifecycle, and audit **outside** that layer, and issue tools the least privilege they need.
