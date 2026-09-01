"""The four suppression paths, and the promise that detection cannot fail a dispatch.

``speckit_block`` is the only place the two halves meet — what the worktree contains and
what the configuration says about it — so this is where "detected but turned off" has to be
distinguishable in the record from "not a Spec Kit repository". A log that conflated them
would make a suppressed repository look like a broken one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.conftest import config_dict, make_speckit_tree, monkey_token

from robot_army import dispatch, speckit
from robot_army.config import parse


def build_config(repo_clone: Path, layout: Any, tmp_path: Path, **overrides: Any) -> Any:
    monkey_token()
    return parse(
        config_dict(repo_clone, layout, tmp_path / "worktrees", **overrides),
        tmp_path / "config.toml",
    )


def records(layout: Any, audit: Any) -> list[dict[str, Any]]:
    # AuditLog flushes per record, so there is nothing to flush here.
    lines: list[dict[str, Any]] = []
    for path in sorted(layout.log_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                lines.append(json.loads(line))
    return [r for r in lines if r.get("action") == "speckit.detect"]


def call(config: Any, audit: Any, worktree: Path) -> str | None:
    return dispatch.speckit_block(
        config=config,
        audit=audit,
        repo_key="demo",
        item_id=1,
        worktree_path=str(worktree),
    )


def test_detected_and_enabled_returns_the_block(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path
) -> None:
    config = build_config(repo_clone, layout, tmp_path)
    worktree = make_speckit_tree(tmp_path / "wt")

    assert call(config, audit, worktree) == speckit.GUIDANCE

    record = records(layout, audit)[-1]
    assert record["detail"]["detected"] is True
    assert record["detail"]["enabled"] is True
    assert record["detail"]["form"] == "skills"
    assert "suppressed_by" not in record["detail"]


def test_undetected_returns_nothing_and_says_why(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path
) -> None:
    config = build_config(repo_clone, layout, tmp_path)
    worktree = tmp_path / "plain"
    worktree.mkdir()

    assert call(config, audit, worktree) is None

    detail = records(layout, audit)[-1]["detail"]
    assert detail["detected"] is False
    assert detail["enabled"] is False
    assert "no spec kit scaffolding" in detail["reason"]
    assert "suppressed_by" not in detail, "nothing suppressed it; there was nothing to suppress"


def test_globally_disabled_is_recorded_as_suppression_not_absence(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path
) -> None:
    """The distinction the record exists for: turned off, not missing."""
    config = build_config(repo_clone, layout, tmp_path, speckit={"enabled": False})
    worktree = make_speckit_tree(tmp_path / "wt")

    assert call(config, audit, worktree) is None

    detail = records(layout, audit)[-1]["detail"]
    assert detail["detected"] is True
    assert detail["enabled"] is False
    assert detail["suppressed_by"] == "[speckit] enabled"


def test_disabled_for_one_repository_names_that_setting(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path
) -> None:
    config = build_config(
        repo_clone,
        layout,
        tmp_path,
        repos={"demo": {"path": str(repo_clone), "base_branch": "main", "speckit": False}},
    )
    worktree = make_speckit_tree(tmp_path / "wt")

    assert call(config, audit, worktree) is None
    assert records(layout, audit)[-1]["detail"]["suppressed_by"] == '[repos."demo"] speckit'


def test_a_detection_that_raises_is_a_miss_not_a_failed_dispatch(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path, monkeypatch: Any
) -> None:
    """FR-005, proved by injection rather than by inspection.

    ``speckit.detect`` promises never to raise. This asserts the behaviour when that promise
    is broken anyway, because the cost of being wrong about it is a repository that cannot
    dispatch at all on account of a paragraph of prose.
    """

    def explode(_root: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(speckit, "detect", explode)
    config = build_config(repo_clone, layout, tmp_path)

    assert call(config, audit, make_speckit_tree(tmp_path / "wt")) is None

    record = records(layout, audit)[-1]
    assert record["outcome"] == "error"
    assert "permission denied" in record["detail"]["reason"]


# -- milestone 039: configured instructions reach the block, and the record says so ----


def test_configured_instruction_reaches_the_block(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path
) -> None:
    config = build_config(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"implement": "push the branch and open a PR."}},
    )
    worktree = make_speckit_tree(tmp_path / "wt")

    block = call(config, audit, worktree)

    assert block is not None
    assert "push the branch and open a PR." in block
    assert block != speckit.GUIDANCE


def test_the_record_names_the_setting_and_never_the_text(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path
) -> None:
    """Research R6, and the Principle III gap the plan enumerates.

    The provenance is recorded because two callers need it. The *text* is not, because the
    log does not reconstruct a composed prompt today — the issue body, the repository's own
    instructions and the delivery block are all absent from it — and privileging configured
    prose over the issue body sitting beside it is indefensible.
    """
    configured_text = "push the branch and open a PR."
    config = build_config(
        repo_clone,
        layout,
        tmp_path,
        speckit={"commands": {"implement": configured_text}},
    )
    worktree = make_speckit_tree(tmp_path / "wt")

    call(config, audit, worktree)

    detail = records(layout, audit)[-1]["detail"]
    assert detail["instructions"] == {"implement": "[speckit.commands] implement"}
    assert configured_text not in json.dumps(detail)


def test_no_instructions_field_when_nothing_is_configured(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path
) -> None:
    config = build_config(repo_clone, layout, tmp_path)

    call(config, audit, make_speckit_tree(tmp_path / "wt"))

    assert "instructions" not in records(layout, audit)[-1]["detail"]


def test_suppression_withholds_the_configured_text_too(
    repo_clone: Path, layout: Any, audit: Any, tmp_path: Path
) -> None:
    """US1 scenario 4, FR-005. The configuration lives inside the gate, not beside it."""
    for overrides in (
        {"speckit": {"enabled": False, "commands": {"implement": "push it."}}},
        {
            "speckit": {"commands": {"implement": "push it."}},
            "repos": {
                "demo": {"path": str(repo_clone), "base_branch": "main", "speckit": False}
            },
        },
    ):
        config = build_config(repo_clone, layout, tmp_path, **overrides)

        assert call(config, audit, make_speckit_tree(tmp_path / "wt")) is None
