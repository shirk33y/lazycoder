"""Scheduler — pure Python, zero LLM calls.

Responsibilities:
- Sort plans by human-set priority labels (priority/critical → low → none)
- Accumulate estimates, cut list when soft budget is exhausted
- Skip issues labeled needs-human or other blocked labels
- Return an ordered list of Tasks ready for the executor
"""

from __future__ import annotations

from github import Github

from .budget import DailyBudget, remaining_soft
from .models import Plan, Priority, Task
from .run_tracker import is_stuck


# Priority label → sort key (lower = higher priority)
_PRIORITY_ORDER = {
    "priority/critical": 0,
    "priority/high": 1,
    "priority/medium": 2,
    "priority/low": 3,
}


def _priority_of(label_names: set[str]) -> int:
    for label, order in _PRIORITY_ORDER.items():
        if label in label_names:
            return order
    return 99  # no priority label → sort last


def _priority_enum(label_names: set[str]) -> Priority:
    for label in _PRIORITY_ORDER:
        if label in label_names:
            return Priority(label)
    return Priority.NONE


def select_within_budget(
    candidates: list[tuple[int, float, Task]],
    remaining: float,
) -> list[Task]:
    """Pure selection logic: sort candidates, pick tasks that fit in remaining budget."""
    candidates.sort(key=lambda x: (x[0], x[1]))
    selected: list[Task] = []
    accumulated = 0.0
    for _, est, task in candidates:
        if accumulated + est > remaining:
            print(f"  budget cap ${accumulated:.3f} — stopping selection")
            break
        selected.append(task)
        accumulated += est
    return selected


def schedule(
    plans: list[Plan],
    budget: DailyBudget,
    soft_limit: float,
    token: str,
    blocked_labels: list[str],
) -> list[Task]:
    """Return ordered tasks that fit within the remaining soft budget."""
    remaining = remaining_soft(budget, soft_limit)
    if remaining <= 0:
        print(f"  soft budget exhausted (${budget.total:.3f} / ${soft_limit:.2f})")
        return []

    gh = Github(token)
    candidates: list[tuple[int, float, Task]] = []

    for plan in plans:
        try:
            repo = gh.get_repo(plan.repo)
            issue = repo.get_issue(plan.issue_number)
        except Exception as e:
            print(f"  ✗ could not fetch #{plan.issue_number}: {e}")
            continue

        label_names = {lbl.name for lbl in issue.labels}

        if label_names & set(blocked_labels):
            print(f"  — #{plan.issue_number} skipped (blocked label)")
            continue

        # Data-based stuck detection — no LLM needed
        if is_stuck(plan.repo, plan.issue_number):
            print(f"  ⚠ #{plan.issue_number} stuck (3+ failures) → needs-human")
            try:
                issue.add_to_labels("needs-human")
            except Exception:
                pass
            continue

        sort_key = _priority_of(label_names)
        priority = _priority_enum(label_names)

        for item in plan.items:
            if item.done:
                continue
            est = item.estimate_usd or 0.05
            task = Task(
                repo=plan.repo,
                issue_number=plan.issue_number,
                task_text=item.text,
                estimate_usd=est,
                priority=priority,
                plan_comment_id=plan.comment_id,
            )
            candidates.append((sort_key, est, task))

    selected = select_within_budget(candidates, remaining)
    total = sum(t.estimate_usd for t in selected)
    print(f"  {len(selected)} task(s) selected  ~${total:.3f} / ${remaining:.3f} remaining")
    return selected
