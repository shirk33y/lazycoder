"""Triage: read issues, estimate effort, apply size + priority labels.

This is the first and safest step — read-only except for label changes.
No comments posted, no code touched, no branches created.

Labels applied:
  size/small   (~$0.02, quick fix / typo / config)
  size/medium  (~$0.05, feature or refactor, few files)
  size/large   (~$0.10, complex feature, many files)

Priority labels are left to the human — we never set those.
If an issue already has a size label we skip it (idempotent).
"""

from __future__ import annotations

import json
import re

import litellm
from github.Issue import Issue
from github.Repository import Repository

SIZE_LABELS = {
    "size/small": ("size/small", "0aff00", "Quick fix, ~$0.02"),
    "size/medium": ("size/medium", "ffcc00", "Medium feature, ~$0.05"),
    "size/large": ("size/large", "e11d48", "Complex feature, ~$0.10"),
}

ESTIMATE_MAP = {
    "small": ("size/small", 0.02),
    "medium": ("size/medium", 0.05),
    "large": ("size/large", 0.10),
}

_TRIAGE_SYSTEM = """\
You are a software engineering effort estimator.
Given a GitHub issue title and body, classify the effort as one of:
  small  — trivial fix, config change, or tiny addition (~1-2 files, <30 min)
  medium — meaningful feature or refactor, a few files (~1-2 hours)
  large  — complex feature, many files, or unclear scope (>2 hours)

Return JSON only: {"size": "small"|"medium"|"large", "reason": "one sentence"}
"""


def _ensure_labels(repo: Repository) -> None:
    existing = {lbl.name for lbl in repo.get_labels()}
    for name, (_, color, desc) in SIZE_LABELS.items():
        if name not in existing:
            repo.create_label(name=name, color=color, description=desc)


def _has_size_label(issue: Issue) -> bool:
    return any(lbl.name.startswith("size/") for lbl in issue.labels)


def _estimate(issue: Issue, model: str) -> tuple[str, float, str]:
    """Returns (size_label, estimate_usd, reason)."""
    user = f"Title: {issue.title}\n\nBody:\n{issue.body or '(no body)'}"
    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": _TRIAGE_SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=128,
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```[a-z]*\n?|```$", "", raw, flags=re.MULTILINE).strip()
    data = json.loads(raw)
    size = data.get("size", "medium")
    label, usd = ESTIMATE_MAP.get(size, ESTIMATE_MAP["medium"])
    return label, usd, data.get("reason", "")


def triage_repo(
    repo_name: str,
    model: str,
    token: str,
    blocked_labels: list[str],
    dry_run: bool = False,
) -> list[dict]:
    """Triage all open issues in a repo. Returns list of results."""
    from github import Github
    gh = Github(token)
    repo = gh.get_repo(repo_name)

    if not dry_run:
        _ensure_labels(repo)

    results = []
    for issue in repo.get_issues(state="open"):
        # Skip PRs (GitHub returns them as issues too)
        if issue.pull_request:
            continue
        issue_labels = {lbl.name for lbl in issue.labels}
        if issue_labels & set(blocked_labels):
            results.append({"issue": issue.number, "title": issue.title, "skipped": "blocked"})
            continue
        if _has_size_label(issue):
            existing = next(l for l in issue.labels if l.name.startswith("size/"))
            results.append({"issue": issue.number, "title": issue.title, "skipped": f"already {existing.name}"})
            continue

        label, usd, reason = _estimate(issue, model)

        if not dry_run:
            issue.add_to_labels(label)

        results.append({
            "issue": issue.number,
            "title": issue.title,
            "label": label,
            "estimate_usd": usd,
            "reason": reason,
            "dry_run": dry_run,
        })

    return results
