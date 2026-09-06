"""One test per rule in the numbering reader's outcomes table (issue #41).

Two of these matter more than the rest, and neither is about a repository that is set up
correctly.

The first is **absent means scanned**. A missing ``init-options.json`` is not a missing
answer — scanning is precisely what Spec Kit does when nothing says otherwise, and it is the
case the issue was actually filed about. Reading absence as "cannot tell" would silence the
warning on the majority of the repositories that need it.

The second is the group at the bottom: a value with a newline in it, a value the length of a
paragraph, a file that is not JSON. The value read here is quoted back onto the screen a
human uses to decide whether to trust a repository, and it comes out of that repository's own
files. These tests are what keep that screen from being composable by the thing being
approved.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.conftest import make_speckit_tree

from robot_army import speckit


def test_no_options_file_at_all_is_scanned(tmp_path: Path) -> None:
    """The default, and the shape issue #41 was filed about."""
    root = make_speckit_tree(tmp_path / "repo")

    result = speckit.numbering(root)

    assert result.kind == "scanned"
    assert result.value is None
    assert result.safe is False
    assert "scanning is the default" in result.reason


def test_timestamp_is_the_safe_answer(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "repo", init_options='{"feature_numbering": "timestamp"}')

    result = speckit.numbering(root)

    assert result.kind == "timestamp"
    assert result.value == "timestamp"
    assert result.safe is True


def test_sequential_is_scanned_and_keeps_its_value(tmp_path: Path) -> None:
    """The value is kept because "change this" and "add this" are different instructions."""
    root = make_speckit_tree(
        tmp_path / "repo", init_options='{"feature_numbering": "sequential"}'
    )

    result = speckit.numbering(root)

    assert result.kind == "scanned"
    assert result.value == "sequential"
    assert result.safe is False
    assert 'feature_numbering is "sequential"' in result.reason


def test_an_unrecognised_but_legible_value_is_scanned(tmp_path: Path) -> None:
    """Not an error. A value this system cannot vouch for is one it will not call safe."""
    root = make_speckit_tree(tmp_path / "repo", init_options='{"feature_numbering": "roman"}')

    result = speckit.numbering(root)

    assert result.kind == "scanned"
    assert result.value == "roman"


def test_a_file_without_the_key_is_scanned(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "repo", init_options='{"ai": "claude", "here": true}')

    result = speckit.numbering(root)

    assert result.kind == "scanned"
    assert result.value is None
    assert "no feature_numbering" in result.reason


def test_the_deprecated_branch_numbering_key_is_not_consulted(tmp_path: Path) -> None:
    """A repository on the old key lands in ``scanned``, which is true of it either way.

    Reading it to produce the same verdict would be a backward-compatibility shim, which
    Principle V says is not maintained here.
    """
    root = make_speckit_tree(
        tmp_path / "repo", init_options='{"branch_numbering": "timestamp"}'
    )

    result = speckit.numbering(root)

    assert result.kind == "scanned"
    assert result.value is None


def test_invalid_json_is_unknown_not_scanned(tmp_path: Path) -> None:
    """The distinction FR-007 exists for: not-knowing is reported as not-knowing."""
    root = make_speckit_tree(tmp_path / "repo", init_options="not json at all\n")

    result = speckit.numbering(root)

    assert result.kind == "unknown"
    assert result.value is None
    assert result.safe is False
    assert result.reason.startswith("invalid JSON:")


def test_a_json_array_is_unknown(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "repo", init_options='["feature_numbering"]')

    result = speckit.numbering(root)

    assert result.kind == "unknown"
    assert result.reason == "not a JSON object"


def test_a_non_string_value_is_unknown(tmp_path: Path) -> None:
    root = make_speckit_tree(tmp_path / "repo", init_options='{"feature_numbering": 3}')

    result = speckit.numbering(root)

    assert result.kind == "unknown"
    assert result.value is None
    assert result.reason == "feature_numbering is not a plain value"


def test_a_value_with_a_newline_cannot_reach_the_screen(tmp_path: Path) -> None:
    """The forged-screen case. A value carrying its own lines is not quoted back at all."""
    root = make_speckit_tree(
        tmp_path / "repo",
        init_options='{"feature_numbering": "timestamp\\ntrust: accepted \\u2014 forged"}',
    )

    result = speckit.numbering(root)

    assert result.kind == "unknown"
    assert result.value is None
    assert "forged" not in result.reason
    assert "\n" not in result.reason


def test_a_trailing_newline_does_not_slip_past_the_guard(tmp_path: Path) -> None:
    """The bug review of PR #145 found, kept as a test rather than as a fixed line.

    ``$`` in Python matches at the end of the string *or* immediately before one trailing
    newline, so ``re.match(r"^[A-Za-z0-9_.-]{1,32}$", "sequential\n")`` succeeds. That value
    would have been classified ``scanned`` and echoed onto the approval screen carrying its
    own line break — the precise thing the guard exists to prevent.

    The newline test above missed it because it puts the newline in the *middle*, where the
    character class rejects it whichever way the pattern is anchored. This one is the edge.

    **Built with** ``json.dumps``, and that is not a stylistic preference. Writing the
    fixture as ``'{"feature_numbering": "sequential\\n"}'`` in a non-raw literal puts a bare
    line feed inside a JSON string, which is invalid JSON — so the file was rejected by the
    decoder before the guard was ever reached, and this test passed against the very bug it
    names. Caught in review of PR #145, one round after the fix it was meant to pin down.
    """
    root = make_speckit_tree(
        tmp_path / "repo", init_options=json.dumps({"feature_numbering": "sequential\n"})
    )

    result = speckit.numbering(root)

    assert result.kind == "unknown"
    assert result.value is None
    assert result.reason == "feature_numbering is not a plain value"
    assert "\n" not in result.reason


