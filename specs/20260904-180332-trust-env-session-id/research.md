# Phase 0 Research: The session wrapper trusts only the identifiers its launcher gave it

**Feature**: `specs/20260904-180332-trust-env-session-id` | **Date**: 2026-09-04

Every decision below was checked against the code or measured in a prototype, not assumed.
The prototypes are recorded here because two of them changed the answer.

---

## D1 — The argument scan is deleted outright, not demoted to a fallback

**Decision**: `share/robot-army-session-wrapper.sh` reads the session id from
`ROBOT_ARMY_SESSION_ID` and from nowhere else. The `for a in "$@"` loop goes.

**Rationale**: The issue offers "invert the precedence and validate" as an alternative that
keeps a hand-invocation fallback. There is no caller that needs it. `dispatch.plan_launch`
(`src/robot_army/dispatch.py:686-700`) builds `session_env` in exactly one place, always
including `ROBOT_ARMY_SESSION_ID`, and every launch — first attempt, resume, restart — goes
through that one builder, because `resume_session_id` is a parameter of it rather than a
separate path. `kitty.open` (`src/robot_army/boundaries/kitty.py:225-226`) turns each entry
into `--env KEY=VALUE`, so the variable is delivered, not merely recorded. A fallback would
therefore be a second code path with no caller, which Principle I rules out — and keeping it
would preserve, in weakened form, the exact mechanism this feature exists to remove.

**Alternatives considered**: *Invert precedence and keep the scan.* Rejected: it leaves an
attacker-reachable path alive to serve a hypothetical user. *Scan only the arguments before
the prompt.* Rejected: it depends on the prompt staying last, which is a property of the
caller, not of the wrapper — the wrapper cannot verify it, so the defence would be one
refactor away from silently evaporating.

**Consequence to handle**: the wrapper's usage line, header comment, and the three existing
integration tests all pass the id through argv. All must move to the environment. That is
work, not a drawback: it makes the tests exercise the path production actually uses.

---

## D2 — The accepted session-id shape is the canonical UUID, not the issue's character class

**Decision**: `^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`

**Rationale**: `dispatch.py:1092` generates the id as `str(uuid.uuid4())`, so the canonical
form is what the system actually issues and nothing is lost by requiring it. The issue's
suggested `^[0-9a-fA-F-]{36}$` is looser for no benefit — measured, it **accepts a string of
36 dashes**:

```text
strict=no  loose=yes  value=------------------------------------
strict=yes loose=yes  value=0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f
strict=no  loose=no   value=../../../../.claude/sessions/x
```

Neither form is path-traversing, so this is not a hole either way; the point is that when
two checks cost the same, the one that admits only real identifiers is the one to write.

**Anchor behaviour verified, because this is where such checks usually fail.** In some regex
dialects `$` matches before a trailing newline, which would let `<valid-uuid>\n../x` pass a
test whose author believed it anchored. Measured in bash 5.3:

```text
ok=yes  0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f
ok=no   $'0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f\n'
ok=no   $'\n0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f'
ok=no   0d5b1f3e-9c2a-4f1b-8e77-6a1d2c3b4e5f/x
```

`[[ =~ ]]` anchors against the whole string. The embedded-newline bypass does not exist here,
and a test pins it so a future rewrite cannot reintroduce it.

**Alternatives considered**: *Reject only `/` and `..`.* Rejected: a denylist has to
enumerate what is dangerous, and the allowlist costs the same line. *Delegate validation to
the daemon.* Rejected: the wrapper is the process that builds the path, and a check that
lives anywhere else is a check the wrapper's own future callers can miss.

---

## D3 — The item id must be validated before anything opens a path built from it

**Decision**: `^[0-9]+$`, checked before `mkdir` and before `LOGFILE` is composed.

**Rationale**: It is a SQLite row id, so an integer is its true shape. It is unreachable by
untrusted input today; it is included because it is the same defect class one edit away, and
the check is one line. Ordering matters more than the pattern: today the script runs
`mkdir -p "$SPOOL_DIR" "$LOG_DIR"` and composes `LOGFILE` *before* any validation exists, so
validating in place would still let a bad id name a file in the act of being rejected. Both
checks therefore move above the `mkdir`, which is what makes FR-004's "creates no file or
directory" literally true rather than nearly true.

---

## D4 — C0 escaping is a substitution loop over the 28 remaining control characters

**Decision**: after the existing `\`, `"`, `\n`, `\r`, `\t` substitutions, loop over code
points 1-8, 11, 12 and 14-31, replacing each with its `\u00XX` form, using only `printf -v`
and parameter expansion.

**Rationale**: The wrapper may use only bash builtins plus `printf`, `date`, `mv` and `mkdir`
(M0 F19), so `sed`, `iconv` and `python` are all unavailable. Prototyped against every C0
character at once, plus quotes, a backslash and multi-byte UTF-8, and read back with
Python's strict parser:

```text
parsed OK; roundtrip: True
```

Both halves matter: the record parses, *and* the decoded text is byte-identical to the input,
so the escaping is not quietly lossy. Newline, carriage return and tab keep their existing
short forms — they are already correct, and rewriting them as `\u000a` and friends would
churn the format for nothing.

**Cost measured, because this runs over the whole composed prompt.** 28 global substitutions
across a 100 KB string:

```text
bytes: 102893
jesc seconds: 0.079
```

Under a tenth of a second, paid once per session (`ARGV_JSON` is computed once and reused by
both records). No guard is needed to skip the loop when the string is clean, and none is
added: a `[[ $s == *[range]* ]]` pre-check would depend on locale collation for the bracket
range, trading a real correctness risk for savings that do not matter.

**Why 0 and 127 are absent**: a NUL cannot reach a bash string through `argv` at all, so it
needs no handling; `DEL` (127) is not a control character JSON forbids, so escaping it would
be noise.

**Alternatives considered**: *A per-character rebuild of the string.* Rejected: quadratic in
bash and far slower on a prompt-sized input. *Escaping every non-ASCII byte too.* Rejected:
the existing records are UTF-8 and correct as they stand.

---

## D5 — A refusal is recorded on error output only, and this is a named Principle III exception

**Decision**: the refusal path writes one message to stderr and exits 2. It does not write to
the audit log, and does not write a spool record.

**Rationale**: The wrapper has no access to the audit log by construction — it is a bare
shell script running in a minimal environment specifically so it works when the daemon is
down, which is the property that made a file rather than an HTTP POST the right design
(research R5). Giving it audit access would contradict the constraint that is its reason for
existing. The action *is* recorded, in three places that already exist: the message on
stderr, which the session host captures and `--hold` keeps on screen; the absence of a spool
record, which the daemon's reconciliation already surfaces as a lost session; and the
`kitty.launch` audit entry, which records the full `argv` and `env` of the launch that was
refused. Exit status 2 is reused deliberately — it is already this script's status for its
own usage errors, and since no record is written there is no way for it to be mistaken for
the worker's verdict.

This gap is enumerated in the Constitution Check rather than left implicit, which is what
Principle III requires of an omission.

---

## D6 — Documentation is part of the change, not a follow-up

**Decision**: `docs/security-analysis.md` gains a resolution note for RA-16 and for RA-48,
and both table rows move to **Resolved**.

**Rationale**: FR-010, and the document already has the shape to follow — RA-15 was resolved
on 2026-09-04 with a dated paragraph explaining what was done and why each part was needed.
A security analysis whose findings are fixed in code but still listed as open is worse than
one that was never written, because it directs the next reader's attention to the wrong
place.
