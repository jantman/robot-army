# Phase 0: Research

Seven decisions. Four are confirmations that an existing pattern applies; three (R1, R4, R5) were
live questions where the obvious answer was wrong or unavailable.

---

## R1 — Where the configuration sits, and why the two halves cannot share a name

**Decision**: Global instructions live in a sub-table `[speckit.commands]`, keyed by bare command
name. Per-repository overrides live in `[repos."owner/name".speckit_commands]`, keyed identically.

```toml
[speckit]
enabled = true

[speckit.commands]
implement = "when finished with implementation, commit, push the branch to origin, and open a PR."

[repos."jantman/other-repo"]
speckit = true

[repos."jantman/other-repo".speckit_commands]
implement = ""
```

**Rationale**: A sub-table keeps `[speckit]` homogeneous. The alternative — flat keys in `[speckit]`,
so `enabled = true` sits beside `implement = "…"` — mixes a switch with four bodies of prose in one
table, and `[speckit] specify = "…"` does not read as "what `/speckit-specify` is invoked with".

**The asymmetry in the names is forced, not sloppy.** The repository section already has a key named
`speckit`, holding the boolean that gates the block. TOML cannot make `speckit` both a boolean and a
table, so `[repos."k".speckit]` is unavailable and the override table has to be called something
else. `speckit_commands` was chosen because it reads as the same thing the global `[speckit.commands]`
is, and because the alternative — four flat keys `speckit_specify`, `speckit_plan`, `speckit_tasks`,
`speckit_implement` — quadruples `_REPO_KEYS` and makes "override just one" and "override all four"
look like different kinds of edit.

This is worth writing down because the mismatch looks like carelessness to anyone reading the two
sections side by side, and the reason is one sentence long.

**Alternatives considered**:

| Option | Rejected because |
|---|---|
| Flat keys in `[speckit]` | Mixes a switch with prose; reads badly at the call site. |
| `[speckit.prompts]` | "Prompt" already means the whole composed argument in `prompt.py`. A second meaning inside the same system is a word worth not spending. |
| A table keyed by repository inside `[speckit]` | Puts per-repository settings somewhere other than `[repos.*]`, which is the one place milestone 005 established for them. |
| Renaming the existing `[repos.*] speckit` boolean to free the name | A breaking config change to buy symmetry in a name. Principle V permits it; nothing justifies it. |

---

## R2 — May `config.py` import from `speckit.py`?

**Decision**: Yes. `config.py` imports `LIFECYCLE` from `robot_army.speckit` and uses it for both
validation and render order, so the four command names have exactly one definition.

**Rationale**: The import graph was walked rather than assumed. `speckit` transitively reaches
`audit`, `cardstates`, `db`, `migrations`, `models` and `states`; **none of them imports `config`**,
so the edge is acyclic today. It stays acyclic under one rule, which belongs in `speckit.py`'s
docstring: **`speckit.py` must never import `config`.** That is already its character — it is
deliberately plain filesystem reads with no configuration awareness, and `dispatch.py` is where the
two meet.

**Alternatives considered**:

- *Define the four names again in `config.py`, with a test asserting the two tuples are equal.* The
  test makes the duplication safe, and a codebase that argues against second sources of truth on
  nearly every page should not add one to avoid an import it has measured as harmless.
- *Move the resolution into `speckit.py` so nothing new is imported anywhere.* Puts a `Config`
  consumer inside the module whose whole design is not having one, and breaks the house pattern that
  puts `*_for(repo_key)` resolution on `Config` beside `model_for`, `permission_mode_for`,
  `base_branch_for` and `speckit_enabled_for`.

---

## R3 — Resolution shape

**Decision**: `Config.speckit_commands_for(key) -> tuple[CommandInstruction, ...]`, returning only
the commands whose effective text is non-empty, in `LIFECYCLE` order, each carrying its text and the
setting that produced it.

**Rationale**: This is `speckit_enabled_for`'s shape — answer plus provenance from one function —
extended from one boolean to four strings. That function's docstring already states the reason two
callers need the provenance together: "computing the reason separately at each site is how the two
come to disagree." Here the two sites are the `speckit.detect` audit record (FR-020) and
`robot-army repos --json` (FR-027), which is exactly the pair 007 was talking about.

Returning a sorted tuple rather than a mapping puts FR-011's ordering guarantee at the single point
where it can be tested once, instead of at the renderer, where a second caller could get it wrong.

**Alternatives considered**: returning `dict[str, str]` plus a separate provenance call — two
functions that can disagree, which is the failure this shape exists to prevent.

---

