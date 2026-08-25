"""
PR Reviewer Agent

Analyses a pull request diff for:
  - Security vulnerabilities (hardcoded secrets, injection risks, insecure patterns)
  - Reliability risks (missing error handling, unbounded retries, race conditions)
  - Observability gaps (missing tracing, inadequate error logging)
  - SLO impact (changes to critical paths, timeout modifications)

Returns structured findings with severity (HIGH / MEDIUM / LOW / INFO)
and a gate decision: APPROVE / REQUEST_CHANGES / BLOCK.

HIGH findings → BLOCK (cannot merge until resolved)
MEDIUM findings → REQUEST_CHANGES (human review required)
LOW/INFO → APPROVE with comments
"""

from __future__ import annotations
import os
import sys
import json
import subprocess
from typing import Optional

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from harness.agent import AgentHarness, RunConfig, AgentResult


SYSTEM_PROMPT = """\
You are a production platform reliability and security engineer reviewing a pull request diff.

Your job is to catch issues BEFORE they reach production — security vulnerabilities, reliability
risks, observability gaps, and changes that could affect SLO targets.

You MUST respond with valid JSON only. No prose, no markdown, no explanation outside the JSON.

Required output schema:
{
  "confidence": <float 0.0–1.0, how confident you are in this review>,
  "reasoning": <string, 1–3 sentence summary of your overall assessment>,
  "gate_decision": <"APPROVE" | "REQUEST_CHANGES" | "BLOCK">,
  "findings": [
    {
      "severity": <"HIGH" | "MEDIUM" | "LOW" | "INFO">,
      "category": <"security" | "reliability" | "observability" | "slo_impact" | "style">,
      "file": <filename or "general">,
      "line": <line number or null>,
      "title": <short title, max 80 chars>,
      "description": <what is wrong and why it matters>,
      "suggestion": <concrete fix or mitigation>
    }
  ],
  "slo_risk": <"NONE" | "LOW" | "MEDIUM" | "HIGH">,
  "security_risk": <"NONE" | "LOW" | "MEDIUM" | "HIGH">,
  "summary_for_pr_comment": <markdown string suitable for posting as a GitHub PR comment>
}

Gate decision rules:
- BLOCK if any finding has severity HIGH
- REQUEST_CHANGES if any finding has severity MEDIUM and no HIGH
- APPROVE if all findings are LOW or INFO

Be precise. Only flag real issues, not style preferences. If the diff looks clean, say so with
confidence and return an empty findings list.

Security patterns to check:
  - Hardcoded secrets, API keys, passwords, tokens
  - SQL/command injection risks
  - Insecure cryptography (MD5, SHA1 for security purposes, ECB mode)
  - Missing input validation on user-controlled data
  - Overly permissive IAM policies or RBAC rules

Reliability patterns to check:
  - Missing error handling for I/O, network, or database operations
  - Unbounded retry loops without backoff or circuit breaker
  - Missing timeout on external calls
  - Race conditions in concurrent code
  - Resource leaks (unclosed files, connections, goroutines)

Observability gaps:
  - Missing structured logging on error paths
  - No tracing on cross-service calls
  - Silent error swallowing (bare `except: pass` or `catch {}`)

SLO impact:
  - Changes to timeout values or retry counts on critical paths
  - Modifications to health check endpoints
  - Changes that affect request routing or load balancing
"""


def fetch_pr_diff(pr_number: int, repo: str, token: str) -> str:
    """Fetch the unified diff for a GitHub PR."""
    import urllib.request
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.diff",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8")


def post_pr_comment(
    pr_number: int,
    repo: str,
    token: str,
    body: str,
) -> None:
    """Post a comment on a GitHub PR."""
    import urllib.request
    import urllib.parse

    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    data = json.dumps({"body": body}).encode()
    req  = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"Failed to post PR comment: {resp.status}")


