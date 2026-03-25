"""
demo_swe_agent.py — minimal demonstration of mini-swe-agent usage.

Shows how lazycoder's executive role invokes the agent on a real repo.
Run with:
    python demo_swe_agent.py

Requires:
    GITHUB_TOKEN env var  (for cloning)
    ANTHROPIC_API_KEY env var
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# mini-swe-agent package is named minisweagent
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel


REPO = "shirk33y/tauron"
MODEL = "anthropic/claude-haiku-4-5-20251001"  # cheap model for demo


def clone_repo(repo: str, token: str) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="lazycoder-demo-"))
    url = f"https://x-access-token:{token}@github.com/{repo}.git"
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(tmpdir)],
        check=True,
        capture_output=True,
    )
    print(f"Cloned {repo} → {tmpdir}")
    return tmpdir


def run_agent(repo_dir: Path, task: str, model: str) -> dict:
    """Run mini-swe-agent on repo_dir for a given task string."""
    env = LocalEnvironment(cwd=str(repo_dir))
    mdl = LitellmModel(model_name=model, cost_tracking="ignore_errors")
    agent = DefaultAgent(mdl, env)

    prompt = f"""\
Repository path: {repo_dir}

Task:
{task}

Instructions:
- Read the relevant files first.
- Make only the minimal changes needed.
- Do not commit.
- When done, output a brief summary of what you changed.
"""
    print(f"\n[agent] Running on {repo_dir.name} ...")
    result = agent.run(prompt)
    return result


def show_diff(repo_dir: Path) -> None:
    result = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print("\n[diff --stat]")
        print(result.stdout)
    else:
        print("\n[no changes made]")


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("WARNING: GITHUB_TOKEN not set — cloning public repo without auth")

    repo_dir = clone_repo(REPO, token)

    try:
        task = (
            "Find any TODO or FIXME comments in the codebase. "
            "If there are none, add a small README section called '## Contributing' "
            "with one sentence about how to open an issue."
        )

        result = run_agent(repo_dir, task, MODEL)

        print("\n[agent output]")
        output = result.get("output") or result.get("result") or str(result)
        print(output[:1000])

        show_diff(repo_dir)

    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)
        print("\n[demo done] temp dir cleaned up")


if __name__ == "__main__":
    main()
