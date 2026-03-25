# Project: lazycoder

A multi-repo GitHub issue orchestrator with three separated agent roles (judicial, legislative, executive), built as a Python wrapper around mini-swe-agent.

## What this is

A cron-driven bot that autonomously works on GitHub issues across ~10 hobby repositories. It reads issues, plans work, prioritizes within a daily budget, writes code, runs tests, commits to branches, and reports status — all via GitHub issue comments.

## Architecture: three branches of power

The system enforces strict separation of concerns. No role can do what another role does.

### Legislative (Sonnet)
- Scans all configured repos for open issues
- Reads prior bot comments (author=bot, headers `## Plan`, `## Status`, `## Summary`)
- Decomposes issues into checklist subtasks with cost estimates
- Posts/updates `## Plan` comments on issues
- After execution: collects all statuses, writes `## Summary`
- CAN: create subtasks (checklist items), estimate costs, summarize
- CANNOT: decide priority, decide what runs, execute code

### Judicial (Haiku)
- Reads plans from legislative, reads priority labels set by the human
- Reads `spent_today.json` for remaining budget
- Selects which tasks fit in today's soft budget ($0.50 default)
- Skips anything labeled `needs-human`
- Opens and closes each run (verifies summary, flags stuck tasks)
- CAN: prioritize, select, exclude, flag
- CANNOT: create tasks, modify plans, execute code

### Executive (Sonnet via mini-swe-agent)
- Receives selected tasks from judicial
- Per task: clones repo, checks out `bot/run-{date}` branch, builds a prompt from issue + prior `## Status` + plan checklist, invokes mini-swe-agent
- mini-swe-agent does the actual coding: reads files, writes code, runs tests, iterates
- After mini-swe-agent finishes: commits, pushes branch, posts `## Status` comment
- Checks actual cost vs estimate after each task. If actual > 2x estimate: posts `## Paused` comment with `@{repo_owner}` mention, adds `needs-human` label, stops working on that task
- Hard budget gate: before each LLM call, checks `spent_today < $1.00`. If exceeded, stops entirely
- CAN: read code, write code, run tests, commit, push, report status
- CANNOT: create new tasks, change priorities, skip budget checks

## Run cycle (cron triggers this)

1. Legislative scans repos, reads prior state
2. Legislative decomposes new issues into `## Plan` checklists (skips if plan exists)
3. Legislative updates existing plans based on `## Status` comments from prior runs
4. Judicial reads all plans, sorts by human-set priority labels, selects subset within budget
5. Executive works on each selected task sequentially (clone, prompt, mini-swe-agent, commit, status)
6. Executive checks budget between tasks — stops if hard limit reached
7. Legislative collects all `## Status` comments from this run, writes `## Summary`
8. Judicial verifies summary, flags tasks stuck for 3+ runs as `needs-human`
9. Log costs to `spent_today.json`

Next cron run reads summaries and statuses from this run as context. The system builds on prior work, never retries from scratch.

## GitHub comment conventions

All bot comments use markdown headers as identifiers. No HTML markers, no custom tags. Bot is identified by `author = bot` (the GitHub App or PAT user).

```markdown
## Plan
- [ ] Add OAuth middleware (~$0.05)
- [ ] Write integration tests (~$0.03)
- [x] Update dependencies ($0.02 actual)
```

```markdown
## Status
Task: Add OAuth middleware
Branch: bot/run-2026-03-25
Result: Tests passing, 3/4 checks green. Linting fails on line 47.
Cost: $0.04 (estimate was $0.05)
Remaining: Update error handling in callback route.
```

```markdown
## Summary
Run 2026-03-25: 3 tasks attempted, 2 completed, 1 partial.
Total cost: $0.11 / $0.50 budget.
repo-a#12: completed (OAuth middleware)
repo-b#7: partial (test failures, see status)
repo-c#3: skipped (needs-human)
```

```markdown
## Paused
Estimated: $0.05, spent so far: $0.11 (2.2x over estimate).
Task paused, needs review. @username
```

## Budget system

