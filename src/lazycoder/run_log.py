"""Manage the dedicated lazycoder run log issue.

On first run, creates a pinned issue titled '[lazycoder] Run log' in each repo
and stores its number in run_log.json. All ## Summary comments go there.
"""

from __future__ import annotations

import json
from pathlib import Path

from github import Github
from github.Repository import Repository

RUN_LOG_FILE = Path("run_log.json")
RUN_LOG_TITLE = "[lazycoder] Run log"
RUN_LOG_LABEL = "bot-meta"
RUN_LOG_BODY = (
    "This issue is the lazycoder run log. "
    "Each automated run posts a `## Summary` comment here.\n\n"
    "Do not close this issue."
)


def _load() -> dict[str, int]:
    if not RUN_LOG_FILE.exists():
        return {}
    return json.loads(RUN_LOG_FILE.read_text())


def _save(data: dict[str, int]) -> None:
    RUN_LOG_FILE.write_text(json.dumps(data, indent=2))


def _ensure_label(repo: Repository) -> None:
    existing = {lbl.name for lbl in repo.get_labels()}
    if RUN_LOG_LABEL not in existing:
        repo.create_label(name=RUN_LOG_LABEL, color="0075ca", description="lazycoder internal")


def get_or_create_log_issue(repo_name: str, token: str) -> int:
    """Return the issue number of the run log issue, creating it if needed."""
    data = _load()
    if repo_name in data:
        return data[repo_name]

    gh = Github(token)
    repo = gh.get_repo(repo_name)
    _ensure_label(repo)

    # Check if it already exists (e.g. run_log.json was deleted)
    for issue in repo.get_issues(state="open", labels=[RUN_LOG_LABEL]):
        if issue.title == RUN_LOG_TITLE:
            data[repo_name] = issue.number
            _save(data)
            return issue.number

    issue = repo.create_issue(
        title=RUN_LOG_TITLE,
        body=RUN_LOG_BODY,
        labels=[RUN_LOG_LABEL],
    )
    # Pin if API supports it (requires admin — silently ignore if not)
    try:
        repo._requester.requestJsonAndCheck(
            "PUT", f"{repo.url}/issues/{issue.number}/pin"
        )
    except Exception:
        pass

    print(f"[run_log] Created run log issue #{issue.number} in {repo_name}")
    data[repo_name] = issue.number
    _save(data)
    return issue.number
