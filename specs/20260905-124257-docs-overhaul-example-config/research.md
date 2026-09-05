# Phase 0 research: docs overhaul and example config

Nine questions the spec leaves to design. Each is a decision the implementation would
otherwise make by accident.

---

## R1 — What is the one source of truth for "what keys exist"? (FR-019, FR-024)

**Decision.** `config.py`'s existing `_KNOWN_KEYS` and `_REPO_KEYS` remain the sole
definition of the key surface. The generator **iterates them** and looks up an annotation —
a rendered value and a one-line comment — for each key it finds. A key present in those
tables with no annotation is a clean, named error from the generator itself, not a silent
omission. The generator's own ordering list decides only the order keys are printed in, and
a name appearing there that the tables do not contain is likewise an error.

**Rationale.** The spec forbids "a second hand-maintained list of key names". Prose cannot
be derived — someone has to write "how often the daemon polls GitHub" — so an annotation
table keyed by name is unavoidable. What is avoidable is that table being *authoritative*:
by iterating the loader's tables and demanding an annotation for every entry, the
completeness property is structural. Adding a key to the loader and running the generator
produces an error naming the key, before any test runs.

**Alternatives rejected.**

- *Derive comments from the dataclass docstrings.* The docstrings are paragraphs of design
  rationale, several of them fifteen lines long, and many attributes have none. It would
  produce an unreadable file and force the docstrings to be rewritten for a second audience.
- *Annotate in `config.py` beside each key.* Tempting, and it would collapse two tables into
  one — but `_KNOWN_KEYS` is a set-of-names used in hot validation paths, and turning it
  into a mapping to prose changes the loader to serve the generator. Principle I: the
  generator is the new thing, so the generator absorbs the cost.
- *Test-only enforcement, generator stays a flat template.* Weaker: the failure arrives at
  test time rather than at render time, and `robot-army example-config` would happily emit
  an incomplete file in the meantime.

---

## R2 — How is the environment-derived default rendered reproducibly? (FR-016, FR-020)

**Decision.** `[terminal] socket_glob` is rendered **commented out**, with its comment
stating that the default is `mykitty-*` under `$XDG_RUNTIME_DIR`, resolved when the daemon
starts. No other key reads the environment at render time.

**Rationale.** `TerminalConfig.socket_glob`'s default comes from `default_socket_glob()`,
which calls `runtime_dir()`. Three bad options and one good one:

- Emitting the resolved value (`/run/user/1000/mykitty-*`) embeds this machine's UID in a
  committed file, breaks FR-016 on any other machine, and breaks FR-020 by reading the
  author's environment.
- Emitting the literal string `$XDG_RUNTIME_DIR/mykitty-*` is worse than useless: TOML does
  not expand variables and neither does the loader, so it would silently configure a glob
  against a directory literally named `$XDG_RUNTIME_DIR`.
- Omitting the key entirely fails FR-011.

Commenting it out satisfies all three: the key is present and explained, nothing is read,
and the loader computes the correct per-machine default because the key is absent.

---

## R3 — How can "every key present" and "the file loads clean" both hold? (FR-011, FR-013)

**Decision.** A key may be rendered **active** or **commented**, and a commented key counts
as present for coverage. Four rules decide which:

| Rule | Keys it governs | Why |
|---|---|---|
| Mutually exclusive pairs: one active, its twin commented | `[github] token_env`/`token_file`, `[trello] key_env`/`key_file`, `[trello] token_env`/`token_file` | The loader requires *exactly one* of each pair. Both active is a validation error; both commented is an error too, for `[github]`. |
| Sections that must stay absent to stay inert: every key commented, **including the section header** | all of `[trello]`, all of `[pushover]` | `config.trello is None` and `config.pushover is None` are what make an unconfigured install make no outbound request. An active `[trello]` with an empty `board_id` is a board poll against nothing. |
| Keys validated against the filesystem: commented | `[github] token_file`, `[repos.*] path` | The loader requires `token_file` to exist at mode 0600 and `path` to be an existing git repository. No value can satisfy that on an arbitrary machine. |
| Environment-derived: commented | `[terminal] socket_glob` | R2. |

Everything else is rendered active at its documented default.

**Rationale.** This is the only arrangement under which the file is simultaneously complete,
loadable verbatim, and inert on copy. The coverage test counts a key as covered when it
appears as `key = …` or `# key = …` at the start of a line within its section.

**Consequence for `[github] author`.** It is a hard error when blank — the loader calls it
the FR-007 security boundary. The example renders it active with the placeholder
`"your-github-login"`, which is a valid string and loads clean while being obviously
not-a-real-value.

---

## R4 — What does the drift test have to arrange before it can load the example? (FR-013)

**Decision.** The test sets `HOME` to a temporary directory, creates the directories the
example names under it (`~/GIT` for `[paths] repo_root`, and `~/worktrees`), then calls
`config.load()` on the committed file **unmodified**.

**Rationale.** `[paths] repo_root` is validated for existence at load time, deliberately, so
that a missing clone root is one message rather than one per repository. FR-013 requires
the file to load "with no edits" — arranging the machine the file describes is not an edit
to the file. Creating a directory is what the author does too, and the quickstart says so.

**Alternative rejected.** Rendering `repo_root` commented out to dodge the check. It would
hide the single most important thing the author must decide, and the loader's default is
`~/GIT`, which is only right by accident.

