"""Noticing that a repository uses Spec Kit, and reading how far a session has got.

Two reads and one paragraph of text (milestone 007). Nothing here writes into a worktree,
runs a subprocess, or touches the network — which is not a coincidence to be preserved by
care but the reason this module exists as plain filesystem reads instead of as a boundary.

The thing worth knowing before changing anything here: **Spec Kit's extension hooks are
instructions an agent chooses to follow, not callbacks.** Nothing in Spec Kit calls out to
anything, so a hook that failed to run is indistinguishable from one whose moment has not
arrived. Every question this module answers is therefore answered from files on disk, which
cannot decline to be true. The full argument, and the three conditions that would make hooks
worth revisiting, are in ``specs/007-speckit-extensions/spec.md`` under Out of Scope.

**This module must never import** :mod:`robot_army.config` **at runtime.** Milestone 039
made the guidance block configurable, which meant ``config.py`` importing :data:`LIFECYCLE`
from here so the four command names have one definition; that edge is acyclic today and
stays acyclic only while nothing here imports back. :func:`guidance` therefore takes
already-resolved instructions rather than a ``Config`` — the ``TYPE_CHECKING`` import below
names their type and creates no import at run time — and ``dispatch.py`` remains the one
place where configuration and Spec Kit knowledge meet.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from robot_army import db
from robot_army.states import utcnow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from robot_army.audit import AuditLog
    from robot_army.config import CommandInstruction
    from robot_army.models import WorkItem

#: The four lifecycle commands, in the order they run. A repository missing any of them
#: cannot follow the flow the prompt would name, which is why detection needs all four.
LIFECYCLE: tuple[str, ...] = ("specify", "plan", "tasks", "implement")

#: Scaffolding. ``spec-template.md`` is the file ``/speckit-specify`` copies to produce a
#: spec; a ``.specify/`` without it cannot start the lifecycle whatever else is present.
SCAFFOLD_DIR = ".specify"
SCAFFOLD_TEMPLATE = ".specify/templates/spec-template.md"

#: The two forms the Claude integration installs its commands in. Both are invoked as
#: ``/speckit-<name>`` regardless, which is why the prompt block needs no variant.
SKILL_PATH = ".claude/skills/speckit-{name}/SKILL.md"
COMMAND_PATH = ".claude/commands/speckit.{name}.md"

#: Where features live. Spec Kit permits ``SPECIFY_FEATURE_DIRECTORY`` to point elsewhere;
#: a layout this does not recognise is a detection miss, never an error (FR-005).
SPECS_DIR = "specs"

#: The ladder, lowest rung first. ``implement`` is last and is the only rung not evidenced
#: by a file appearing — see ``observe``.
RUNGS: tuple[str, ...] = ("specify", "plan", "tasks", "implement")

#: Rung → the file whose existence proves it. ``implement`` is absent deliberately.
RUNG_FILES: dict[str, str] = {
    "specify": "spec.md",
    "plan": "plan.md",
    "tasks": "tasks.md",
}


#: What a session in a Spec Kit repository is told (milestone 007, contracts/prompt.md).
#:
#: Fixed text. It does not vary with the command form detected, the repository, or the
#: issue. Both installation forms are invoked as ``/speckit-<name>`` anyway, so there is
#: nothing here to vary.
#:
#: Milestone 039 amended 007's FR-009 — "identical text on every dispatch, in every
#: repository" — to *identical per effective configuration*: :func:`guidance` may insert
#: the maintainer's configured per-command instructions between this body and
#: :data:`GUIDANCE_CLOSING`. An installation that configures nothing still gets these bytes
#: and only these, which is what ``tests/unit/test_speckit_prompt.py``'s golden string
#: holds.
#:
#: The last paragraph is load-bearing: ``prompt.compose`` puts a repository's own
#: ``.claude/robot-army.md`` *above* this, and position is how that file already encodes
#: precedence. Saying so explicitly costs a sentence and removes an inference.
GUIDANCE_BODY = """\
This repository uses Spec Kit for feature work. Its lifecycle is `/speckit-specify` \u2192
`/speckit-plan` \u2192 `/speckit-tasks` \u2192 `/speckit-implement`, run in that order, and the
issue below is the input to `/speckit-specify` \u2014 hand it the issue rather than
re-describing it.

