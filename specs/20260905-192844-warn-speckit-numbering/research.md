# Research: warning at onboarding about scanned spec numbering

Seven decisions. None needed a `[NEEDS CLARIFICATION]` marker; the issue's Human Decision settled
scope, and everything below is a choice about *how* that lands.

## R1 — Read the working tree, not the base ref

**Decision**: read `.specify/init-options.json` from the clone's working tree.

**Rationale**: the approval screen already reads two different kinds of thing from two different
places, and the rule dividing them is *what will actually be honoured*. Committed permission
settings are read at the base ref (`dispatch.read_committed_settings`) because a dispatched session
honours the committed file, not whatever is uncommitted in the primary clone. Feature numbering is
honoured by nothing in this system: it is read by `/speckit-specify` running inside a worktree,
which is a checkout of a branch cut from the working tree's repository — and, more to the point,
the warning is about the repository as a whole rather than about one dispatch.

The clinching argument is consistency with the module the reader lives in. `speckit.detect` and
`speckit.baseline` both read the filesystem directly. A reader that answered from a git ref would
make one screen state two facts about the same directory from two different sources, and the first
person to notice would have to work out which one was wrong.

**Alternatives considered**: reading at the base ref, for symmetry with the committed settings —
rejected above. Reading both and reporting a disagreement — a third outcome nobody asked for, on a
warning the issue explicitly asks not to over-fix.

## R2 — One reader with three outcomes, not a boolean and not a settings loader

**Decision**: `speckit.numbering(root) -> Numbering`, where `Numbering` carries a `kind` of
`timestamp`, `scanned`, or `unknown`, plus the raw `value` when there is a trustworthy one.

**Rationale**: two of the three outcomes have to be *told apart on the screen* (FR-007), so a
boolean cannot express the answer. Three named outcomes is the smallest thing that can. Returning
the parsed file, or a general "read this repository's tool configuration" helper, would be an
abstraction with exactly one caller — the thing Principle I names first.

`value` exists because FR-003 requires the warning to say what the numbering *is*, and "sequential"
and "not configured at all" are different sentences to the person reading them: one is a setting to
change, the other is a setting to add.

**Alternatives considered**: a `safe: bool` plus a `readable: bool` — two booleans encoding three
states, with a fourth combination that cannot occur and that every reader has to reason about
anyway. Rejected.

## R3 — The block goes last on the screen, immediately before the flush

**Decision**: after the committed-settings block and any re-approval fingerprint diff, immediately
before `result.flush_to(out)`.

**Rationale**: two constraints, and only one position satisfies both. It must appear before the
prompt (FR-009), which is what the flush point guarantees — and `operations.onboard` has exactly
one flush point on purpose, with a comment saying that adding a second reintroduces a doubling bug.
And it must not push the committed permission settings further from the top of the screen: that
text is the most important thing on that screen and the thing the milestone-011 rewrite existed to
get read.

Last also happens to be closest to the question, which is where a one-line advisory does the most
good.

**Alternatives considered**: in the header block beside `trust`, which is where a *property of the
repository* would naturally sit — rejected because the header answers "which repository is this",
and the numbering is not part of that answer.

## R4 — The finding rides the existing audit record; the reads are not logged

**Decision**: add `speckit` and `speckit_numbering` to the existing `repo.onboard` detail. Do not
log the file reads. Do not add an action.

**Rationale**: Principle III's standard is reconstruction — from the log alone, what did the system
do, to what, and with what result. The state-changing act here is the onboarding, which is already
one record; the numbering is a *property of what was approved*, which is exactly what the rest of
that detail already holds (`clone_path`, `verified_origin`, `owner_verdict`). Putting it there
answers "what was I shown when I said yes" in the place someone would already look.

The reads themselves fall under an exception the guide already documents for Spec Kit detection's
own file reads: they change no state outside the process, and the decision they inform is logged.
The plan names this exception explicitly, as Principle III requires, and the guide's table is
extended to cover it rather than left to imply it.

**Alternatives considered**: a `speckit.numbering` audit action — a record per `onboard` run saying
a file was read, on a command that is run once per repository and already writes a record. Volume,
no information.

## R5 — No knob to turn it off

**Decision**: no configuration key.

**Rationale**: it is one short block, on one interactive command, run once per repository, and it
already suppresses itself in every case where it would be noise (timestamp, or no Spec Kit). A key
to silence it would have one hypothetical user and would need a `SECTIONS` entry, a regenerated
example config, and a paragraph in the configuration guide — more surface than the feature.

**Alternatives considered**: none seriously. Recording the rejection because "add a setting" is the
reflex this principle exists to interrupt.

## R6 — `branch_numbering` is not consulted

**Decision**: read only `feature_numbering`.

**Rationale**: Spec Kit's own command text marks `branch_numbering` deprecated and slated for
removal, and it is the *git extension's* branch key rather than the core feature-directory key. A
repository still relying on it is, by definition, not one where `feature_numbering` is `timestamp`
— so it earns the warning through the absent-key path anyway, and the warning's suggested fix
(set `feature_numbering = "timestamp"`) is the correct advice for it too.

Principle V is explicit that backward-compatibility shims for outside consumers are not maintained.
Reading a deprecated key to produce the same outcome would be one.

**Alternatives considered**: consulting it to phrase the warning more precisely for such a
repository. The extra branch buys a nicer sentence in a case that resolves correctly without it.

## R7 — Report only; never write, never offer to write

**Decision**: the feature reads and prints. It does not modify the onboarded repository, and does
not prompt to.

**Rationale**: this is the boundary `speckit.py` already holds — it "never installs, upgrades, or
repairs Spec Kit in a repository", as the session guide states. Writing into a repository during
`onboard` would also mean an outward-facing, state-changing action on a command whose whole purpose
is for a human to decide about a repository *before* anything is done to it. And the Human Decision
is unambiguous about the remedy: warn, and if the warning is ignored, that is the maintainer's
choice.

**Alternatives considered**: an `onboard --fix-numbering` flag. A second, destructive mode on the
trust command, for a problem the issue rates as low impact.

## R8 — The value is quoted back only if it is safe to quote

**Decision**: accept `feature_numbering` as a value to echo only when it is a string of at most 32
characters matching `[A-Za-z0-9_.-]+`. Anything else — including a valid but wild string — is
`unknown`.

**Rationale**: the approval screen is the surface a human uses to decide whether to trust a
repository, and one of the things it prints comes out of that repository's own files. A value
containing newlines could add lines to that screen; a very long one could push the committed
permission settings out of a terminal scrollback. Neither is a dramatic attack and neither needs to
be possible. Restricting what gets echoed costs one regular expression and turns "the screen cannot
be forged by the thing being approved" into a property rather than a hope.

Treating a wild value as `unknown` rather than as `scanned` is the honest classification: the system
genuinely does not know what such a file means, and FR-007's whole point is that not-knowing is
reported as not-knowing.

**Alternatives considered**: truncating and escaping the value for display — more code, and it
still asserts a classification for a file nobody can read. Printing it raw — rejected above.
