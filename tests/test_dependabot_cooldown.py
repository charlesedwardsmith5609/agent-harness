"""Every Dependabot ecosystem waits before proposing a version update.

A text check: the repo declares no YAML parser. It reads .github/dependabot.yml, splits it
into `- package-ecosystem:` blocks, and looks for `cooldown:` with `default-days: 7` under it.
"""

import re
from pathlib import Path

DEPENDABOT = Path(__file__).resolve().parent.parent / ".github" / "dependabot.yml"
EXPECTED_ECOSYSTEMS = ["pip", "github-actions"]
COOLDOWN_DAYS = 7


def _blocks() -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in DEPENDABOT.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*-\s+package-ecosystem:\s*(\S+)\s*$", line)
        if match:
            current = blocks.setdefault(match.group(1), [])
        elif current is not None:
            current.append(line)
    return blocks


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def test_exactly_the_expected_ecosystems_are_configured():
    assert sorted(_blocks()) == sorted(EXPECTED_ECOSYSTEMS)


def test_each_ecosystem_has_a_cooldown_of_seven_default_days():
    for ecosystem, lines in _blocks().items():
        starts = [i for i, line in enumerate(lines) if line.strip() == "cooldown:"]
        assert len(starts) == 1, f"{ecosystem} needs one cooldown block"
        start = starts[0]
        # cooldown must sit beside `directory:` (an entry key), not nested under `schedule:`.
        directory = next(line for line in lines if line.strip().startswith("directory:"))
        assert _indent(lines[start]) == _indent(directory), f"{ecosystem} cooldown is not an entry key"
        child = next(
            (line for line in lines[start + 1 :] if line.strip() and _indent(line) > _indent(lines[start])),
            "",
        )
        assert child.strip() == f"default-days: {COOLDOWN_DAYS}", f"{ecosystem} cooldown is {child.strip()!r}"


def test_github_actions_uses_only_default_days():
    # GitHub documents default-days only for github-actions; the semver-* keys are not supported there.
    lines = _blocks()["github-actions"]
    assert not any("semver-" in line for line in lines)
