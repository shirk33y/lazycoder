"""Executor — runs mini-swe-agent per task, commits, opens PRs.

Never pushes to main/master. Always bot/run-{date} branch → PR.
Checks cost vs estimate after each task:
  actual > 2x estimate → pause, add needs-human, stop that task.
Hard budget gate checked before each agent invocation.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from github import Github

import logging
import time

from .budget import DailyBudget, add_entry, over_hard_limit
from .config import Config
from .models import RunResult, Task
from .run_tracker import record_result


class _RateLimitHalt(Exception):
    """Raised to immediately stop the entire run when rate-limited."""


def _build_prompt(issue_title: str, issue_body: str, task_text: str, prior_status: str | None) -> str:
    parts = [
        f"Task: {task_text}",
        "",
        f"Issue: {issue_title}",
        issue_body or "(no body)",
    ]
    if prior_status:
        parts += ["", "Prior run status:", prior_status]
    parts += [
        "",
        "Instructions:",
        "- Make only the changes needed for this task.",
        "- Run existing tests if present. Do not break passing tests.",
        "- Do not commit. Output a one-paragraph summary of what you changed.",
    ]
    return "\n".join(parts)


_DEFAULT_SYSTEM = """\
You are a helpful assistant that can interact with a computer.

Your response must contain exactly ONE bash code block with ONE command (or commands connected with && or ||).
Include a THOUGHT section before your command where you explain your reasoning process.
Format your response as shown in <format_example>.

<format_example>
Your reasoning and analysis here. Explain why you want to perform the action.

```mswea_bash_command
your_command_here
```
</format_example>

Failure to follow these rules will cause your response to be rejected.
"""

_DEFAULT_INSTANCE = """\
Please solve this task: {{task}}

You can execute bash commands and edit files to implement the necessary changes.

