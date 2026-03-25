"""Track consecutive failed runs per issue in run_counts.json.

Key: "owner/repo#42"
Value: int — number of consecutive runs with no success=True result.
Resets to 0 on any successful run for that issue.

Used by the scheduler (pure Python) to flag issues as needs-human
after STUCK_THRESHOLD consecutive failures — no LLM needed.
"""

from __future__ import annotations

import json
from pathlib import Path

RUN_COUNTS_FILE = Path("run_counts.json")
STUCK_THRESHOLD = 3


def _key(repo: str, issue: int) -> str:
    return f"{repo}#{issue}"


def load_counts(path: Path = RUN_COUNTS_FILE) -> dict[str, int]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_counts(counts: dict[str, int], path: Path = RUN_COUNTS_FILE) -> None:
    path.write_text(json.dumps(counts, indent=2))


def record_result(repo: str, issue: int, success: bool, path: Path = RUN_COUNTS_FILE) -> int:
    """Increment or reset the failure counter. Returns the new count."""
    counts = load_counts(path)
    k = _key(repo, issue)
    if success:
        counts[k] = 0
    else:
        counts[k] = counts.get(k, 0) + 1
    save_counts(counts, path)
    return counts[k]


def is_stuck(repo: str, issue: int, path: Path = RUN_COUNTS_FILE) -> bool:
    counts = load_counts(path)
    return counts.get(_key(repo, issue), 0) >= STUCK_THRESHOLD


def stuck_issues(path: Path = RUN_COUNTS_FILE) -> list[tuple[str, int, int]]:
    """Return list of (repo, issue_number, fail_count) for stuck issues."""
    counts = load_counts(path)
    result = []
    for k, count in counts.items():
        if count >= STUCK_THRESHOLD:
            repo, issue_str = k.rsplit("#", 1)
            result.append((repo, int(issue_str), count))
    return result
