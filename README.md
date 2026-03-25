# lazycoder

Autonomous GitHub issue bot. Reads open issues, plans work, writes code, opens PRs.

## Architecture

```
triage     → read issues, estimate effort, apply size/* labels  (Haiku, no code)
plan       → decompose issues into checklists, post ## Plan     (Sonnet, 1 call)
run        → planner → scheduler → executor → summarizer
  planner    LLM #1: update plans, flag stuck issues            (Sonnet)
  scheduler  pure Python: sort by priority labels, cut at budget
  executor   mini-swe-agent × N: code, commit, PR              (Sonnet)
  summarizer LLM #2: write ## Summary                          (Haiku)
```

Bot never pushes to main/master — always opens a PR from `bot/run-{date}`.

## Setup

```bash
pip install -e .

# Create ~/.env with:
GITHUB_TOKEN=ghp_...
ANTHROPIC_API_KEY=sk-ant-...

cp config.example.yaml config.yaml
# edit config.yaml: set your repos
```

## Usage

```bash
lazycoder triage config.yaml           # label issues by size
lazycoder triage config.yaml --dry-run # preview without applying

lazycoder plan config.yaml             # post ## Plan checklists on issues
lazycoder plan config.yaml --dry-run   # preview plans

lazycoder run config.yaml --dry-run    # show scheduled tasks
lazycoder run config.yaml              # full cycle

lazycoder budget-status config.yaml    # show today's spend
```

## Labels

Set `priority/critical`, `priority/high`, `priority/medium`, or `priority/low` on issues
to control execution order. Unlabeled issues run last.

Add `needs-human` to pause the bot on an issue.

## Budget

- Soft limit (default $0.50/day): scheduler stops selecting tasks when reached
- Hard limit (default $1.00/day): executor stops mid-run if crossed
- Cost overrun (default 2×): if a task costs 2× its estimate, it is paused

Tracked in `spent_today.json` (local, not committed). Resets each day.