## R4 — Where the instructions render inside the block

**Decision**: Immediately **above** the block's closing paragraph, never below it.

**Rationale**: This looked like a formatting choice and is not. `GUIDANCE` ends with:

> Where any instruction above this paragraph conflicts with this one, the instruction above wins.

That sentence is how the block defers to a repository's own `.claude/robot-army.md`, which
`prompt.compose` places above it. Its scope is literally "above this paragraph". Configured text
appended after it would sit outside the precedence rule the block advertises, and FR-015's guarantee
— that a repository's own instructions still outrank everything in the block — would become false
by construction while every test still passed.

So the rendered instructions go between the constitution paragraph and the closing sentence, and the
closing sentence stays last.

**Alternatives considered**:

- *Append after the closing sentence.* Breaks FR-015 as described. This was the first sketch.
- *Prepend above the whole block.* Puts the maintainer's per-command instructions above the sentence
  explaining what the lifecycle is, which is backwards for a reader encountering it cold.
- *Rewrite the closing sentence to name the configured text explicitly.* Changes 007's fixed prose
  for no gain, and re-earns the golden-string test's objection every time the wording is touched.

---

## R5 — Empty string means different things in the two places

**Decision**: An empty or whitespace-only instruction is a **configuration problem** in
`[speckit.commands]`, and a **valid override meaning "no instruction for this command here"** in
`[repos.*].speckit_commands`.

**Rationale**: The asymmetry is not a special case; it falls out of what "override" means.

| Where | Absent means | Empty means | Are they the same state? |
|---|---|---|---|
| `[speckit.commands]` | nothing configured | nothing configured | **Yes** — so empty says nothing, and a value that says nothing is a mistake worth reporting. |
| `[repos.*].speckit_commands` | inherit the global | override the global with nothing | **No** — so empty is the only way to express "not in this repository". |

Without it, removing one instruction in one repository requires `speckit = false`, which removes the
entire guidance block — 007's paragraph about the lifecycle included. That is far too blunt for
"just not this paragraph, just here", and FR-025 exists to name the gap.

The project's standing rule is that a setting which quietly does nothing is worse than one that is
missing, because it looks applied. Reporting the global empty string honours that rule; accepting the
repository empty string does not violate it, because there the value does something.

**Alternatives considered**:

- *Accept empty everywhere.* A global empty string then means exactly what omitting it means, and
  the maintainer who typed one gets no signal either way.
- *A sentinel like `implement = false` for "none here".* A second type in a table of strings, to
  avoid an asymmetry that one table row explains.

---

## R6 — What the audit record carries

**Decision**: The existing `speckit.detect` record gains `instructions`, a mapping of command name to
the provenance string of the setting that supplied it. The instruction **text is not recorded**.

**Rationale**: Argued in full under Principle III in [plan.md](./plan.md#iii-total-accountability).
In short: the log already omits the issue body, the repository's `.claude/robot-army.md`, and the
delivery block, so it does not reconstruct a composed prompt today; recording up to 16,000 characters
of configured prose per dispatch while continuing to omit the issue body beside it would privilege
this one section for no defensible reason. The record names the setting, and the setting is in a
local hand-edited file.

This is the gap Governance requires the plan to enumerate, and it is enumerated there.

**Alternatives considered**: recording the full text (rejected above); recording a hash of it
(answers "did it change" and not "what did it say", which is the question anyone actually asks).

---

## R7 — Where "what will this repository be told?" is answerable offline

**Decision**: `robot-army repos --json` carries the resolved provenance per repository, in the
existing `speckit` payload object beside `detected`, `reason`, `form`, `enabled` and `suppressed_by`.
The human table is **not** given an eighth column.

**Rationale**: FR-027 and SC-008 inherit an expectation 007 set — that which repositories the
behaviour changes is answerable before labelling anything, offline. The `repos` table's Spec Kit cell
answers "is this repository getting the block at all", and that question and its four answers
(`yes` / `no` / `off` / `?`) are unchanged. Instructions are prose; a table cell cannot hold them,
and a cryptic marker meaning "something here is overridden" would raise the question without
answering it.

The `--json` payload names, per command, which setting supplied the instruction. Together with the
configuration file — which is where the maintainer wrote the text — that is a complete offline
answer, and it is the same pairing SC-006 uses for the log.

**Alternatives considered**:

- *An eighth table column.* Seven columns is already wide for a terminal.
- *A new verb that prints the composed block for a repository.* Genuinely useful, and out of scope by
  name in the spec — it is the "previewing a composed prompt" item, and it wants its own issue rather
  than arriving as a side effect of a configuration change.
