"""Manage the daily lazycoder run log issue.

Creates a new issue each day titled '[lazycoder] Run YYYY-MM-DD' and closes
the previous day's issue. Stores the current issue number in run_log.json.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from github import Github
from github.Repository import Repository

RUN_LOG_FILE = Path("run_log.json")
RUN_LOG_LABEL = "bot-meta"


def _title_for(d: date) -> str:
    return f"[lazycoder] Run {d.isoformat()}"


def _load() -> dict:
    if not RUN_LOG_FILE.exists():
        return {}
    return json.loads(RUN_LOG_FILE.read_text())


def _save(data: dict) -> None:
    RUN_LOG_FILE.write_text(json.dumps(data, indent=2))


def _ensure_label(repo: Repository) -> None:
    existing = {lbl.name for lbl in repo.get_labels()}
    if RUN_LOG_LABEL not in existing:
        repo.create_label(name=RUN_LOG_LABEL, color="0075ca", description="lazycoder internal")


def get_or_create_log_issue(repo_name: str, token: str) -> int:
    """Return today's run log issue number, creating it (and closing yesterday's) if needed."""
    today = date.today().isoformat()
    data = _load()
    entry = data.get(repo_name, {})

    # Already have today's issue
    if isinstance(entry, dict) and entry.get("date") == today:
        return entry["issue"]

    gh = Github(token)
    repo = gh.get_repo(repo_name)
    _ensure_label(repo)

    # Close yesterday's issue if we have it recorded
    prev_issue_num = entry.get("issue") if isinstance(entry, dict) else (entry if isinstance(entry, int) else None)
    if prev_issue_num:
        try:
            prev = repo.get_issue(prev_issue_num)
            if prev.state == "open":
                prev.edit(state="closed")
                print(f"  closed previous run log #{prev_issue_num} in {repo_name}")
        except Exception:
            pass

    # Check if today's issue already exists on GitHub (e.g. run_log.json was deleted)
    title = _title_for(date.today())
    for issue in repo.get_issues(state="open", labels=[RUN_LOG_LABEL]):
        if issue.title == title:
            data[repo_name] = {"date": today, "issue": issue.number}
            _save(data)
            return issue.number

    issue = repo.create_issue(
        title=title,
        body=(
            f"lazycoder run log for {today}.\n\n"
            "Each task posts a `## Summary` comment here. "
            "This issue is closed at end of day automatically."
        ),
        labels=[RUN_LOG_LABEL],
    )

    print(f"  created run log issue #{issue.number} in {repo_name}")
    data[repo_name] = {"date": today, "issue": issue.number}
    _save(data)
    return issue.number
