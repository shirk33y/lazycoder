"""GitHub API helpers: issues, comments, labels, branches, PRs."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from github import Github
from github.Issue import Issue
from github.IssueComment import IssueComment
from github.Repository import Repository

from .models import ChecklistItem, Plan, StatusComment


@dataclass
class GitHubClient:
    token: str
    bot_username: str

    def __post_init__(self) -> None:
        self._gh = Github(self.token)

    def repo(self, full_name: str) -> Repository:
        return self._gh.get_repo(full_name)

    # ------------------------------------------------------------------ issues

    def open_issues(self, full_name: str) -> list[Issue]:
        r = self.repo(full_name)
        return list(r.get_issues(state="open"))

    def has_blocked_label(self, issue: Issue, blocked_labels: list[str]) -> bool:
        names = {lbl.name for lbl in issue.labels}
        return bool(names & set(blocked_labels))

    def priority_index(self, issue: Issue, priority_labels: list[str]) -> int:
        names = {lbl.name for lbl in issue.labels}
        for i, lbl in enumerate(priority_labels):
            if lbl in names:
                return i
        return len(priority_labels)

    def add_label(self, issue: Issue, label: str) -> None:
        issue.add_to_labels(label)

    def remove_label(self, issue: Issue, label: str) -> None:
        try:
            issue.remove_from_labels(label)
        except Exception:
            pass

    # --------------------------------------------------------------- comments

    def bot_comments(self, issue: Issue) -> list[IssueComment]:
        return [c for c in issue.get_comments() if c.user.login == self.bot_username]

    def find_comment_by_header(self, issue: Issue, header: str) -> IssueComment | None:
        prefix = f"## {header}"
        for c in self.bot_comments(issue):
            if c.body.startswith(prefix):
                return c
        return None

    def post_comment(self, issue: Issue, body: str) -> IssueComment:
        return issue.create_comment(body)

    def update_comment(self, comment: IssueComment, body: str) -> None:
        comment.edit(body)

    def upsert_comment(self, issue: Issue, header: str, body: str) -> IssueComment:
        existing = self.find_comment_by_header(issue, header)
        if existing:
            self.update_comment(existing, body)
            return existing
        return self.post_comment(issue, body)

    # ------------------------------------------------------------------- plan

    def parse_plan(self, issue: Issue) -> Plan | None:
        comment = self.find_comment_by_header(issue, "Plan")
        if not comment:
            return None

        items: list[ChecklistItem] = []
        for line in comment.body.splitlines():
            m = re.match(r"\s*-\s*\[(x| )\]\s*(.+)", line)
            if not m:
                continue
            done = m.group(1) == "x"
            text = m.group(2).strip()
            # parse optional estimate: (~$0.05) or ($0.04 actual)
            est_match = re.search(r"\(~?\$([0-9.]+)", text)
            est = float(est_match.group(1)) if est_match else None
            items.append(ChecklistItem(text=text, done=done, estimate_usd=est))

        return Plan(
            issue_number=issue.number,
            repo=issue.repository.full_name,
            items=items,
            comment_id=comment.id,
        )

    def post_plan(self, issue: Issue, plan: Plan) -> IssueComment:
        lines = ["## Plan"]
        for item in plan.items:
            check = "x" if item.done else " "
            est = f" (~${item.estimate_usd:.2f})" if item.estimate_usd is not None else ""
            lines.append(f"- [{check}] {item.text}{est}")
        body = "\n".join(lines)
        return self.upsert_comment(issue, "Plan", body)

    def post_status(self, issue: Issue, s: StatusComment) -> IssueComment:
        body = (
            f"## Status\n"
            f"Task: {s.task}\n"
            f"Branch: {s.branch}\n"
            f"Result: {s.result}\n"
            f"Cost: ${s.cost_actual:.3f} (estimate was ${s.cost_estimate:.3f})\n"
        )
        if s.remaining:
            body += f"Remaining: {s.remaining}\n"
        return self.upsert_comment(issue, "Status", body)

    def post_paused(self, issue: Issue, estimated: float, actual: float, owner: str) -> IssueComment:
        ratio = actual / estimated if estimated else 0
        body = (
            f"## Paused\n"
            f"Estimated: ${estimated:.3f}, spent so far: ${actual:.3f} ({ratio:.1f}x over estimate).\n"
            f"Task paused, needs review. @{owner}\n"
        )
        comment = self.post_comment(issue, body)
        self.add_label(issue, "needs-human")
        return comment

    # -------------------------------------------------------------------- git

    def clone_and_branch(self, full_name: str, base_branch: str, branch_name: str) -> Path:
        tmpdir = Path(tempfile.mkdtemp(prefix="lazycoder-"))
        url = f"https://x-access-token:{self.token}@github.com/{full_name}.git"
        subprocess.run(["git", "clone", "--depth", "1", "-b", base_branch, url, str(tmpdir)], check=True)
        subprocess.run(["git", "checkout", "-b", branch_name], cwd=tmpdir, check=True)
        subprocess.run(["git", "config", "user.email", "lazycoder-bot@users.noreply.github.com"], cwd=tmpdir, check=True)
        subprocess.run(["git", "config", "user.name", "lazycoder-bot"], cwd=tmpdir, check=True)
        return tmpdir

    def commit_and_push(self, repo_dir: Path, branch: str, message: str) -> bool:
        result = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
        if not result.stdout.strip():
            return False  # nothing changed
        subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=repo_dir, check=True)
        subprocess.run(["git", "push", "origin", branch], cwd=repo_dir, check=True)
        return True

    def create_pr(self, full_name: str, branch: str, base: str, title: str, body: str) -> str:
        r = self.repo(full_name)
        pr = r.create_pull(title=title, body=body, head=branch, base=base)
        return pr.html_url

    def default_branch(self, full_name: str) -> str:
        return self.repo(full_name).default_branch

    def run_date_branch(self, prefix: str) -> str:
        return f"{prefix}-{date.today().isoformat()}"
