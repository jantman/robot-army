"""Composing the prompt a dispatched session starts with (R19, FR-023).

The issue's title, body, canonical URL, and label list become a single prompt argument.
If the repository has a ``.claude/robot-army.md``, its contents are **prepended** as
dispatch-specific instructions — prepended rather than appended so repository-specific
standing instructions frame the task rather than trailing after it.

That file is read from the worktree, not from git: unlike the committed settings
fingerprint (which is a security boundary and must reflect what a fresh worktree will
honour), this is just prose for the session, and reading it from the prepared worktree is
what the session itself would see.

Milestone 007 adds one more optional section between those two: ``speckit.GUIDANCE``, when
the worktree is a Spec Kit project. Same reasoning about position, one rung down — the
repository's own instructions still frame everything, including that block.
"""

from __future__ import annotations

import re
from pathlib import Path

from robot_army.boundaries import Issue

INSTRUCTIONS_FILENAME = ".claude/robot-army.md"

#: Long enough to be useful, short enough that the whole thing stays a single argv entry
#: well inside ARG_MAX. An issue body larger than this is truncated with a pointer to the
#: URL, which the session can fetch.
MAX_BODY_CHARS = 60_000


def slugify(title: str, *, max_length: int = 40) -> str:
    """Lowercase, non-alphanumerics collapsed to single hyphens, cut at a hyphen (R18).

    Returns an empty string when the title reduces to nothing — a title of only emoji or
    CJK produces no slug, and the branch name simply omits it rather than carrying a
    meaningless placeholder.
    """
    lowered = title.lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if len(collapsed) <= max_length:
        return collapsed
    truncated = collapsed[:max_length]
    at_boundary = truncated.rsplit("-", 1)[0]
    return (at_boundary or truncated).strip("-")


def read_instructions(worktree_path: str | Path) -> str | None:
    """Return ``.claude/robot-army.md`` from the worktree, or ``None`` if absent."""
    path = Path(worktree_path) / INSTRUCTIONS_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    except OSError:
        return None
    return text.strip() or None


def compose(
    issue: Issue,
    *,
    repo_key: str,
    branch: str,
    instructions: str | None = None,
    speckit_block: str | None = None,
) -> str:
    """Build the prompt argument. Deterministic, so the same issue produces the same text.

    ``speckit_block`` is milestone 007's fixed guidance, present only when the worktree was
    detected as a Spec Kit project and the repository is not opted out. It goes **after** a
    repository's own instructions and **before** the issue: after, because position is how
    this function already encodes precedence and the repository's own words must outrank a
    generic paragraph; before, because an issue body can be 60,000 characters and guidance
    that follows one is guidance the session reads last.

    With it ``None`` the output is byte-identical to what this produced before the parameter
    existed (FR-010), which ``tests/unit/test_speckit_prompt.py`` holds to a golden string.
    """
    body = issue.body.strip()
    if len(body) > MAX_BODY_CHARS:
        body = (
            body[:MAX_BODY_CHARS]
            + f"\n\n[truncated at {MAX_BODY_CHARS} characters — full text at {issue.url}]"
        )

    sections: list[str] = []
    if instructions:
        sections.append(instructions.strip())
        sections.append("---")
    if speckit_block:
        sections.append(speckit_block.strip())
        sections.append("---")

    labels = ", ".join(issue.labels) if issue.labels else "(none)"
    sections.append(
        "\n".join(
            [
                f"You are working on {repo_key} issue #{issue.number} in a dedicated git",
                f"worktree on branch `{branch}`.",
                "",
                f"**Title**: {issue.title}",
                f"**URL**: {issue.url}",
                f"**Labels**: {labels}",
                "",
                "---",
                "",
                body if body else "_(the issue has no body)_",
            ]
        )
    )
    return "\n\n".join(sections).strip()


def session_name(repo_key: str, issue_number: int) -> str:
    """Identifiable in every listing that shows sessions (FR-024).

    The repo's owner is dropped: ``ra-specfiles-142`` is what the maintainer will
    recognise in a tab title, and the owner is nearly always themselves.
    """
    short = repo_key.split("/")[-1]
    return f"ra-{short}-{issue_number}"


def branch_name(prefix: str, issue_number: int, title: str) -> str:
    """``robot-army/issue-<n>-<slug>``. The slug is what makes ``git branch --list
    'robot-army/*'`` readable months later; it is omitted if it reduces to empty."""
    slug = slugify(title)
    stem = f"issue-{issue_number}"
    if slug:
        stem = f"{stem}-{slug}"
    return f"{prefix}/{stem}"


def worktree_dir(root: Path, repo_key: str, issue_number: int) -> Path:
    """``<root>/<repo>/issue-<n>/``, keyed on the issue **number only**.

    Deliberately not on the slug: the path is stored and reused across resume and
    restart, so it must stay stable if the issue is retitled (R18).
    """
    short = repo_key.split("/")[-1]
    return Path(root) / short / f"issue-{issue_number}"