## Recommended Workflow
1. Analyze the codebase by finding and reading relevant files
2. Make the changes needed for this task
3. Run existing tests if present — do not break passing tests
4. Output a one-paragraph summary of what you changed (do NOT commit)
"""


def _run_agent(prompt: str, repo_dir: Path, model: str) -> tuple[str, float]:
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_model import LitellmModel

    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    logging.getLogger("litellm").setLevel(logging.ERROR)

    env = LocalEnvironment(cwd=str(repo_dir))
    mdl = LitellmModel(model_name=model, cost_tracking="ignore_errors")
    agent = DefaultAgent(mdl, env, system_template=_DEFAULT_SYSTEM, instance_template=_DEFAULT_INSTANCE)
    result = agent.run(prompt)

    cost = 0.0
    try:
        from minisweagent.models import GLOBAL_MODEL_STATS
        cost = float(getattr(GLOBAL_MODEL_STATS, "total_cost", 0.0))
    except Exception:
        pass

    summary = result.get("output") or result.get("result") or "(no summary)"
    return str(summary), cost


def _clone_and_branch(repo_name: str, base_branch: str, branch: str, token: str) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="lazycoder-"))
    url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
    subprocess.run(["git", "clone", "--depth", "1", "-b", base_branch, url, str(tmpdir)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", branch], cwd=tmpdir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "lazycoder-bot@users.noreply.github.com"], cwd=tmpdir, check=True)
    subprocess.run(["git", "config", "user.name", "lazycoder-bot"], cwd=tmpdir, check=True)
    return tmpdir


def _commit_and_push(repo_dir: Path, branch: str, message: str, token: str, repo_name: str) -> bool:
    r = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
    if not r.stdout.strip():
        return False
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo_dir, check=True)
    url = f"https://x-access-token:{token}@github.com/{repo_name}.git"
    subprocess.run(["git", "push", url, branch], cwd=repo_dir, check=True, capture_output=True)
    return True


def run_task(task: Task, budget: DailyBudget, cfg: Config) -> RunResult:
    gh = Github(cfg.github.token)
    repo_obj = gh.get_repo(task.repo)
    issue = repo_obj.get_issue(task.issue_number)
    base_branch = repo_obj.default_branch
    branch = f"{cfg.branch_prefix}-{date.today().isoformat()}"

    # Prior status for context
    prior_status = None
    for c in issue.get_comments():
        if c.user.login == cfg.github.bot_username and c.body.startswith("## Status"):
            prior_status = c.body
            break

    repo_dir: Path | None = None
    try:
        repo_dir = _clone_and_branch(task.repo, base_branch, branch, cfg.github.token)
        prompt = _build_prompt(issue.title, issue.body or "", task.task_text, prior_status)
        summary, actual_cost = _run_agent(prompt, repo_dir, cfg.models.executor)

        # Cost overrun check
        overrun_limit = task.estimate_usd * cfg.budget.cost_overrun_multiplier
        if actual_cost > overrun_limit and actual_cost > 0:
            owner = task.repo.split("/")[0]
            ratio = actual_cost / task.estimate_usd
            issue.create_comment(
                f"## Paused\n"
                f"Estimated: ${task.estimate_usd:.3f}, spent: ${actual_cost:.3f} ({ratio:.1f}x).\n"
                f"Task paused, needs review. @{owner}\n"
            )
            issue.add_to_labels("needs-human")
            add_entry(budget, task.repo, task.issue_number, task.task_text, task.estimate_usd, actual_cost)
            return RunResult(task=task, success=False, actual_cost=actual_cost, branch=branch,
                             notes=f"Cost overrun {ratio:.1f}x")

        # Commit + PR
        commit_msg = f"lazycoder: {task.task_text[:72]}"
        changed = _commit_and_push(repo_dir, branch, commit_msg, cfg.github.token, task.repo)

        pr_url = ""
        if changed:
            pr = repo_obj.create_pull(
                title=f"[bot] {task.task_text[:60]}",
                body=(
                    f"## lazycoder automated PR\n\n"
                    f"Closes #{task.issue_number} (partial)\n\n"
                    f"**Task:** {task.task_text}\n\n"
                    f"**Summary:** {summary}\n\n"
                    f"Cost: ${actual_cost:.3f} (estimate ${task.estimate_usd:.3f})\n\n"
                    f"---\n*Please review before merging.*"
                ),
                head=branch,
                base=base_branch,
            )
            pr_url = pr.html_url

        # Post ## Status comment
        status_body = (
            f"## Status\n"
            f"Task: {task.task_text}\n"
            f"Branch: {branch}\n"
            f"Result: {summary[:400]}\n"
            f"Cost: ${actual_cost:.3f} (estimate ${task.estimate_usd:.3f})\n"
        )
        if pr_url:
            status_body += f"PR: {pr_url}\n"
        if not changed:
            status_body += "Note: no file changes made.\n"

        # Upsert status comment
        existing_status = None
        for c in issue.get_comments():
            if c.user.login == cfg.github.bot_username and c.body.startswith("## Status"):
                existing_status = c
                break
        if existing_status:
            existing_status.edit(status_body)
            comment_id = existing_status.id
        else:
            nc = issue.create_comment(status_body)
            comment_id = nc.id

        add_entry(budget, task.repo, task.issue_number, task.task_text, task.estimate_usd, actual_cost)
        record_result(task.repo, task.issue_number, success=changed)
        return RunResult(task=task, success=changed, actual_cost=actual_cost,
                         branch=branch, notes=summary, status_comment_id=comment_id)

    except Exception as exc:
        err = str(exc)
        is_rate_limit = "rate_limit" in err.lower() or "RateLimitError" in type(exc).__name__
        try:
            issue.create_comment(
                f"## Status\nTask: `{task.task_text}`\nBranch: `{branch}`\n\n"
                f"```\nERROR: {err[:400]}\n```\n\nCost: $0.000"
            )
        except Exception:
            pass
        record_result(task.repo, task.issue_number, success=False)
        result = RunResult(task=task, success=False, actual_cost=0.0, branch=branch, notes=err)
        if is_rate_limit:
            raise _RateLimitHalt(err) from exc
        return result
    finally:
        if repo_dir and repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)


def run_all(tasks: list[Task], budget: DailyBudget, cfg: Config) -> list[RunResult]:
    results: list[RunResult] = []
    for i, task in enumerate(tasks, 1):
        if over_hard_limit(budget, cfg.budget.hard_limit_daily):
            print(f"  ⚠ hard limit ${cfg.budget.hard_limit_daily} reached — stopping")
            break
        print(f"  [{i}/{len(tasks)}] #{task.issue_number}  {task.task_text[:70]}")
        try:
            result = run_task(task, budget, cfg)
        except _RateLimitHalt as e:
            print(f"  ⚠ rate limit hit — halting run")
            print(f"    {str(e)[:200]}")
            break
        results.append(result)
        mark = "✓" if result.success else "✗"
        cost_str = f"${result.actual_cost:.3f}"
        print(f"        {mark}  {cost_str}")
        if not result.success:
            short_err = (result.notes or "unknown error")[:120]
            print(f"        ERROR: {short_err}")
        if i < len(tasks) and cfg.task_delay_seconds > 0:
            print(f"        waiting {cfg.task_delay_seconds}s …")
            time.sleep(cfg.task_delay_seconds)
    return results
