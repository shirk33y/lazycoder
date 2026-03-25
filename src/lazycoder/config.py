"""Load and validate YAML configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(Path.home() / ".env")


@dataclass
class BudgetConfig:
    soft_limit_daily: float = 0.50
    hard_limit_daily: float = 1.00
    cost_overrun_multiplier: float = 2.0


@dataclass
class ModelsConfig:
    planner: str = "anthropic/claude-sonnet-4-6"
    summarizer: str = "anthropic/claude-haiku-4-5-20251001"
    executor: str = "anthropic/claude-haiku-4-5-20251001"


@dataclass
class GitHubConfig:
    token_env: str = "GITHUB_TOKEN"
    bot_username: str = "lazycoder-bot"

    @property
    def token(self) -> str:
        tok = os.getenv(self.token_env, "")
        if not tok:
            raise RuntimeError(f"No GitHub token found. Set {self.token_env!r} in ~/.env")
        return tok


@dataclass
class Config:
    repos: list[str]
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    branch_prefix: str = "bot/run"
    task_delay_seconds: int = 60  # pause between executor tasks to avoid rate limits
    max_tasks_per_run: int = 0  # 0 = unlimited
    priority_labels: list[str] = field(
        default_factory=lambda: ["priority/critical", "priority/high", "priority/medium", "priority/low"]
    )
    blocked_labels: list[str] = field(default_factory=lambda: ["needs-human", "wontfix"])


def load_config(path: str | Path) -> Config:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not found in ~/.env")

    raw = yaml.safe_load(Path(path).read_text())

    budget = BudgetConfig(**raw.get("budget", {}))
    models = ModelsConfig(**raw.get("models", {}))
    github = GitHubConfig(**raw.get("github", {}))

    return Config(
        repos=raw["repos"],
        budget=budget,
        models=models,
        github=github,
        branch_prefix=raw.get("branch_prefix", "bot/run"),
        task_delay_seconds=raw.get("task_delay_seconds", 60),
        max_tasks_per_run=raw.get("max_tasks_per_run", 0),
        priority_labels=raw.get("priority_labels", Config.__dataclass_fields__["priority_labels"].default_factory()),
        blocked_labels=raw.get("blocked_labels", Config.__dataclass_fields__["blocked_labels"].default_factory()),
    )
