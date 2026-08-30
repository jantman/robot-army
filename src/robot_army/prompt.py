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

Milestone 012 adds a fourth section, ``DELIVERY``, immediately above the issue. Two things
about it are different from every other section here and are the reason it needs explaining:

* **It is unconditional.** No parameter, no configuration key, nothing for a caller to pass.
  The Spec Kit block is optional because it is wrong for a repository without Spec Kit;
  this one is right for every repository the daemon dispatches into.
* **It states its own precedence instead of inheriting it.** Everything else in this file
  ranks by position, earlier outranking later. That rule gives the wrong answer here: the
  issue body sits *below* ``DELIVERY`` and is meant to override it, so the block says so in
  its own last paragraph. It sits below ``speckit.GUIDANCE`` so that block's closing
  sentence — "the instruction above wins" — still covers exactly what it covered before.
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

#: What every session is told about how its work is delivered (milestone 012,
#: contracts/delivery-block.md).
#:
#: Fixed text, and unconditional — unlike ``speckit.GUIDANCE`` it takes no parameter and has
#: no configuration key, because there is nothing to decide. That block is *wrong* for a
#: repository without Spec Kit, so something has to choose per dispatch; this one is right for
#: every repository the daemon dispatches into, so a caller opt-in would be a knob with one
#: caller that always passes the same constant.
#:
#: Two sentences are load-bearing and were argued out in ``specs/012-prompt-branch-pr-safety``:
#:
#: * The push and the pull request are named as *exceptions* to "do not change the state of any
#:   system". Without that, the second instruction forbids the first one.
#: * The override rule is stated rather than implied. Every other precedence in this file is
#:   encoded by position, and position says the opposite here: the issue body is *below* this
#:   text and still outranks it.
#:
#: It never says "above" of the branch, either. The branch name appears in the issue section,
#: which sits below this block — so a direction word pointing up would read perfectly well and
#: be false.
DELIVERY = """\
Unless the issue below explicitly says otherwise, this is how the work is expected to be
delivered.

Do the work on the feature branch this session was started on, never on the repository's
default branch. When the work is done, commit it, push that branch to `origin`, and open a
pull request. Commits sitting on an unpushed branch are not a finished job: the worktree can
be reclaimed, and unpushed work is the one thing that cannot be recovered from it.

What you produce should be code and file changes in this git repository, arriving as commits
and pull requests. Do not satisfy the issue by changing the state of this machine or any other
system — do not deploy, restart, reconfigure, or edit something in place where the change
belongs in this repository instead. Pushing your branch and opening the pull request are the
exceptions. Running tests, running builds, and installing dependencies inside this worktree
are ordinary parts of doing the work and are not what this restricts.

If the issue below explicitly asks for something else — no pull request, a commit straight to
the default branch, or an action on a system — the issue wins. Nothing here is checked."""


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

    ``DELIVERY`` follows it and is not optional — see the module docstring. Milestone 007's
    promise that a ``None`` block reproduces the pre-007 output byte-for-byte was a statement
    about *that* change and is deliberately superseded by 012: every prompt now carries the
    delivery block. ``tests/unit/test_speckit_prompt.py`` still holds the whole assembly to a
    golden string, which is what notices when these sections are reshaped by accident.
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
    # Unconditional, and therefore not a parameter: there is nothing for a caller to decide.
    # Last of the guidance so it is read closest to the issue it defers to, and so the Spec
    # Kit block's own "the instruction above wins" keeps meaning what it meant when written.
    sections.append(DELIVERY)
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
