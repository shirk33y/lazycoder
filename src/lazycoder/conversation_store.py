"""ConversationStore — persist LLM conversations and run summaries to disk.

Storage layout:
  conversations/{date}/{repo}#{issue}-{role}.json   — one file per LLM call
  summaries/{date}.md                               — one file per run date
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any


_BASE = Path("conversations")
_SUMMARIES = Path("summaries")


def _safe_repo(repo: str) -> str:
    """Replace path-unsafe characters so the filename is shell-friendly."""
    return repo.replace("/", "_")


class ConversationStore:
    """Saves LLM conversations and markdown summaries to the local filesystem."""

    def save_conversation(
        self,
        *,
        role: str,
        repo: str,
        issue: int,
        messages: list[dict[str, str]],
        response: Any,
    ) -> Path:
        """Persist a single LLM conversation to disk.

        Parameters
        ----------
        role:
            "planner" or "summarizer".
        repo:
            Full repo slug, e.g. ``"owner/repo"``.
        issue:
            Issue number (use 0 for run-level calls that aren't tied to one issue).
        messages:
            The list of dicts sent to the LLM (system + user at minimum).
        response:
            The raw ``litellm`` completion response object.

        Returns
        -------
        Path
            The path of the written file.
        """
        today = date.today().isoformat()
        folder = _BASE / today
        folder.mkdir(parents=True, exist_ok=True)

        safe_repo = _safe_repo(repo)
        filename = f"{safe_repo}#{issue}-{role}.json"
        filepath = folder / filename

        usage = getattr(response, "usage", None)
        cost: float | None = None
        try:
            import litellm
            cost = litellm.completion_cost(response)
        except Exception:
            pass

        payload: dict[str, Any] = {
            "date": today,
            "role": role,
            "model": getattr(response, "model", None),
            "repo": repo,
            "issue": issue,
            "messages": messages
            + [
                {
                    "role": "assistant",
                    "content": response.choices[0].message.content,
                }
            ],
            "cost_usd": round(cost, 6) if cost is not None else None,
            "tokens_in": getattr(usage, "prompt_tokens", None) if usage else None,
            "tokens_out": getattr(usage, "completion_tokens", None) if usage else None,
        }

        filepath.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return filepath

    def save_summary(self, summary_text: str) -> Path:
        """Append *summary_text* to ``summaries/{today}.md``.

        If the file already exists (e.g. multiple repos in one run) the new
        content is appended with a blank-line separator.

        Returns
        -------
        Path
            The path of the written (or updated) file.
        """
        _SUMMARIES.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        filepath = _SUMMARIES / f"{today}.md"

        if filepath.exists():
            existing = filepath.read_text()
            filepath.write_text(existing.rstrip() + "\n\n" + summary_text.strip() + "\n")
        else:
            filepath.write_text(summary_text.strip() + "\n")

        return filepath