Use the lifecycle for work that adds or changes behaviour. Do not use it for a typo, a
one-line fix, a dependency bump, a documentation edit, or a question \u2014 going through four
phases for those costs more than it returns, and starting straight in is the right call.
That judgement is yours; nothing checks it and nothing is recorded as failed if you decide
this issue does not warrant the lifecycle.

If this repository has a constitution at `.specify/memory/constitution.md`, it governs, and
the plan must include its Constitution Check."""

#: The closing sentence, kept separate so :func:`guidance` can insert configured
#: instructions *above* it. Splitting the constant is what makes "nothing configured
#: produces the pre-milestone bytes" true by construction rather than by a ``rstrip`` that
#: has to stay correct --- see ``contracts/prompt-block.md``, "Absence is byte-identical".
GUIDANCE_CLOSING = """\
Where any instruction above this paragraph conflicts with this one, the instruction above
wins."""

GUIDANCE = GUIDANCE_BODY + "\n\n" + GUIDANCE_CLOSING

#: Introduces the maintainer's configured per-command instructions (milestone 039).
#:
#: "in addition to, not instead of" is FR-012 and is the whole reason this sentence exists
#: rather than the instructions simply being listed. Without it a configured ``specify``
#: instruction reads as a replacement for the body's "the issue below is the input to
#: ``/speckit-specify``", which is the one thing it must not do. One sentence covering all
#: four commands, rather than a special case for one of them.
INSTRUCTIONS_LEAD = """\
When you run these commands, invoke each with the instruction given for it below — in
addition to, not instead of, any input named for it above."""


def guidance(instructions: Sequence[CommandInstruction] = ()) -> str:
    """The block a Spec Kit session is sent, with the configured instructions in it.

    Pure, and deliberately ignorant of configuration: the caller resolves, this renders.
    That is what keeps the ``config`` → ``speckit`` import edge acyclic (see the module
    docstring).

    **The instructions go above** :data:`GUIDANCE_CLOSING`, never after it. That sentence
    reads "Where any instruction above this paragraph conflicts with this one, the
    instruction above wins", and it is how the block defers to a repository's own
    ``.claude/robot-army.md``, which ``prompt.compose`` places above the whole block. Its
    scope is literally *above this paragraph*, so text appended after it would fall outside
    the precedence rule the block advertises — FR-015 would be false by construction while
    every test still passed. Placing them above also has the desirable second effect of
    making the maintainer's own instructions outrank the block's generic paragraphs, which
    needed no new wording to establish (research R4).

    With nothing to render this returns :data:`GUIDANCE` itself, so an installation that
    configures nothing gets the pre-milestone bytes by construction rather than by a
    string-slicing round trip that has to stay correct (FR-013).
    """
    if not instructions:
        return GUIDANCE

    parts = [GUIDANCE_BODY, INSTRUCTIONS_LEAD]
    for instruction in instructions:
        parts.append(f"`/speckit-{instruction.command}`:")
        parts.append(instruction.text)
    parts.append(GUIDANCE_CLOSING)
    return "\n\n".join(parts)


@dataclass(frozen=True, slots=True)
class Detection:
    """Whether a directory is a Spec Kit project, and the evidence either way.

    Derived on demand and never stored: FR-006 requires that a repository which adopts
    Spec Kit after being onboarded gets the behaviour with no re-onboarding, and a cache
    written at onboarding is precisely what would prevent that.
    """

    detected: bool
    reason: str
    scaffolding: bool = False
    commands: tuple[str, ...] = ()
    form: str | None = None


@dataclass(frozen=True, slots=True)
class Phase:
    """Which rung a run has reached, and the feature directory it was read from."""

    rung: str
    feature_dir: str


def _exists(root: Path, relative: str) -> bool:
    """``Path.exists()`` that answers False for every reason a path can be unusable.

    Detection must never raise (FR-005): a permission error on one candidate path is a
    detection miss, not a failed dispatch.
    """
    try:
        return (root / relative).exists()
    except OSError:
        return False


def _find_commands(root: Path) -> tuple[tuple[str, ...], str | None]:
    """Which lifecycle commands are present, and in which form.

    Both forms are accepted per command rather than per repository, because a repository
    mid-migration between them genuinely has both and would otherwise read as broken.
    """
    found: list[str] = []
    forms: set[str] = set()
    for name in LIFECYCLE:
        if _exists(root, SKILL_PATH.format(name=name)):
            found.append(name)
            forms.add("skills")
        elif _exists(root, COMMAND_PATH.format(name=name)):
            found.append(name)
            forms.add("commands")
    if not forms:
        return tuple(found), None
    form = forms.pop() if len(forms) == 1 else "mixed"
    return tuple(found), form


def detect(root: str | Path) -> Detection:
    """Is this directory a Spec Kit project? (FR-001 through FR-005)

    Two halves, both required. The failure this guards against is concrete: a repository
    carrying ``.specify/`` from an installation whose agent commands are gone would get a
    prompt naming four commands that do not exist, and the session's only sensible response
    — ignoring the instruction — looks exactly like the instruction not working.

    Never raises. Every failure becomes a ``Detection`` whose ``reason`` is a sentence fit
    to appear in the log verbatim.
    """
    path = Path(root)
    try:
        scaffolding = (path / SCAFFOLD_DIR).is_dir() and (path / SCAFFOLD_TEMPLATE).is_file()
    except OSError as exc:
        return Detection(detected=False, reason=f"could not read {path}: {exc}")

    if not scaffolding:
        return Detection(
            detected=False,
            reason=f"no spec kit scaffolding at {path / SCAFFOLD_DIR}",
        )

    commands, form = _find_commands(path)
    missing = [name for name in LIFECYCLE if name not in commands]
    if missing:
        return Detection(
            detected=False,
            reason=(
                "spec kit scaffolding present but lifecycle commands missing: "
                + ", ".join(missing)
            ),
            scaffolding=True,
            commands=commands,
            form=form,
        )

    return Detection(
        detected=True,
        reason=f"spec kit present ({form})",
        scaffolding=True,
        commands=commands,
        form=form,
    )


#: A completed task in a Spec Kit ``tasks.md``. The only file-visible proof that
#: ``/speckit-implement`` ran, because unlike the other three commands it writes no new
#: file of its own — what it does is tick tasks off.
_DONE_TASK = re.compile(r"^\s*-\s*\[[Xx]\]", re.MULTILINE)


def baseline(root: str | Path) -> tuple[str, ...]:
    """The feature directories present right now, sorted (FR-013).

    Recorded once, when a worktree is created, and compared against for the rest of the
    item's life. The trap it exists for: a fresh worktree of a repository that uses Spec
    Kit contains every feature it has ever shipped, each with a ``tasks.md`` full of ticked
    boxes, so a phase derived from "what artifacts exist" would report ``implement`` the
    instant the worktree existed — on every item, forever.

    Never raises. An unreadable or absent ``specs/`` is an empty baseline, which is the
    honest answer: there were no feature directories we could see.
    """
    try:
        specs = Path(root) / SPECS_DIR
        return tuple(sorted(entry.name for entry in specs.iterdir() if entry.is_dir()))
    except OSError:
        return ()


def _rung_for(directory: Path) -> str | None:
    """The highest rung this feature directory evidences, or ``None`` for none at all."""
    reached: str | None = None
    for rung, filename in RUNG_FILES.items():
        try:
            if (directory / filename).is_file():
                reached = rung
        except OSError:
            continue
    if reached != "tasks":
        return reached
    try:
        text = (directory / "tasks.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # The file's existence is still evidence; its contents merely fail to prove more.
        return "tasks"
    return "implement" if _DONE_TASK.search(text) else "tasks"


def observe(root: str | Path, *, baseline: frozenset[str] | tuple[str, ...]) -> Phase | None:
    """How far this item's Spec Kit run has got, read from files (FR-012).

    Requires no cooperation from the session, which is the whole reason it reads files
    rather than waiting for a hook to report: an absent report means either "not there yet"
    or "did not bother", and nothing distinguishes them.

    ``None`` is returned for every "nothing to say" case — no ``specs/``, no directory
    outside the baseline, a new directory with no artifacts yet, an unreadable worktree, a
    worktree that cleanup has removed. Deliberately one value rather than several: they are
    all the same instruction to the caller, which is *leave what is recorded alone*.
    """
    known = frozenset(baseline)
    try:
        entries = [
            entry
            for entry in (Path(root) / SPECS_DIR).iterdir()
            if entry.is_dir() and entry.name not in known
        ]
    except OSError:
        return None

    candidates: list[tuple[int, float, str, str]] = []
    for entry in entries:
        rung = _rung_for(entry)
        if rung is None:
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            mtime = 0.0
        candidates.append((RUNGS.index(rung), mtime, entry.name, rung))
    if not candidates:
        return None

    # Highest rung, then most recently touched, then name descending — three keys so the
    # answer is deterministic even when a worktree holds two new features at once.
    _, _, name, rung = max(candidates)
    return Phase(rung=rung, feature_dir=f"{SPECS_DIR}/{name}")


def record_phase(
    conn: sqlite3.Connection,
    audit: AuditLog,
    item: WorkItem,
) -> Phase | None:
    """Advance one item's recorded phase if the files say it moved (FR-012 – FR-017).

    Returns the new ``Phase`` when something was written, ``None`` otherwise. The rules are
    data-model.md's, and each exists because of a specific way this could be wrong:

    * **No baseline, no observation.** ``NULL`` means nothing can be attributed to this
      item, so nothing is. Deriving a baseline now would classify the session's own feature
      directory as pre-existing and produce the same silence with none of the honesty.
    * **Detection gates observation.** A repository that merely has a ``specs/`` directory
      containing a ``spec.md`` is not a Spec Kit project, and ``specs/`` is not a rare
      enough name to carry that meaning alone.
    * **Absence never clears.** ``observe`` returning ``None`` leaves every column
      untouched, so a worktree removed by cleanup leaves the last known stage standing as
      history rather than deleting something the log cannot restore.
    * **Advance only, within a directory.** A ladder that could descend would turn an
      ordinary edit of an artifact into a spurious transition.
    * **A change of directory is recorded as such**, whatever its height, with both names —
      the "two features in one worktree" case, which would otherwise read as a bug.

    The audit line is written inside the transaction and therefore reaches the log before
    the commit, matching ``states.transition_work_item``: a crash between them re-derives
    the same phase from the same files and records it again, which duplicates a line at
    worst and never loses one.
    """
    if not item.worktree_path or item.speckit_baseline is None:
        return None
    if not detect(item.worktree_path).detected:
        return None

    try:
        known = tuple(json.loads(item.speckit_baseline))
    except (TypeError, ValueError):
        # A baseline we cannot read is a baseline we do not have. Same rule as above:
        # silence rather than a guess.
        return None

    phase = observe(item.worktree_path, baseline=known)
    if phase is None:
        return None

    same_directory = item.speckit_feature_dir == phase.feature_dir
    if (
        same_directory
        and item.speckit_phase in RUNGS
        and RUNGS.index(phase.rung) <= RUNGS.index(item.speckit_phase)
    ):
        return None

    detail: dict[str, object] = {
        "from": item.speckit_phase,
        "to": phase.rung,
        "feature_dir": phase.feature_dir,
    }
    if item.speckit_feature_dir and not same_directory:
        detail["previous_feature_dir"] = item.speckit_feature_dir

    with db.transaction(conn):
        audit.record(
            "speckit.phase",
            outcome="ok",
            entity_type="work_item",
            entity_id=item.id,
            target=item.worktree_path,
            dry_run=item.dry_run,
            detail=detail,
        )
        db.update_work_item_columns(
            conn,
            item.id,
            speckit_phase=phase.rung,
            speckit_feature_dir=phase.feature_dir,
            speckit_phase_at=utcnow(),
        )
    return phase