- Soft limit: $0.50/day (judicial uses this to select tasks)
- Hard limit: $1.00/day (executive checks before every LLM call)
- Cost estimation by legislative: small ~$0.02, medium ~$0.05, large ~$0.10
- Actual cost tracked via litellm callback (tokens_in * input_price + tokens_out * output_price)
- Stored in `spent_today.json`: `{"date": "2026-03-25", "tasks": [{"repo": "...", "issue": 12, "estimated": 0.05, "actual": 0.04}], "total": 0.11}`
- Resets daily (new date = fresh budget)
- Escalation: actual > 2x estimate → pause task, @mention owner, label `needs-human`

## File structure to create

```
lazycoder/
├── CLAUDE.md                  # This file
├── pyproject.toml             # Python package config
├── config.example.yaml        # Example configuration
├── src/
│   └── lazycoder/
│       ├── __init__.py
│       ├── main.py            # CLI entry point, cron cycle orchestration
│       ├── config.py          # Load YAML config, repo list, budget limits
│       ├── github_client.py   # GitHub API: issues, comments, labels (PyGithub or httpx)
│       ├── budget.py          # spent_today.json read/write, cost tracking, limit checks
│       ├── legislative.py     # Scan, decompose, summarize
│       ├── judicial.py        # Prioritize, select, verify
│       ├── executive.py       # Task execution loop, mini-swe-agent invocation
│       └── models.py          # Dataclasses: Task, Plan, RunResult, etc.
└── tests/
    ├── test_budget.py
    ├── test_judicial.py
    └── test_legislative.py
```

## Configuration (config.yaml)

```yaml
repos:
  - owner/repo-a
  - owner/repo-b
  # ... up to 10

budget:
  soft_limit_daily: 0.50
  hard_limit_daily: 1.00
  cost_overrun_multiplier: 2.0

models:
  legislative: anthropic/claude-sonnet-4-6
  judicial: anthropic/claude-haiku-4-5-20251001
  executive: anthropic/claude-sonnet-4-6

github:
  token_env: GITHUB_TOKEN   # reads from env var
  bot_username: lazycoder[bot]

branch_prefix: bot/run

priority_labels:
  - priority/critical
  - priority/high
  - priority/medium
  - priority/low

blocked_labels:
  - needs-human
  - wontfix
```

## Key dependencies

- `mini-swe-agent` — executive coding engine (pip install mini-swe-agent)
- `litellm` — LLM calls for legislative and judicial (already a mini-swe-agent dependency)
- `PyGithub` or `httpx` — GitHub API client
- `pyyaml` — config parsing
- `click` — CLI

## Implementation notes

- Legislative and judicial are simple LLM calls via litellm (not mini-swe-agent). They get a system prompt + context, return structured output (plan checklist, task selection).
- Executive wraps mini-swe-agent's Python API: `DefaultAgent(LitellmModel(model), LocalEnvironment()).run(prompt)`. The prompt includes issue description, prior status, plan checklist, and repo path.
- All git operations (clone, branch, commit, push) happen in a temp directory per task. Use subprocess for git, not a library.
- Sequential execution across repos (not parallel) to avoid rate limits.
- litellm has a `completion_cost()` function — use it to track actual spend per call.
- The bot GitHub user is determined by the PAT or GitHub App. Comments authored by this user are "bot comments".

## What to build first

1. `models.py` — dataclasses for Task, Plan, Issue, RunResult, BudgetEntry
2. `config.py` — load YAML, validate
3. `budget.py` — read/write spent_today.json, check limits
4. `github_client.py` — fetch issues, fetch bot comments by header, post comments, set labels
5. `judicial.py` — simplest LLM role: takes plans + budget, returns selected task list
6. `legislative.py` — scan + decompose + summarize
7. `executive.py` — the mini-swe-agent wrapper loop
8. `main.py` — wire it all together in the cron cycle
9. Tests for budget logic and judicial selection (mock LLM calls)

## Non-goals (do not build these)

- Web UI or dashboard
- Vector database or semantic memory (overkill for this scope)
- Docker/container orchestration
- Multi-machine distribution
- Real-time notifications (beyond @mention on escalation)

## Language and style

- All code, comments, docstrings, commit messages, and documentation in English
- Type hints everywhere
- No classes where functions suffice
- Minimal dependencies — stdlib where possible
- No async unless needed (sequential is fine for ~10 repos)
