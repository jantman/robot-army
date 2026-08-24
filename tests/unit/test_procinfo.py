"""``/proc`` reads against a synthetic tree, including a process that vanishes (T026)."""

from __future__ import annotations

from tests.conftest import write_proc

from robot_army import procinfo


def test_starttime_parses_past_a_comm_containing_spaces_and_parens(tmp_path):
    """Field 2 is parenthesised and may itself contain spaces and parentheses, so the
    split has to start after the **last** ``)``. A naive whitespace split reads the
    wrong field and silently produces a PID-reuse guard that never matches."""
    write_proc(tmp_path, 1234, starttime="998877")
    assert procinfo.starttime(1234, root=tmp_path) == "998877"


def test_a_vanished_process_reads_as_gone_not_as_an_error(tmp_path):
    """/proc/<pid>/* raises at any moment, and that means "gone", not "error"."""
    assert procinfo.starttime(999999, root=tmp_path) is None
    assert procinfo.exe(999999, root=tmp_path) is None
    assert procinfo.cwd(999999, root=tmp_path) is None
    assert procinfo.cgroup(999999, root=tmp_path) is None
    assert procinfo.is_alive(999999, "1", root=tmp_path) is False


def test_a_process_that_vanishes_mid_read_is_not_fatal(tmp_path):
    directory = write_proc(tmp_path, 4321)
    (directory / "stat").unlink()
    assert procinfo.starttime(4321, root=tmp_path) is None
    assert procinfo.is_alive(4321, "12345", root=tmp_path) is False


def test_truncated_stat_yields_none_rather_than_an_index_error(tmp_path):
    directory = write_proc(tmp_path, 5555)
    (directory / "stat").write_text("5555 (claude) S 1 2 3\n", encoding="utf-8")
    assert procinfo.starttime(5555, root=tmp_path) is None


def test_stat_with_no_closing_paren_yields_none(tmp_path):
    directory = write_proc(tmp_path, 5556)
    (directory / "stat").write_text("garbage without parens\n", encoding="utf-8")
    assert procinfo.starttime(5556, root=tmp_path) is None


def test_is_alive_requires_pid_and_starttime_to_agree(tmp_path):
    """``pid`` alone is never sufficient identity (FR-038)."""
    write_proc(tmp_path, 2000, starttime="111")
    assert procinfo.is_alive(2000, "111", root=tmp_path) is True
    assert procinfo.is_alive(2000, "222", root=tmp_path) is False


def test_is_alive_with_no_recorded_starttime_degrades_to_existence(tmp_path):
    write_proc(tmp_path, 2001, starttime="111")
    assert procinfo.is_alive(2001, None, root=tmp_path) is True


def test_cwd_and_exe_resolve_symlinks(tmp_path):
    target = tmp_path / "worktree"
    target.mkdir()
    write_proc(tmp_path, 3000, cwd=str(target), exe="/usr/local/bin/claude")
    assert procinfo.cwd(3000, root=tmp_path) == str(target)
    assert procinfo.exe(3000, root=tmp_path) == "/usr/local/bin/claude"


def test_systemd_scope_is_extracted_from_cgroup(tmp_path):
    write_proc(
        tmp_path,
        4000,
        cgroup=(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
            "kitty-1996044-3.scope\n"
        ),
    )
    assert procinfo.systemd_scope(4000, root=tmp_path) == "kitty-1996044-3.scope"


def test_no_scope_in_cgroup_yields_none(tmp_path):
    write_proc(tmp_path, 4001, cgroup="0::/user.slice/user-1000.slice\n")
    assert procinfo.systemd_scope(4001, root=tmp_path) is None


def test_find_by_exe_matches_on_the_binary_not_the_command_line(tmp_path):
    """FR-039: never identify a process by matching its command line. M0 recorded a
    ``pgrep -f claude`` returning 18 matches of which 12 were the desktop application."""
    write_proc(tmp_path, 100, exe="/usr/bin/claude", cwd="/home/x/wt/a")
    write_proc(tmp_path, 101, exe="/usr/bin/bash", cwd="/home/x")
    write_proc(tmp_path, 102, exe="/opt/claude-desktop/claude-desktop", cwd="/home/x")

    found = procinfo.find_by_exe(("claude",), root=tmp_path)
    assert [pid for pid, _, _ in found] == [100]
    assert found[0][2] == "/home/x/wt/a"


def test_iter_pids_ignores_non_numeric_entries(tmp_path):
    write_proc(tmp_path, 10)
    write_proc(tmp_path, 20)
    (tmp_path / "self").mkdir()
    (tmp_path / "meminfo").write_text("x", encoding="utf-8")
    assert procinfo.iter_pids(root=tmp_path) == [10, 20]


def test_iter_pids_on_a_missing_root_is_empty_not_an_error(tmp_path):
    assert procinfo.iter_pids(root=tmp_path / "absent") == []
