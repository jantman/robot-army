"""Preparation steps run inside a freshly created worktree.

**Every step is bounded by a timeout, and the timeout kills the process group** (FR-013).
This is not defensive padding. M0 F15: ``git submodule update --init --recursive`` on a
real repository hung *indefinitely* because its ``.gitmodules`` uses ``git://`` URLs and
port 9418 is now dropped rather than refused. It does not error — it hangs. A hung hook
wedges a work item in ``dispatching`` forever with no session, no error, and nothing for
reconciliation to observe. The timeout is the whole reason this boundary exists as
something other than a ``subprocess.run`` call.

``link`` and ``copy`` are first-class step forms rather than shell commands because they
must be idempotent and readable (FR-015).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_army.boundaries import HookResult
from robot_army.subproc import run

if TYPE_CHECKING:
    from robot_army.audit import AuditLog
    from robot_army.config import HookStep


class SubprocessHookRunner:
    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit

    def run(
        self,
        steps: Any,
        worktree_path: str,
        clone_path: str,
        env: dict[str, str],
    ) -> HookResult:
        """Run every step in order. The first failure stops the sequence.

        A ``HookResult(ok=False)`` means the work item fails and **no session is ever
        launched into a partially prepared worktree** (FR-014).
        """
        for index, step in enumerate(steps):
            result = self._run_step(index, step, worktree_path, clone_path, env)
            if not result.ok:
                return result
        return HookResult(ok=True)

    def _run_step(
        self,
        index: int,
        step: HookStep,
        worktree_path: str,
        clone_path: str,
        env: dict[str, str],
    ) -> HookResult:
        description = step.describe()
        with self._audit.action(
            "hook.step",
            target=worktree_path,
            detail={"index": index, "step": description, "timeout_s": step.timeout},
        ) as outcome:
            if step.kind == "run":
                # shell=True is deliberate: `{ run = "make setup" }` is a shell command
                # the maintainer wrote in their own config file. The trust boundary here
                # is the OS user (Principle II), not this string.
                completed = run(  # noqa: S604
                    [step.value],
                    shell=True,
                    cwd=worktree_path,
                    env=env,
                    timeout=float(step.timeout),
                    audit=self._audit,
                    action="hook.subprocess",
                )
                outcome["exit"] = completed.returncode
                outcome["timed_out"] = completed.timed_out
                if completed.timed_out:
                    outcome["output"] = completed.output[:4000]
                    return HookResult(
                        ok=False,
                        step_index=index,
                        output=(
                            f"step {index} ({description}) timed out after "
                            f"{step.timeout}s and its process group was killed\n"
                            f"{completed.output}"
                        ),
                        timed_out=True,
                        description=description,
                    )
                if not completed.ok:
                    outcome["output"] = completed.output[:4000]
                    return HookResult(
                        ok=False,
                        step_index=index,
                        output=(
                            f"step {index} ({description}) exited "
                            f"{completed.returncode}\n{completed.output}"
                        ),
                        description=description,
                    )
                return HookResult(ok=True, step_index=index, description=description)

            if step.kind in ("link", "copy"):
                try:
                    placed = _place_file(step.kind, step.value, worktree_path, clone_path)
                except OSError as exc:
                    outcome["error"] = str(exc)
                    return HookResult(
                        ok=False,
                        step_index=index,
                        output=f"step {index} ({description}) failed: {exc}",
                        description=description,
                    )
                outcome["placed"] = placed
                return HookResult(ok=True, step_index=index, description=description)

            # Unreachable via a validated config; loud rather than silent if it happens.
            return HookResult(
                ok=False,
                step_index=index,
                output=f"step {index}: unknown step kind {step.kind!r}",
                description=description,
            )


def _place_file(kind: str, relative: str, worktree_path: str, clone_path: str) -> str:
    """Link or copy one path from the primary clone into the worktree, idempotently.

    Idempotency matters because preparation can be re-run after an interruption: an
    existing correct symlink is success, not a collision.
    """
    source = Path(clone_path) / relative
    destination = Path(worktree_path) / relative
    if not source.exists() and not source.is_symlink():
        raise FileNotFoundError(f"{kind} source does not exist in the primary clone: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if kind == "link":
        if destination.is_symlink():
            if os.readlink(destination) == str(source):
                return f"symlink already correct: {destination}"
            destination.unlink()
        elif destination.exists():
            raise FileExistsError(
                f"{destination} exists and is not a symlink; refusing to replace it"
            )
        destination.symlink_to(source)
        return f"symlinked {destination} -> {source}"

    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    return f"copied {source} -> {destination}"


class SimulatedHookRunner:
    """Logs each step it would have run, with its timeout, and reports success."""

    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit

    def run(
        self,
        steps: Any,
        worktree_path: str,
        clone_path: str,
        env: dict[str, str],
    ) -> HookResult:
        for index, step in enumerate(steps):
            self._audit.record(
                "hook.step",
                outcome="ok",
                target=worktree_path,
                simulated=True,
                detail={
                    "index": index,
                    "step": step.describe(),
                    "timeout_s": step.timeout,
                    "cwd": worktree_path,
                    "clone": clone_path,
                    "env": dict(env),
                },
            )
        return HookResult(ok=True, output="", description=f"{len(list(steps))} simulated step(s)")