def format_github_comment(result: AgentResult, pr_number: int) -> str:
    """Format the agent result as a GitHub PR comment."""
    if not result.passed():
        return (
            f"## 🤖 Agent PR Review — ⚠️ Gate failed\n\n"
            f"The review agent could not complete successfully.\n\n"
            f"**Reason:** {result.gate_reason or result.reasoning}\n\n"
            f"*Manual review required before merging.*"
        )

    data     = result.data
    decision = data.get("gate_decision", "APPROVE")
    findings = data.get("findings", [])

    emoji = {"APPROVE": "✅", "REQUEST_CHANGES": "⚠️", "BLOCK": "🚫"}.get(decision, "❓")

    # Use the agent's pre-formatted comment if available
    if "summary_for_pr_comment" in data:
        body = data["summary_for_pr_comment"]
    else:
        lines = [
            f"## 🤖 Agent PR Review — {emoji} {decision}\n",
            f"*Confidence: {result.confidence:.0%} · Cost: ${result.cost_usd:.4f} · "
            f"Run: `{result.run_id}`*\n",
            f"> {data.get('reasoning', '')}\n",
        ]
        if findings:
            lines.append("\n### Findings\n")
            for f in findings:
                sev_emoji = {"HIGH": "🚨", "MEDIUM": "⚠️", "LOW": "💡", "INFO": "ℹ️"}.get(
                    f.get("severity", ""), "•"
                )
                lines.append(
                    f"**{sev_emoji} [{f['severity']}] {f['title']}**  \n"
                    f"`{f.get('file', 'general')}`"
                    + (f" line {f['line']}" if f.get("line") else "")
                    + f"\n{f['description']}\n\n"
                    f"*Suggestion:* {f['suggestion']}\n"
                )
        else:
            lines.append("\n*No issues found. Diff looks clean.*\n")
        body = "\n".join(lines)

    return body


def review_pr(
    pr_number:  int,
    repo:       str,
    token:      Optional[str] = None,
    api_key:    Optional[str] = None,
    post_comment: bool = True,
) -> AgentResult:
    """
    Run the PR review agent on a pull request.

    Args:
        pr_number:    GitHub PR number to review.
        repo:         GitHub repo in "owner/name" format.
        token:        GitHub token (defaults to GITHUB_TOKEN env var).
        api_key:      Anthropic API key (defaults to ANTHROPIC_API_KEY env var).
        post_comment: Whether to post findings as a PR comment.

    Returns:
        AgentResult with structured findings and gate decision.
    """
    token   = token   or os.environ.get("GITHUB_TOKEN", "")
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    config = RunConfig(
        agent_name="pr-reviewer",
        model="claude-sonnet-4-6",
        max_tokens=2048,
        temperature=0.2,
        min_confidence=0.65,
        max_cost_usd=0.30,
        audit_log_path=os.environ.get("AGENT_AUDIT_LOG"),
    )
    harness = AgentHarness(config=config, api_key=api_key)

    # Fetch the diff
    diff = fetch_pr_diff(pr_number=pr_number, repo=repo, token=token)

    # Truncate very large diffs to stay within context limits
    MAX_DIFF_CHARS = 40_000
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n[... diff truncated for length ...]"

    user_message = (
        f"Repository: {repo}\n"
        f"PR: #{pr_number}\n\n"
        f"Diff:\n```diff\n{diff}\n```"
    )

    result = harness.run(
        system_prompt=SYSTEM_PROMPT,
        user_message=user_message,
        context={"github.repo": repo, "github.pr_number": pr_number},
    )

    if post_comment and token:
        comment = format_github_comment(result, pr_number)
        post_pr_comment(pr_number=pr_number, repo=repo, token=token, body=comment)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run PR review agent")
    parser.add_argument("--pr",   type=int, required=True, help="PR number")
    parser.add_argument("--repo", required=True, help="owner/repo")
    parser.add_argument("--no-comment", action="store_true", help="Skip posting PR comment")
    args = parser.parse_args()

    result = review_pr(
        pr_number=args.pr,
        repo=args.repo,
        post_comment=not args.no_comment,
    )

    print(json.dumps(result.to_dict(), indent=2))

    # Exit code drives CI gate:
    # 0 = approved, 1 = changes requested, 2 = blocked, 3 = agent failure
    decision = result.data.get("gate_decision", "") if result.success else ""
    sys.exit({"APPROVE": 0, "REQUEST_CHANGES": 1, "BLOCK": 2}.get(decision, 3))
