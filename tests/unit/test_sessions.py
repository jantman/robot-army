"""Session registry parsing, the PID-reuse guard, and the credential prohibition.

Covers T058, T060 and T095. The registry is an *undocumented internal format* we depend
on, so the failure-path cases here are the point rather than an afterthought: an unknown
version, a truncated file, an absent directory, and a ``procStart`` that disagrees with
``/proc``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from tests.conftest import write_proc, write_registry

from robot_army import sessions


def build(tmp_path: Path, *, pid: int = 4242, **kwargs) -> tuple[Path, Path]:
    registry = tmp_path / "registry"
    proc = tmp_path / "proc"
    write_registry(registry, pid=pid, session_id=kwargs.pop("session_id", "s-1"), **kwargs)
    return registry, proc


def test_a_valid_live_entry_is_returned(tmp_path):
    registry, proc = build(tmp_path, proc_start="777")
    write_proc(proc, 4242, starttime="777")

    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    assert [e.session_id for e in scan.entries] == ["s-1"]
    assert scan.entries[0].pid == 4242


def test_an_absent_registry_directory_is_empty_not_an_error(tmp_path):
    scan = sessions.scan(registry_dir=tmp_path / "nope", proc_root=tmp_path / "proc")
    assert scan.entries == ()
    assert scan.unreadable == ()


def test_an_unknown_version_is_refused_and_reported(tmp_path):
    """Parsing is gated on ``version``; guessing that a new format follows the old one is
    exactly the assumption the guard exists to refuse."""
    registry, proc = build(tmp_path, version="9.0.1")
    write_proc(proc, 4242)

    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    assert scan.entries == ()
    assert scan.unknown_versions == ("9.0.1",)


def test_the_guard_matches_the_shape_the_worker_actually_writes(tmp_path):
    """The field is the worker's own version string, e.g. "2.1.239" — measured against a
    real registry file, not assumed. An integer guard rejected every live entry, degraded
    permanently to /proc, and destroyed the sessionId join dispatch confirmation needs."""
    assert sessions.version_is_known("2.1.239") is True
    assert sessions.version_is_known("2.1.0") is True, "patch releases must not trip it"
    assert sessions.version_is_known("3.0.0") is False
    assert sessions.version_is_known("2.2.0") is False
    assert sessions.version_is_known(1) is False
    assert sessions.version_is_known(None) is False
    assert sessions.version_is_known("garbage") is False
    assert sessions.parse_version("2.1.239") == (2, 1)


@pytest.mark.skipif(
    not (Path.home() / ".claude" / "sessions").is_dir(),
    reason="no live session registry on this machine",
)
def test_the_guard_accepts_this_machines_real_registry(tmp_path):
    """The check that would have caught the original mistake. Reads only the ``version``
    field, and never touches a ``.key`` file."""
    live = sorted((Path.home() / ".claude" / "sessions").glob("*.json"))
    if not live:
        pytest.skip("no registry files present")
    versions = {json.loads(p.read_text(encoding="utf-8")).get("version") for p in live}
    unknown = {v for v in versions if not sessions.version_is_known(v)}
    assert not unknown, (
        f"the version guard rejects this machine's real registry: {unknown}. "
        "Widen KNOWN_VERSIONS deliberately after looking at a sample"
    )


def test_a_missing_version_field_counts_as_unknown(tmp_path):
    """An unversioned file must not be silently skipped — that would look identical to
    "no sessions running", which is a very different fact."""
    registry, proc = build(tmp_path, version=None)
    write_proc(proc, 4242)

    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    assert scan.entries == ()
    assert scan.unknown_versions == (None,)


def test_a_truncated_file_is_reported_as_unreadable_not_crashing(tmp_path):
    registry, proc = build(tmp_path, truncate=True)
    write_proc(proc, 4242)

    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    assert scan.entries == ()
    assert len(scan.unreadable) == 1
    assert "unparseable JSON" in scan.unreadable[0]


def test_a_file_missing_session_id_is_unreadable(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "1.json").write_text(
        json.dumps({"version": "2.1.239", "pid": 1}), encoding="utf-8"
    )
    scan = sessions.scan(registry_dir=registry, proc_root=tmp_path / "proc")
    assert scan.entries == ()
    assert "missing sessionId or pid" in scan.unreadable[0]


def test_a_json_array_instead_of_an_object_is_unreadable(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "1.json").write_text("[]", encoding="utf-8")
    scan = sessions.scan(registry_dir=registry, proc_root=tmp_path / "proc")
    assert "expected a JSON object" in scan.unreadable[0]


def test_a_dead_process_is_filtered_out(tmp_path):
    registry, proc = build(tmp_path)
    proc.mkdir()  # no /proc/4242 at all
    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    assert scan.entries == ()


def test_pid_reuse_is_caught_by_the_proc_start_guard(tmp_path):
    """T095. Same PID, different ``procStart`` — a recycled PID belonging to something
    unrelated must not read as a live session."""
    registry, proc = build(tmp_path, proc_start="111")
    write_proc(proc, 4242, starttime="999")

    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    assert scan.entries == (), "a recycled pid must not be treated as alive"

    unfiltered = sessions.scan(registry_dir=registry, proc_root=proc, live_only=False)
    assert unfiltered.entries[0].alive(proc_root=proc) is False


def test_no_code_path_ever_opens_a_key_file(tmp_path, monkeypatch):
    """T060. ``<pid>.<hash>.key`` files sit alongside the registry, mode 0600, and appear
    to be session credentials. This asserts the prohibition mechanically rather than by
    inspection: every open() is recorded and the assertion is on the record."""
    registry = tmp_path / "registry"
    proc = tmp_path / "proc"
    write_registry(registry, pid=4242, session_id="s-1", proc_start="1")
    write_proc(proc, 4242, starttime="1")
    secret = registry / "4242.deadbeef.key"
    secret.write_text("super-secret-session-credential", encoding="utf-8")
    secret.chmod(0o600)

    opened: list[str] = []
    real_open = Path.open

    def recording_open(self, *args, **kwargs):
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    real_read_text = Path.read_text

    def recording_read_text(self, *args, **kwargs):
        opened.append(str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)
    monkeypatch.setattr(Path, "read_text", recording_read_text)

    sessions.scan(registry_dir=registry, proc_root=proc)
    sessions.scan_via_proc(("claude",), proc_root=proc)

    assert not [p for p in opened if p.endswith(".key")], (
        f"a .key file was opened: {[p for p in opened if p.endswith('.key')]}"
    )


def test_parse_entry_refuses_a_key_file_even_if_asked_directly(tmp_path):
    """Defence in depth at the one function that opens these files."""
    secret = tmp_path / "4242.deadbeef.key"
    secret.write_text("secret", encoding="utf-8")
    parsed = sessions.parse_entry(secret)
    assert parsed.entry is None
    assert "refusing to read credential-shaped file" in (parsed.error or "")


def test_the_proc_fallback_finds_workers_without_session_ids(tmp_path):
    """The degraded path cannot recover a ``sessionId`` — that only exists in the
    registry — so it is usable for the orphan sweep and never for the database join."""
    proc = tmp_path / "proc"
    write_proc(proc, 700, exe="/usr/bin/claude", cwd="/home/x/wt/issue-1")
    write_proc(proc, 701, exe="/usr/bin/bash", cwd="/home/x")

    scan = sessions.scan_via_proc(("claude",), proc_root=proc)
    assert scan.degraded is True
    assert [e.pid for e in scan.entries] == [700]
    assert scan.entries[0].session_id == ""


def test_under_root_classifies_orchestrator_owned_sessions(tmp_path):
    root = tmp_path / "worktrees"
    (root / "demo" / "issue-1").mkdir(parents=True)
    assert sessions.under_root(str(root / "demo" / "issue-1"), root) is True
    assert sessions.under_root(str(tmp_path / "elsewhere"), root) is False
    assert sessions.under_root(None, root) is False


def test_transcript_detection_finds_a_matching_jsonl(tmp_path):
    """M0 F19: a session can run, exit 0, and be permanently unresumable because a stray
    environment variable disabled transcript saving."""
    projects = tmp_path / ".claude" / "projects" / "-home-x-wt"
    projects.mkdir(parents=True)
    (projects / "abc-123.jsonl").write_text("{}", encoding="utf-8")

    assert sessions.transcript_exists("abc-123", home=tmp_path) is True
    assert sessions.transcript_exists("missing", home=tmp_path) is False


def test_transcript_detection_with_no_projects_directory_is_false(tmp_path):
    assert sessions.transcript_exists("abc", home=tmp_path) is False


def test_summarise_reports_the_aggregate_a_reconcile_pass_logs(tmp_path):
    registry = tmp_path / "registry"
    proc = tmp_path / "proc"
    root = tmp_path / "worktrees"
    (root / "demo").mkdir(parents=True)
    write_registry(registry, pid=10, session_id="ours", proc_start="1", cwd=str(root / "demo"))
    write_registry(registry, pid=11, session_id="theirs", proc_start="1", cwd=str(tmp_path))
    write_proc(proc, 10, starttime="1")
    write_proc(proc, 11, starttime="1")

    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    summary = sessions.summarise(scan, root)
    assert summary["sessions_found"] == 2
    assert summary["under_worktree_root"] == 1


# -- the gap that reads as free capacity (milestone 004, R4) ----------------


def test_a_missing_registry_directory_is_distinguished_from_an_empty_one(tmp_path):
    """The only registry failure that looks identical to an idle machine.

    No version is refused, no file is unreadable, the scan simply returns nothing — and
    an under-count of live sessions is the one capacity error that causes harm. So the
    distinction is made at the only place that can still tell the difference.
    """
    scan = sessions.scan(registry_dir=tmp_path / "nope", proc_root=tmp_path / "proc")
    assert scan.entries == ()
    assert scan.directory_missing is True


def test_a_path_that_is_a_file_rather_than_a_directory_counts_as_missing(tmp_path):
    not_a_directory = tmp_path / "registry"
    not_a_directory.write_text("", encoding="utf-8")
    scan = sessions.scan(registry_dir=not_a_directory, proc_root=tmp_path / "proc")
    assert scan.entries == ()
    assert scan.directory_missing is True


def test_an_unreadable_registry_directory_counts_as_missing(tmp_path):
    """Exists, will not be listed. Same conclusion as absent, different cause."""
    registry = tmp_path / "registry"
    registry.mkdir()
    registry.chmod(0o000)
    try:
        scan = sessions.scan(registry_dir=registry, proc_root=tmp_path / "proc")
    finally:
        registry.chmod(0o700)
    assert scan.entries == ()
    assert scan.directory_missing is True


def test_an_empty_but_present_registry_directory_is_not_missing(tmp_path):
    """The genuinely idle machine. This is the case that must stay dispatchable."""
    registry = tmp_path / "registry"
    registry.mkdir()
    scan = sessions.scan(registry_dir=registry, proc_root=tmp_path / "proc")
    assert scan.entries == ()
    assert scan.directory_missing is False
    assert scan.degraded is False


def test_a_populated_registry_directory_is_not_missing(tmp_path):
    registry, proc = build(tmp_path, proc_start="777")
    write_proc(proc, 4242, starttime="777")
    scan = sessions.scan(registry_dir=registry, proc_root=proc)
    assert scan.directory_missing is False


# -- the idle clock retirement hangs off (issue #138) -------------------------
#
# These are failure-path tests almost all the way down, and deliberately so. The whole
# safety argument for ending a process on the strength of an undocumented file is that
# every way of *failing* to establish idleness returns None rather than a number.


def entry_for(tmp_path: Path, **kwargs) -> sessions.RegistryEntry:
    """Parse one registry file and hand back the entry, bypassing the liveness filter."""
    registry, _ = build(tmp_path, **kwargs)
    parsed = sessions.parse_entry(registry / "4242.json")
    assert parsed.entry is not None, parsed.error
    return parsed.entry


def test_status_updated_at_is_parsed_and_gives_an_idle_duration(tmp_path):
    entry = entry_for(tmp_path, status="idle", status_updated_at=1_000_000_000_000)

    assert entry.status_updated_at == 1_000_000_000_000
    assert entry.idle_for(now_ms=1_000_000_060_000) == 60.0


def test_an_absent_status_updated_at_is_not_idle_at_any_age(tmp_path):
    """The conftest default. Every caller written before retirement existed lands here."""
    entry = entry_for(tmp_path, status="idle")

    assert entry.status_updated_at is None
    assert entry.idle_for(now_ms=9_999_999_999_999) is None


@pytest.mark.parametrize("value", ["1000000000000", 1e12, True, None, [], {"a": 1}])
def test_a_status_updated_at_of_the_wrong_type_is_treated_as_absent(tmp_path, value):
    """A wrong type must not raise: a worker upgrade cannot be allowed to take the daemon
    down. ``True`` is in the list because bool is an int subclass in Python."""
    entry = entry_for(tmp_path, status="idle", status_updated_at=value)

    assert entry.status_updated_at is None
    assert entry.idle_for(now_ms=9_999_999_999_999) is None


@pytest.mark.parametrize("status", ["busy", "compacting", "", None, "Idle", "IDLE"])
def test_only_the_exact_status_idle_counts_as_idle(tmp_path, status):
    """Matched for equality, not against an enumerated set of busy values: the things a
    worker can be doing are not ours to enumerate and will grow without telling us."""
    entry = entry_for(tmp_path, status=status, status_updated_at=1_000_000_000_000)

    assert entry.idle_for(now_ms=9_999_999_999_999) is None


def test_a_status_updated_at_in_the_future_is_not_idle(tmp_path):
    """A clock that disagrees with ours is not evidence of anything, and a negative
    duration compared against a threshold would read as "idle for ages"."""
    entry = entry_for(tmp_path, status="idle", status_updated_at=2_000_000_000_000)

    assert entry.idle_for(now_ms=1_000_000_000_000) is None


def test_idle_for_defaults_to_the_wall_clock(tmp_path):
    """The production call passes no ``now_ms``."""
    entry = entry_for(
        tmp_path, status="idle", status_updated_at=int(time.time() * 1000) - 120_000
    )

    measured = entry.idle_for()
    assert measured is not None
    assert 110 < measured < 200


def test_the_degraded_proc_path_is_never_idle(tmp_path):
    """``scan_via_proc`` cannot see a status at all, so nothing found that way is ever
    retirable — the degraded path must not become a way to end processes blind."""
    proc = tmp_path / "proc"
    write_proc(proc, 4242, exe="/usr/bin/claude")

    scan = sessions.scan_via_proc(("claude",), proc_root=proc)
    assert scan.entries
    assert all(e.idle_for(now_ms=9_999_999_999_999) is None for e in scan.entries)


def test_reading_the_idle_fields_opens_no_file_beyond_the_registry_entry(tmp_path):
    """T008. The new fields come from the already-decoded payload, so the credential
    prohibition is untouched — asserted mechanically rather than by inspection."""
    registry = tmp_path / "registry"
    proc = tmp_path / "proc"
    write_registry(
        registry,
        pid=4242,
        session_id="s-1",
        proc_start="1",
        status_updated_at=1_000_000_000_000,
    )
    write_proc(proc, 4242, starttime="1")
    secret = registry / "4242.deadbeef.key"
    secret.write_text("super-secret-session-credential", encoding="utf-8")
    secret.chmod(0o600)

    opened: list[str] = []
    real_read_text = Path.read_text

    def recording_read_text(self, *args, **kwargs):
        opened.append(str(self))
        return real_read_text(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "read_text", recording_read_text)
        scan = sessions.scan(registry_dir=registry, proc_root=proc)

    assert scan.entries[0].idle_for(now_ms=1_000_000_060_000) == 60.0
    assert [p for p in opened if p.startswith(str(registry))] == [str(registry / "4242.json")]


@pytest.mark.skipif(
    not (Path.home() / ".claude" / "sessions").is_dir(),
    reason="no live session registry on this machine",
)
def test_this_machines_real_registry_carries_the_idle_fields():
    """The premise issue #138's fix rests on, checked against reality rather than assumed.

    If a worker release drops these fields this test fails loudly, which is the outcome
    wanted: retirement would silently stop happening otherwise, and the symptom — sessions
    piling up again — looks nothing like its cause.
    """
    live = sorted((Path.home() / ".claude" / "sessions").glob("*.json"))
    if not live:
        pytest.skip("no registry files present")
    payloads = [json.loads(p.read_text(encoding="utf-8")) for p in live]
    usable = [p for p in payloads if sessions.version_is_known(p.get("version"))]
    if not usable:
        pytest.skip("no registry file of a known version")

    assert any("status" in p for p in usable), "no live entry carries `status`"
    assert any(isinstance(p.get("statusUpdatedAt"), int) for p in usable), (
        "no live entry carries an integer `statusUpdatedAt`; retirement can never fire"
    )
