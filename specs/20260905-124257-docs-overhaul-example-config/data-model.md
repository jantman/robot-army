# Phase 1 data model: the example-config generator

Three types and one invariant. Everything here lives in `src/robot_army/exampleconfig.py`;
nothing is persisted, and no database or schema is touched.

## `KeySpec`

One configuration key as the example presents it.

| Field | Type | Meaning |
|---|---|---|
| `name` | `str` | The key, exactly as the loader accepts it. Must appear in `_KNOWN_KEYS[section]` or `_REPO_KEYS`. |
| `value` | `str` | The TOML value, already rendered — `"60"`, `'"robot-army"'`, `'["dispatch", "failure"]'`. Rendered rather than typed because TOML's spelling of a value is the thing being shown, and a type-driven writer would be a serialiser this project does not need. |
| `comment` | `str` | One line, saying what the key does. No trailing full stop; kept short enough to sit on the same line as the key. |
| `active` | `bool` | `True` renders `name = value`; `False` renders `# name = value`. |
| `why_commented` | `str \| None` | Required when `active` is `False`, forbidden otherwise. Rendered as an extra comment line so the reader is told why the key is off rather than left guessing. |

**Validation.** `value` must be non-empty. `comment` must be non-empty. The
`active`/`why_commented` pairing is checked at construction: a commented key with no reason
is the shape that produces an example nobody can act on.

## `SectionSpec`

One `[section]` of the document.

| Field | Type | Meaning |
|---|---|---|
| `name` | `str` | The TOML section name — `daemon`, `github`, `repos."owner/name"`. |
| `blurb` | `tuple[str, ...]` | Comment lines printed above the header, saying what the section is for. |
| `keys` | `tuple[KeySpec, ...]` | In the order they are rendered. |
| `active` | `bool` | `False` comments out the header as well as every key, for the sections that must stay absent to stay inert. |

**Validation.** A section with `active=False` must have every key `active=False` too —
otherwise the document contains a live key under a dead header, which is either a TOML error
or, worse, a key silently attributed to the previous section.

## The key-surface invariant

The generator does not hold a list of key names. It holds annotations *keyed by* name, and
at render time:

```
for section in _KNOWN_KEYS:            # the loader's own table
    for key in _KNOWN_KEYS[section]:   # the loader's own key set
        annotation = ANNOTATIONS[section][key]   # must exist
```

with the same for `_REPO_KEYS`. Three ways this can fail, and what each does:

| Condition | Result |
|---|---|
| A key in the loader's table has no annotation. | `ExampleConfigError`, naming the section and key, and saying that adding a key requires documenting it. Raised by the generator, so `robot-army example-config` reports it immediately — not only at test time. |
| An annotation names a key the loader does not accept. | The same error, from the other direction. This is the case that catches a key being *removed* from the loader while its annotation lingers. |
| A section in the loader's table has no `SectionSpec`. | The same error. |

This is what FR-019 means by one source of truth: `config.py` says which keys exist,
`exampleconfig.py` says what they mean, and the two cannot drift because the second is
checked against the first every time it runs.

**Ordering.** `_KNOWN_KEYS` is a `dict[str, set[str]]`, and sets have no order — so the
rendered order comes from the `SectionSpec`/`KeySpec` sequences, and the loader's tables
supply only membership. This is also what makes FR-016's byte-reproducibility hold: nothing
iterates a set to produce output.

## `RenderedExample`

Not a class — the generator's public surface is two functions.

| Function | Signature | Behaviour |
|---|---|---|
| `render()` | `() -> str` | The whole document as one string, ending in a newline. Deterministic: no clock, no environment, no filesystem, no configuration read (FR-016, FR-020). |
| `write(path, *, force=False, audit=None)` | `(Path, ...) -> None` | Atomic write per research R7: render fully, write to a temporary file beside the destination, `fsync`, `rename`. Refuses an existing path unless `force`. Records the outcome when an audit log is supplied. |

**Deliberately absent from `render()`**: any parameter. No section filter, no minimal mode,
no format switch. Each would have exactly one caller and no second use in hand (Principle I).

## Relationship to existing types

Read-only, one direction. `exampleconfig.py` imports `_KNOWN_KEYS` and `_REPO_KEYS` from
`config.py`; `config.py` gains no import and no change. The edge is acyclic and stays that
way for the same reason `speckit.py`'s docstring records its own rule: the loader must not
learn about the thing that documents it.

The dataclass defaults (`DaemonConfig.tick_seconds = 5` and its siblings) are **not** read
programmatically. Their values are transcribed into the annotations, and a test asserts the
rendered document parses to values the loader accepts — but the example deliberately shows
some keys at values that are *not* the default (`[github] author`, which has no usable
default, and the credential keys). Deriving values from the dataclasses would make those
three cases special-purpose exceptions to a mechanism that then earns nothing.
