"""Summarizer (LLM call #2 per run).

Collects all ## Status comments from this run and writes a ## Summary
on a designated summary issue (or the first issue if none exists).
"""

from __future__ import annotations

from datetime import date

import litellm

from .models import RunResult


_SUMMARY_SYSTEM = """\
You are writing a concise run summary for a GitHub bot.
Given a list of task results, write a short ## Summary comment.
Be factual. List each repo+issue, its outcome, and cost.
Keep it under 20 lines.
"""


def _llm(model: str, user: str) -> str:
    resp = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=512,
    )
    return resp.choices[0].message.content.strip()


def write_summary(results: list[RunResult], model: str, token: str, bot_username: str) -> str:
    """Generate and return summary text. Caller decides where to post it."""
    if not results:
        return f"## Summary\nRun {date.today().isoformat()}: no tasks executed."

    total = sum(r.actual_cost for r in results)
    lines = [f"Run {date.today().isoformat()}: {len(results)} tasks, total ${total:.3f}"]
    for r in results:
        status = "completed" if r.success else "partial/failed"
        lines.append(f"- {r.task.repo}#{r.task.issue_number}: {status} — {r.task.task_text[:60]}")
        if r.notes and not r.success:
            lines.append(f"  note: {r.notes[:100]}")

    user_prompt = "\n".join(lines)
    return _llm(model, user_prompt)


def post_summary(results: list[RunResult], model: str, token: str, bot_username: str, repos: list[str]) -> None:
    """Post ## Summary as a comment on the first open issue of each repo."""
    from github import Github
    gh = Github(token)

    by_repo: dict[str, list[RunResult]] = {}
    for r in results:
        by_repo.setdefault(r.task.repo, []).append(r)

    for repo_name in repos:
        repo_results = by_repo.get(repo_name, [])
        if not repo_results:
            continue

        summary_text = write_summary(repo_results, model, token, bot_username)
        body = f"## Summary\n{summary_text}"

        repo = gh.get_repo(repo_name)
        issues = list(repo.get_issues(state="open"))
        if not issues:
            continue

        # Upsert on the first issue
        issue = issues[0]
        for c in issue.get_comments():
            if c.user.login == bot_username and c.body.startswith("## Summary"):
                c.edit(body)
                print(f"[summarizer] Updated summary on {repo_name}#{issue.number}")
                break
        else:
            issue.create_comment(body)
            print(f"[summarizer] Posted summary on {repo_name}#{issue.number}")
