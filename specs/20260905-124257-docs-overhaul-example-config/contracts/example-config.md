# Contract: the generated `config.toml`

What the rendered document is guaranteed to be. Consumers are the author copying it, the
guide page embedding it, and three tests.

## Shape

```toml
# robot-army example configuration
# <a short preamble: what this file is, where it goes, that it is generated>

# <section blurb line>
# <section blurb line>
[section]
key = value  # what the key does
# other_key = value  # what the key does
#   (why this one is commented)
```

- One blank line between sections; none within a section.
- The comment for a key sits on the same line as the key.
- A commented-out key carries a second, indented comment line giving the reason.
- The file ends with exactly one newline.

## Section order

Fixed, and chosen to read top-to-bottom as the pipeline the guide describes rather than as
the loader's internal order:

`paths` → `github` → `worker` → `dispatch` → `daemon` → `speckit` → `speckit.commands` →
`trello` → `notifications` → `pushover` → `cleanup` → `hooks` → `terminal` → `web` →
`health` → `repos."owner/name"`.

The order is the generator's, not the loader's: `_KNOWN_KEYS` is a mapping to *sets*, which
have no order, and iterating one would break byte-reproducibility (FR-016).

## Active versus commented

Every key the loader accepts appears. Four rules decide whether it is live.

| Rule | Keys | Reason |
|---|---|---|
| **Mutually exclusive pair** — first active, twin commented | `[github] token_env` / `token_file`; `[trello] key_env` / `key_file`; `[trello] token_env` / `token_file` | The loader requires exactly one of each pair; two active is a validation error. |
| **Inert-when-absent section** — every key commented, *header included* | all of `[trello]`, all of `[pushover]` | `config.trello is None` and `config.pushover is None` are what make an unconfigured install issue no outbound request. An active `[trello]` with an empty `board_id` polls nothing, forever. |
| **Filesystem-validated** — commented | `[github] token_file`; `[repos.*] path` | The loader requires `token_file` to exist at mode 0600 and `path` to be an existing git repository. No value satisfies that on an arbitrary machine. |
| **Environment-derived** — commented | `[terminal] socket_glob` | Its default is computed from `$XDG_RUNTIME_DIR`. Emitting the resolved value embeds this machine's UID and breaks FR-016; emitting the literal `$XDG_RUNTIME_DIR/...` silently configures a directory of that literal name, because neither TOML nor the loader expands variables. Commented, the loader computes the right value per machine. |

The whole `[repos."owner/name"]` section is rendered commented: it names a repository that
does not exist, and unknown keys inside `[repos.*]` are an **error** rather than a warning,
so a live example section is a config that fails to load the moment the author edits a key
name.

A **commented key counts as present** for coverage purposes. This is the rule that lets
FR-011 (every key appears) and FR-013 (the file loads clean) both hold.

## Values

| Property | Guarantee |
|---|---|
| Defaults | A key rendered active carries the loader's documented default, except where the default is unusable. |
| `[github] author` | The placeholder `"your-github-login"`. It has no usable default — the loader treats a blank author as a hard error and calls it the FR-007 security boundary — so the example must supply something, and something obviously not real. |
| Credentials | **None.** `*_env` keys name an environment variable (`"GITHUB_TOKEN"`); `*_file` keys name a path. No value in the document may match the loader's own `_TOKEN_PATTERNS` or its Pushover credential shape — if one did, the loader would refuse the file it just generated. |
| Personal data | None. No hostname, no username, no absolute path outside `~`. |

## Guarantees a consumer may rely on

1. **Complete** — every key in `_KNOWN_KEYS` and `_REPO_KEYS`, active or commented, with a
   comment. Enforced at render time, not only by test: an un-annotated key raises.
2. **Loadable** — `config.load()` accepts it unmodified, on a machine where `[paths]
   repo_root` exists. Zero problems; warnings are permitted and expected to be zero too.
3. **Inert** — copying it verbatim configures no board poll, no notification, and no
   cleanup. Every outward-facing or irreversible behaviour is off, as the Operating
   Constraints require of a default.
4. **Reproducible** — byte-identical across machines, users, clocks, and runs. No timestamp
   and no version string in the output; a "generated on <date>" banner is specifically
   excluded, because it would break this guarantee and therefore the drift test.
5. **Committed** — the identical bytes live at `share/config.example.toml`, and the guide's
   configuration page presents them.
