"""CLI entry point.

Run cycle:
  1. planner    (LLM #1) — decompose issues, update plans, flag stuck
  2. scheduler  (Python)  — sort by priority labels, cut at budget
  3. executor   (mini-swe-agent × N) — code, commit, PR
  4. summarizer (LLM #2) — write ## Summary comment
"""

from __future__ import annotations

import click
import litellm
litellm.num_retries = 0  # never retry — rate limits should halt cleanly

from .budget import load_budget, save_budget
from .config import load_config
from .executor import run_all
from .planner import run_planner
from .scheduler import schedule
from .summarizer import post_summary
from .triage import triage_repo


@click.group()
def cli() -> None:
    """lazycoder — autonomous GitHub issue bot."""


@cli.command()
@click.argument("config_path", default="config.yaml", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Print estimates without applying labels.")
def triage(config_path: str, dry_run: bool) -> None:
    """Read open issues, estimate effort, apply size/* labels. No code touched."""
    cfg = load_config(config_path)
    for repo_name in cfg.repos:
        print(f"\n[triage] {repo_name}")
        results = triage_repo(
            repo_name=repo_name,
            model=cfg.models.summarizer,  # haiku — cheap classification
            token=cfg.github.token,
            blocked_labels=cfg.blocked_labels,
            dry_run=dry_run,
        )
        for r in results:
            if "skipped" in r:
                print(f"  #{r['issue']:3d} SKIP  {r['skipped']:<20s}  {r['title'][:60]}")
            else:
                tag = "[DRY]" if dry_run else "     "
                print(f"  #{r['issue']:3d} {tag} {r['label']:<14s}  ~${r['estimate_usd']:.2f}  {r['title'][:50]}")
                print(f"         → {r['reason']}")


@cli.command()
@click.argument("config_path", default="config.yaml", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Show scheduled tasks, skip execution.")
def run(config_path: str, dry_run: bool) -> None:
    """Full cycle: planner → scheduler → executor → summarizer."""
    cfg = load_config(config_path)
    budget = load_budget()

    spent = budget.total
    soft = cfg.budget.soft_limit_daily
    print(f"budget  ${spent:.3f} / ${soft:.2f}  ({spent/soft*100:.0f}% of soft limit)")

    # 1. Planner
    print("\nplanning …")
    plans = run_planner(
        repos=cfg.repos,
        model=cfg.models.planner,
        token=cfg.github.token,
        bot_username=cfg.github.bot_username,
        blocked_labels=cfg.blocked_labels,
    )
    print(f"  {len(plans)} issue(s) planned")

    # 2. Scheduler
    tasks = schedule(
        plans=plans,
        budget=budget,
        soft_limit=cfg.budget.soft_limit_daily,
        token=cfg.github.token,
        blocked_labels=cfg.blocked_labels,
    )

    if not tasks:
        print("\nnothing to schedule — done.")
        return

    print(f"\nscheduled {len(tasks)} task(s):")
    for t in tasks:
        pri = t.priority.value or "—"
        print(f"  #{t.issue_number:<4} ~${t.estimate_usd:.2f}  [{pri}]  {t.task_text[:70]}")

    if dry_run:
        print("\ndry run — stopping before execution.")
        return

    # 3. Executor
    print()
    results = run_all(tasks, budget, cfg)

    # 4. Summarizer
    post_summary(results, cfg.models.summarizer, cfg.github.token,
                 cfg.github.bot_username, cfg.repos)

    save_budget(budget)
    ok = sum(1 for r in results if r.success)
    fail = len(results) - ok
    print(f"\n✓ done  {ok} ok  {fail} failed  total ${budget.total:.3f}")


@cli.command()
@click.argument("config_path", default="config.yaml", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Show plans without posting comments.")
def plan(config_path: str, dry_run: bool) -> None:
    """Planner only: decompose issues and post ## Plan comments."""
    cfg = load_config(config_path)
    plans = run_planner(
        repos=cfg.repos,
        model=cfg.models.planner,
        token=cfg.github.token,
        bot_username=cfg.github.bot_username,
        blocked_labels=cfg.blocked_labels,
    )
    print(f"Done. {len(plans)} plans.")
    for p in plans:
        pending = sum(1 for i in p.items if not i.done)
        total_est = sum(i.estimate_usd or 0 for i in p.items if not i.done)
        print(f"  {p.repo}#{p.issue_number}  {pending} pending tasks  ~${total_est:.2f}")


def _fetch_anthropic_cost(admin_key: str) -> str | None:
    """Fetch today's cost from Anthropic Admin API. Returns formatted string or None."""
    import urllib.request
    import urllib.error
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    tomorrow = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")
    url = (
        f"https://api.anthropic.com/v1/organizations/cost_report"
        f"?starting_at={today}&ending_at={tomorrow}&group_by[]=model"
    )
    req = urllib.request.Request(url, headers={
        "anthropic-version": "2023-06-01",
        "x-api-key": admin_key,
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            import json as _json
            data = _json.loads(r.read())
            total_cents = sum(float(e.get("cost", 0)) for e in data.get("data", []))
            return f"${total_cents/100:.4f} today (Anthropic Admin API)"
    except Exception as e:
        return f"unavailable ({e})"


@cli.command()
@click.argument("config_path", default="config.yaml", type=click.Path(exists=True))
def budget_status(config_path: str) -> None:
    """Show today's budget usage."""
    import os as _os
    cfg = load_config(config_path)
    budget = load_budget()
    print(f"date         {budget.date}")
    print(f"soft limit   ${cfg.budget.soft_limit_daily:.2f}")
    print(f"hard limit   ${cfg.budget.hard_limit_daily:.2f}")
    print(f"spent today  ${budget.total:.4f}")
    for e in budget.entries:
        mark = "✓" if e.actual > 0 else "—"
        print(f"  {mark} {e.repo}#{e.issue}  est=${e.estimated:.3f}  actual=${e.actual:.4f}  '{e.task[:50]}'")
    admin_key = _os.getenv("ANTHROPIC_ADMIN_KEY")
    if admin_key:
        print(f"\nanthropix    {_fetch_anthropic_cost(admin_key)}")
    else:
        print("\nanthropix    set ANTHROPIC_ADMIN_KEY in ~/.env for live balance")


if __name__ == "__main__":
    cli()