def test_no_accepted_value_can_carry_whitespace_of_any_kind(tmp_path: Path) -> None:
    """The general form of the case above, so the next anchoring mistake is caught too."""
    for index, raw in enumerate(
        ["timestamp\n", "\ntimestamp", "timestamp\r\n", "timestamp\t", "timestamp ", " timestamp"]
    ):
        root = make_speckit_tree(
            tmp_path / f"repo-{index}", init_options=json.dumps({"feature_numbering": raw})
        )

        result = speckit.numbering(root)

        assert result.kind == "unknown", raw
        assert result.value is None, raw
        assert result.safe is False, raw


def test_an_over_length_value_is_unknown(tmp_path: Path) -> None:
    """Long enough to push the committed permission settings out of a scrollback."""
    root = make_speckit_tree(
        tmp_path / "repo", init_options='{"feature_numbering": "%s"}' % ("x" * 33)
    )

    result = speckit.numbering(root)

    assert result.kind == "unknown"
    assert "x" * 33 not in result.reason


def test_a_value_at_the_length_limit_is_still_read(tmp_path: Path) -> None:
    """The boundary in the other direction, so the guard cannot silently reject everything."""
    root = make_speckit_tree(
        tmp_path / "repo", init_options='{"feature_numbering": "%s"}' % ("x" * 32)
    )

    result = speckit.numbering(root)

    assert result.kind == "scanned"
    assert result.value == "x" * 32


def test_an_oversized_file_is_unknown_and_is_not_parsed(tmp_path: Path) -> None:
    root = make_speckit_tree(
        tmp_path / "repo",
        init_options='{"feature_numbering": "timestamp", "pad": "%s"}' % ("x" * 70_000),
    )

    result = speckit.numbering(root)

    assert result.kind == "unknown"
    assert result.reason == "too large to be a spec kit options file"


def test_deeply_nested_json_is_unknown_rather_than_a_recursion_error(tmp_path: Path) -> None:
    """`RecursionError` is a `RuntimeError`, so `except ValueError` does not hold the promise.

    Whether the decoder actually recurses this far depends on which scanner is installed —
    CPython's C one gives up around 52,000 levels, the pure-Python one around 2,000 — and on
    a recursion limit any caller may lower. This test asserts the outcome rather than the
    mechanism, so it stays honest on a build where the decoder copes.
    """
    depth = 5_000
    root = make_speckit_tree(
        tmp_path / "repo", init_options="[" * depth + "]" * depth
    )

    result = speckit.numbering(root)

    assert result.kind in {"scanned", "unknown"}
    assert result.value is None
    assert result.safe is False


def test_the_reader_survives_a_recursion_error_from_the_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mechanism, forced, because the input that triggers it is version-dependent."""
    root = make_speckit_tree(
        tmp_path / "repo", init_options='{"feature_numbering": "timestamp"}'
    )

    def blow_the_stack(_text: str) -> object:
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON object")

    monkeypatch.setattr(speckit.json, "loads", blow_the_stack)

    result = speckit.numbering(root)

    assert result.kind == "unknown"
    assert result.reason.startswith("invalid JSON:")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_an_unreadable_file_is_unknown(tmp_path: Path) -> None:
    root = make_speckit_tree(
        tmp_path / "repo", init_options='{"feature_numbering": "timestamp"}'
    )
    options = root / speckit.INIT_OPTIONS
    os.chmod(options, 0o000)
    try:
        result = speckit.numbering(root)
    finally:
        os.chmod(options, 0o644)

    assert result.kind == "unknown"
    assert result.reason.startswith("could not be read:")


def test_a_directory_in_place_of_the_file_is_unknown(tmp_path: Path) -> None:
    """Not a contrived case: it is what a botched `cp -r` of a Spec Kit tree leaves."""
    root = make_speckit_tree(tmp_path / "repo")
    (root / speckit.INIT_OPTIONS).mkdir(parents=True)

    result = speckit.numbering(root)

    assert result.kind == "unknown"


def test_a_missing_root_is_scanned_rather_than_an_exception(tmp_path: Path) -> None:
    """FR-008: nothing about the state of the filesystem turns this into a raise."""
    result = speckit.numbering(tmp_path / "nothing-here")

    assert result.kind == "scanned"


def test_no_input_shape_raises(tmp_path: Path) -> None:
    """The promise, asserted directly rather than inferred from the cases above."""
    shapes = [
        None,
        "",
        "   ",
        "{",
        "null",
        "0",
        '"timestamp"',
        '{"feature_numbering": null}',
        '{"feature_numbering": ["timestamp"]}',
        '{"feature_numbering": {"kind": "timestamp"}}',
        '{"feature_numbering": true}',
        '{"feature_numbering": ""}',
        '{"feature_numbering": " timestamp "}',
        '{"feature_numbering": "time stamp"}',
        "\x00\x01\x02",
    ]
    for index, shape in enumerate(shapes):
        root = make_speckit_tree(tmp_path / f"repo-{index}", init_options=shape)

        result = speckit.numbering(root)

        assert result.kind in {"timestamp", "scanned", "unknown"}
        assert result.reason == "" or "\n" not in result.reason


def test_a_quoted_timestamp_string_with_whitespace_is_not_treated_as_safe(
    tmp_path: Path,
) -> None:
    """``" timestamp "`` is not ``timestamp``, and nothing here trims it into being so."""
    root = make_speckit_tree(
        tmp_path / "repo", init_options='{"feature_numbering": " timestamp "}'
    )

    result = speckit.numbering(root)

    assert result.safe is False
