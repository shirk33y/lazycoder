"""Daily budget tracking via spent_today.json."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .models import BudgetEntry, DailyBudget

BUDGET_FILE = Path("spent_today.json")


def _today() -> date:
    return date.today()


def load_budget(path: Path = BUDGET_FILE) -> DailyBudget:
    if not path.exists():
        return DailyBudget(date=_today())

    raw = json.loads(path.read_text())
    stored_date = date.fromisoformat(raw["date"])

    if stored_date != _today():
        # New day — fresh budget
        return DailyBudget(date=_today())

    entries = [BudgetEntry(**e) for e in raw.get("tasks", [])]
    return DailyBudget(date=stored_date, entries=entries)


def save_budget(budget: DailyBudget, path: Path = BUDGET_FILE) -> None:
    data = {
        "date": budget.date.isoformat(),
        "tasks": [
            {
                "repo": e.repo,
                "issue": e.issue,
                "task": e.task,
                "estimated": e.estimated,
                "actual": e.actual,
            }
            for e in budget.entries
        ],
        "total": budget.total,
    }
    path.write_text(json.dumps(data, indent=2))


def add_entry(
    budget: DailyBudget,
    repo: str,
    issue: int,
    task: str,
    estimated: float,
    actual: float,
    path: Path = BUDGET_FILE,
) -> DailyBudget:
    budget.entries.append(BudgetEntry(repo=repo, issue=issue, task=task, estimated=estimated, actual=actual))
    save_budget(budget, path)
    return budget


def remaining_soft(budget: DailyBudget, soft_limit: float) -> float:
    return max(0.0, soft_limit - budget.total)


def over_hard_limit(budget: DailyBudget, hard_limit: float) -> bool:
    return budget.total >= hard_limit