---

## R5 — Where does the subcommand hook into the CLI? (FR-010, FR-017)

**Decision.** `robot-army example-config [--output PATH] [--force]`, handled in `main()`
**before** `load_config` is called, alongside `run` and `serve`, which are the two existing
commands handled there.

**Rationale.** This is not a style choice. `cli.main()` loads the config and builds a
`Context` before consulting the dispatch table, so every command in that table requires a
valid config to already exist. The entire purpose of this command is to be run on a machine
that has no config. Routing it through the table would make it fail with "config file not
found" — the exact situation it exists to fix.

**Shape.** Standard output by default, so `robot-army example-config > config.toml` works
and composes. `--output PATH` writes to a file; an existing file at that path is refused
with exit 3 (precondition not met, the existing code's meaning) unless `--force` is given.

**Alternative rejected.** `--output` defaulting to the real config path. Convenient, and a
foot-gun: a bare `robot-army example-config` typed to see what it looks like would then
land on the author's live configuration.

---

## R6 — What does this log? (Constitution III; FR-026)

**Decision.**

- **Writing to a file** (`--output`) is a state change outside the process and **is
  recorded** in the audit log: timestamp, component `example-config`, action
  `example_config.write`, target the resolved path, parameters `{"force": bool}`, and the
  outcome — success, or the failure with its error detail. The refusal to overwrite is
  recorded as a failure, because "I ran it and nothing happened" is exactly the question
  the log has to answer.
- **Writing to standard output** is **not** recorded, and this is the documented exception
  Principle III's exception path requires. Nothing outside the process changes: no file is
  created, no request is made, nothing is mutated. The record would be of the author having
  looked at something, which is neither reconstructible-from nor worth the disproportion.
- **Nothing in the documentation change logs anything.** Markdown files are not runtime.

**Complication, and how it is resolved.** The audit log's location comes from the `Layout`,
which comes from the config — and this command runs when there is no config. So: when
`--output` is given, the record is written to the *default* layout's audit log
(`~/.local/state/robot-army/logs/`), which is where it would be anyway for a default
install, and a failure to open that log does not fail the command — the file write is the
point. A `--config` was not read, so no non-default layout can be honoured, and the plan
states that limitation rather than pretending otherwise.

---

## R7 — What happens if it is killed halfway through? (Constitution IV)

**Decision.** The file write is atomic: render the whole document into memory, write it to a
temporary file in the destination's directory, `fsync`, then `rename` over the target. A
kill at any point leaves either no file or the complete file, never a half-written config
that the loader would then read as truncated TOML.

**Why it matters here specifically.** The destination is usually
`~/.config/robot-army/config.toml`. A partial write there is not an inconvenience — it is a
daemon that will not start, discovered later, with no clue as to why. Rendering happens
entirely before the first byte is written, so a generator error cannot truncate an existing
file either.

**The documentation half is not interruptible in any meaningful sense**: it is files in git.

---

## R8 — How does GitHub Pages publish this without a workflow? (FR-001, FR-007)

**Decision.** GitHub Pages' built-in *deploy from a branch* mode, source = default branch,
folder = `/docs`. A `docs/_config.yml` configures the built-in Jekyll build and, critically,
**excludes** the history files from the site:

```yaml
exclude:
  - roadmap.md
  - incident-*.md
  - verification-*.md
  - initial-planning/
```

**Rationale.** The issue asks for GitHub Pages "without needing a separate Actions job to
build and publish the docs". Branch deployment does exactly that: GitHub runs its own
`pages-build-deployment` job, which is not a workflow file in this repository and not one
the author maintains. `exclude` keeps the history files in git — FR-007 requires them to
stay — while keeping them out of the published navigation.

**One manual step, named here so it is not discovered later.** Enabling Pages and choosing
the branch and folder is a repository setting; it cannot be committed. The quickstart lists
it as the one thing the author does by hand.

**Alternatives rejected.** A `gh-pages` branch built by an Actions workflow (the thing the
issue explicitly rules out); deleting the history files (FR-007 forbids it); moving them
out of `docs/` (churn that breaks every existing link to them for no gain, since `exclude`
solves it).

**Theme.** `theme: jekyll-theme-primer` — one of the themes the built-in build supports with
no plugin and no dependency. It renders Markdown as GitHub renders it, which is what the
author already reads.

---

## R9 — How is FR-004, "every subject appears exactly once", made checkable?

**Decision.** The plan carries a section-by-section mapping table from each of the README's
22 top-level sections to its destination guide page. Implementation follows the table;
review checks the table against the tree. A test asserts the weaker, mechanical half
(FR-025: every internal link resolves), because "nothing was lost" is a claim about meaning
and cannot be asserted by a test that does not understand the meaning.

**Rationale.** FR-004 is the requirement most likely to be quietly failed — a 1,180-line
file redistributed by hand loses a paragraph and nobody notices for a year. Writing the
mapping down before moving anything turns "did we lose something?" from a re-read of two
documents into a checklist. It also surfaces the two sections that genuinely split across
pages (`Running it` and `Where things live`) *before* they are split, rather than after.

**What is deliberately not built.** A test that greps the old README for headings and
asserts each appears somewhere in the guide. The README is being rewritten, not moved:
headings change wording, and such a test would fail on every legitimate edit while catching
nothing that matters.
