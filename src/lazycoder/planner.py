"""Planner (LLM call #1 per run).

Responsibilities:
- Scan all repos for open issues
- Decompose new issues into checklist subtasks with cost estimates
- Read prior ## Status comments to update existing plans
- Flag issues that look stuck (no progress after N runs)

One LLM call total per run (not per issue — batched prompt).
Posts/updates ## Plan comments on issues.
"""

from __future__ import annotations

import json
import re

import litellm
from github.Issue import Issue
from github.Repository import Repository

from .models import ChecklistItem, Plan


_PLAN_SYSTEM = """\
You are a software engineering planner for an automated GitHub bot.
You receive a list of open issues (with title, body, and any prior status comments).
For each issue WITHOUT an existing plan, decompose it into concrete subtasks.
For each issue WITH a prior status, update the plan to reflect progress.

Cost estimates per subtask:
  small  ~$0.02  (typo, config tweak, 1 file)
  medium ~$0.05  (feature, refactor, few files)
  large  ~$0.10  (complex, many files, unclear scope)

Return a JSON array — one entry per issue:
[
  {
    "issue": 42,
    "repo": "owner/repo",
    "items": [{"text": "...", "estimate_usd": 0.05, "done": false}, ...]
  }
]
Only include issues that need a plan created or updated. Skip issues that already
have a complete, up-to-date plan with no new status info.
"""


def _llm(model: str, prompt: str) -> str:
    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        max_tokens=4096,
    )
    u = getattr(resp, "usage", None)
    if u:
        cost = litellm.completion_cost(completion_response=resp)
        print(f"  tokens  in={u.prompt_tokens}  out={u.completion_tokens}  cost=${cost:.4f}")
    return resp.choices[0].message.content.strip()


def _build_prompt(issues_data: list[dict]) -> str:
    return "Issues to plan:\n\n" + json.dumps(issues_data, indent=2)


def _parse_response(raw: str) -> list[dict]:
    raw = re.sub(r"^```[a-z]*\n?|```$", "", raw, flags=re.MULTILINE).strip()
    return json.loads(raw)


def _format_plan_comment(items: list[ChecklistItem]) -> str:
    lines = ["## Plan"]
    for item in items:
        check = "x" if item.done else " "
        est = f" (~${item.estimate_usd:.2f})" if item.estimate_usd is not None else ""
        lines.append(f"- [{check}] {item.text}{est}")
    return "\n".join(lines)


def _parse_existing_plan(body: str) -> list[ChecklistItem]:
    items = []
    for line in body.splitlines():
        m = re.match(r"\s*-\s*\[(x| )\]\s*(.+)", line)
        if not m:
            continue
        done = m.group(1) == "x"
        text = m.group(2).strip()
        est_m = re.search(r"\(~?\$([0-9.]+)\)", text)
        est = float(est_m.group(1)) if est_m else None
        items.append(ChecklistItem(text=text, done=done, estimate_usd=est))
    return items


def run_planner(
    repos: list[str],
    model: str,
    token: str,
    bot_username: str,
    blocked_labels: list[str],
) -> list[Plan]:
    """Scan all repos, build one batched prompt, post/update ## Plan comments."""
    from github import Github
    gh = Github(token)

    issues_data: list[dict] = []
    issue_objects: dict[tuple[str, int], Issue] = {}
    plan_comments: dict[tuple[str, int], object] = {}  # existing comment objects

    for repo_name in repos:
        repo: Repository = gh.get_repo(repo_name)
        for issue in repo.get_issues(state="open"):
            if issue.pull_request:
                continue
            label_names = {lbl.name for lbl in issue.labels}
            if label_names & set(blocked_labels):
                continue

            key = (repo_name, issue.number)
            issue_objects[key] = issue

            # Find existing plan comment
            plan_comment = None
            status_text = None
            for c in issue.get_comments():
                if c.user.login != bot_username:
                    continue
                if c.body.startswith("## Plan"):
                    plan_comment = c
                elif c.body.startswith("## Status"):
                    status_text = c.body

            if plan_comment:
                plan_comments[key] = plan_comment

            entry = {
                "issue": issue.number,
                "repo": repo_name,
                "title": issue.title,
                "body": (issue.body or "")[:800],
                "has_plan": plan_comment is not None,
                "prior_status": status_text,
            }
            issues_data.append(entry)

    if not issues_data:
        return []

    # Single LLM call for all issues
    print(f"  {len(issues_data)} issue(s) → planning …")
    raw = _llm(model, _build_prompt(issues_data))
    updates = _parse_response(raw)

    plans: list[Plan] = []

    # Also collect plans that already exist and weren't updated
    updated_keys: set[tuple[str, int]] = set()

    for upd in updates:
        key = (upd["repo"], upd["issue"])
        updated_keys.add(key)
        issue = issue_objects.get(key)
        if not issue:
            continue

        items = [
            ChecklistItem(text=i["text"], done=i.get("done", False), estimate_usd=i.get("estimate_usd"))
            for i in upd["items"]
        ]
        plan = Plan(issue_number=upd["issue"], repo=upd["repo"], items=items)

        # Post or update comment
        body = _format_plan_comment(items)
        existing = plan_comments.get(key)
        if existing:
            existing.edit(body)
            plan.comment_id = existing.id
        else:
            c = issue.create_comment(body)
            plan.comment_id = c.id

        plans.append(plan)

    # Include issues with existing plans that the LLM didn't touch
    for key, issue in issue_objects.items():
        if key in updated_keys:
            continue
        pc = plan_comments.get(key)
        if pc:
            items = _parse_existing_plan(pc.body)
            plans.append(Plan(issue_number=key[1], repo=key[0], items=items, comment_id=pc.id))

    print(f"  {len(plans)} plan(s) ready")
    return plans
