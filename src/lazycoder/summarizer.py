"""Summarizer (LLM call #2 per run).

Collects all ## Status comments from this run and writes a ## Summary
on a designated summary issue (or the first issue if none exists).
"""

from __future__ import annotations

from datetime import date

import litellm

from .conversation_store import ConversationStore
from .models import RunResult


_SUMMARY_SYSTEM = """\
You are writing a concise run summary for a GitHub bot.
Given a list of task results, write a short ## Summary comment.
Be factual. List each repo+issue, its outcome, and cost.
Keep it under 20 lines.
"""

_store = ConversationStore()


def _llm(model: str, user: str, *, repo: str, issue: int) -> str:
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": user},
    ]
    est_in = litellm.token_counter(model=model, messages=messages)
    print(f"  sending  in~{est_in} tokens …")
    resp = litellm.completion(model=model, messages=messages, max_tokens=512)
    u = getattr(resp, "usage", None)
    if u:
        cost = litellm.completion_cost(completion_response=resp)
        print(f"  done     in={u.prompt_tokens}  out={u.completion_tokens}  cost=${cost:.4f}")
    # Persist conversation (best-effort — never crash the main flow)
    try:
        path = _store.save_conversation(
            role="summarizer",
            repo=repo,
            issue=issue,
            messages=messages,
            response=resp,
        )
        print(f"  conversation saved → {path}")
    except Exception as exc:  # pragma: no cover
        print(f"  ⚠ could not save conversation: {exc}")

    return resp.choices[0].message.content.strip()


def write_summary(results: list[RunResult], model: str, token: str, bot_username: str,
                  *, repo: str = "", issue: int = 0) -> str:
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
    summary_text = _llm(model, user_prompt, repo=repo or (results[0].task.repo if results else ""), issue=issue)

    # Persist the human-readable markdown summary
    try:
        md_path = _store.save_summary(f"## Summary\n{summary_text}")
        print(f"  summary saved → {md_path}")
    except Exception as exc:  # pragma: no cover
        print(f"  ⚠ could not save summary: {exc}")

    return summary_text


def post_summary(results: list[RunResult], model: str, token: str, bot_username: str, repos: list[str]) -> None:
    """Post ## Summary as a comment on the dedicated run log issue for each repo."""
    from github import Github
    from .run_log import get_or_create_log_issue
    gh = Github(token)

    by_repo: dict[str, list[RunResult]] = {}
    for r in results:
        by_repo.setdefault(r.task.repo, []).append(r)

    for repo_name in repos:
        repo_results = by_repo.get(repo_name, [])
        if not repo_results:
            continue

        issue_number = get_or_create_log_issue(repo_name, token)
        summary_text = write_summary(
            repo_results, model, token, bot_username,
            repo=repo_name, issue=issue_number,
        )
        body = f"## Summary\n{summary_text}"

        repo = gh.get_repo(repo_name)
        issue = repo.get_issue(issue_number)

        issue.create_comment(body)
        print(f"  ✓ summary posted → {repo_name}#{issue_number}")
