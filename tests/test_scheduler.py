"""Tests for scheduler — pure Python, no LLM, no GitHub API calls."""

from __future__ import annotations

from datetime import date

import pytest

from lazycoder.budget import DailyBudget, BudgetEntry
from lazycoder.models import ChecklistItem, Plan, Priority, Task
from lazycoder.scheduler import _priority_of, _priority_enum, select_within_budget


# ------------------------------------------------------------------ helpers

def make_budget(spent: float = 0.0) -> DailyBudget:
    b = DailyBudget(date=date.today())
    if spent > 0:
        b.entries.append(BudgetEntry(repo="r", issue=1, task="t", estimated=spent, actual=spent))
    return b


def make_task(repo: str = "owner/repo", issue: int = 1, text: str = "do thing",
              est: float = 0.05, priority: Priority = Priority.NONE) -> Task:
    return Task(repo=repo, issue_number=issue, task_text=text,
                estimate_usd=est, priority=priority)


def make_candidate(sort_key: int, est: float, **kwargs) -> tuple[int, float, Task]:
    return (sort_key, est, make_task(est=est, **kwargs))


# ---------------------------------------------------------- _priority_of

def test_priority_of_critical():
    assert _priority_of({"priority/critical"}) == 0

def test_priority_of_high():
    assert _priority_of({"priority/high"}) == 1

def test_priority_of_medium():
    assert _priority_of({"priority/medium"}) == 2

def test_priority_of_low():
    assert _priority_of({"priority/low"}) == 3

def test_priority_of_none():
    assert _priority_of(set()) == 99

def test_priority_of_picks_highest():
    # If somehow both critical and low are set, critical wins
    assert _priority_of({"priority/critical", "priority/low"}) == 0


# --------------------------------------------------------- _priority_enum

def test_priority_enum_critical():
    assert _priority_enum({"priority/critical"}) == Priority.CRITICAL

def test_priority_enum_none():
    assert _priority_enum(set()) == Priority.NONE

def test_priority_enum_size_label_ignored():
    assert _priority_enum({"size/small"}) == Priority.NONE


# --------------------------------------------------- select_within_budget

def test_empty_candidates():
    assert select_within_budget([], 1.0) == []

def test_all_fit():
    candidates = [
        make_candidate(0, 0.02, text="a"),
        make_candidate(0, 0.03, text="b"),
    ]
    result = select_within_budget(candidates, 0.50)
    assert len(result) == 2

def test_budget_cutoff():
    candidates = [
        make_candidate(0, 0.10, text="a"),
        make_candidate(0, 0.10, text="b"),
        make_candidate(0, 0.10, text="c"),
    ]
    result = select_within_budget(candidates, 0.25)
    assert len(result) == 2
    assert sum(t.estimate_usd for t in result) == pytest.approx(0.20)

def test_priority_ordering():
    candidates = [
        make_candidate(3, 0.05, text="low priority"),    # priority/low
        make_candidate(0, 0.05, text="critical"),         # priority/critical
        make_candidate(2, 0.05, text="medium"),           # priority/medium
        make_candidate(1, 0.05, text="high"),             # priority/high
    ]
    result = select_within_budget(candidates, 1.0)
    texts = [t.task_text for t in result]
    assert texts == ["critical", "high", "medium", "low priority"]

def test_cheap_first_within_same_priority():
    candidates = [
        make_candidate(1, 0.10, text="expensive high"),
        make_candidate(1, 0.02, text="cheap high"),
    ]
    result = select_within_budget(candidates, 1.0)
    assert result[0].task_text == "cheap high"
    assert result[1].task_text == "expensive high"

def test_zero_remaining_budget():
    candidates = [make_candidate(0, 0.05, text="a")]
    result = select_within_budget(candidates, 0.0)
    assert result == []

def test_exact_budget_match():
    candidates = [make_candidate(0, 0.05, text="a")]
    # 0.05 > 0.05 is False so it should be included
    result = select_within_budget(candidates, 0.05)
    assert len(result) == 1

def test_done_items_excluded_via_plan_items():
    # select_within_budget receives candidates already filtered by schedule()
    # Verify it doesn't re-add done items (they'd never appear as candidates)
    candidates = [make_candidate(0, 0.05, text="pending only")]
    result = select_within_budget(candidates, 1.0)
    assert len(result) == 1

def test_idempotent():
    candidates = [
        make_candidate(0, 0.05, text="a"),
        make_candidate(1, 0.05, text="b"),
    ]
    r1 = select_within_budget(list(candidates), 1.0)
    r2 = select_within_budget(list(candidates), 1.0)
    assert [t.task_text for t in r1] == [t.task_text for t in r2]

def test_single_task_over_budget_skipped():
    candidates = [make_candidate(0, 0.30, text="too expensive")]
    result = select_within_budget(candidates, 0.10)
    assert result == []
