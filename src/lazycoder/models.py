"""Core dataclasses for lazycoder."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Priority(str, Enum):
    CRITICAL = "priority/critical"
    HIGH = "priority/high"
    MEDIUM = "priority/medium"
    LOW = "priority/low"
    NONE = ""


@dataclass
class ChecklistItem:
    text: str
    done: bool
    estimate_usd: float | None = None
    actual_usd: float | None = None


@dataclass
class Plan:
    issue_number: int
    repo: str
    items: list[ChecklistItem]
    comment_id: int | None = None  # GitHub comment ID to update in-place


@dataclass
class StatusComment:
    issue_number: int
    repo: str
    task: str
    branch: str
    result: str
    cost_actual: float
    cost_estimate: float
    remaining: str = ""
    comment_id: int | None = None


@dataclass
class Task:
    """A single unit of work selected by the scheduler."""
    repo: str           # e.g. "shirk3y/tauron"
    issue_number: int
    task_text: str      # the checklist item text
    estimate_usd: float
    priority: Priority = Priority.NONE
    plan_comment_id: int | None = None


@dataclass
class RunResult:
    task: Task
    success: bool
    actual_cost: float
    branch: str
    notes: str
    status_comment_id: int | None = None


@dataclass
class BudgetEntry:
    repo: str
    issue: int
    task: str
    estimated: float
    actual: float


@dataclass
class DailyBudget:
    date: date
    entries: list[BudgetEntry] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(e.actual for e in self.entries)
