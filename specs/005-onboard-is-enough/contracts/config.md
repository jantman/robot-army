# Contract: Configuration changes

What milestone 005 adds to, changes in, and corrects about the configuration file. The complete file
is documented in [001's config contract](../../001-minimum-daemon/contracts/config.md); only the
deltas are here.

## Added

```toml
[paths]
repo_root = "~/GIT"        # where clones live; a repository's default location is <repo_root>/<name>

[hooks]
default_timeout_seconds = 300
# The steps every repository gets unless its own section says otherwise. Same shape as
# [repos.*] post_create, same per-step timeout ceiling, same validation.
post_create = [
  { run = "uv sync", timeout = 120 },
]
```

### `[paths] repo_root`

- One directory. Not a list, not a search path.
- Defaults to `~/GIT`.
- `~` is expanded, as the other `[paths]` values already are.
- Validated **at load**: absent, or present but not a directory, is a configuration problem reported
  alongside every other configuration problem. It is not discovered per repository at onboarding
  time, because "your root is missing" is one message, not one per repository.
- A repository's clone location is `<repo_root>/<name>`, where `name` is the second segment of its
  `owner/name` key. **One candidate.** No search, no walk, no `<repo_root>/<owner>/<name>` fallback.

### `[hooks] post_create`

- The same array-of-tables shape `[repos.*] post_create` takes, parsed by the same code and subject
  to `default_timeout_seconds` for any step that sets none.
- A repository's own `post_create` **replaces** these. It does not extend them, and there is no way
  to request both — the repositories that need their own steps need different steps, not extra ones
  (research R10).
- A repository with neither runs no preparation steps, which is exactly today's behaviour.
- These steps feed the startup timeout budget warning for **every repository that inherits them**.
  Feeding it once would under-report for the majority of repositories after this milestone.

## Changed

### `[repos.*] path` becomes optional

- Present: used as-is, and derivation is not attempted. Verified exactly as a derived path is — a
  configured path can be wrong as easily as a derived one (FR-007).
- Absent: derived from `repo_root`.
- Changing it after a repository is onboarded does **not** silently take effect. Dispatch is blocked
  pending `onboard --reapprove`, which shows the recorded path and the configured one. This mirrors
  how a changed settings fingerprint already behaves.

Every other `[repos.*]` key keeps its current meaning and its current fallback. The section becomes a
set of overrides rather than a registration.

### `[github] include_owned` and `[github] extra_repos` acquire a meaning

Both are parsed and validated today and read by nothing
([issue #8](https://github.com/jantman/robot-army/issues/8)). They become the allowlist for what may
be **onboarded**:

| Setting | Meaning |
|---|---|
| `include_owned = true` | any repository the configured author owns may be onboarded |
| `include_owned = false` | ownership alone does not permit onboarding; the repository must be listed |
| `extra_repos = [...]` | these specific repositories may be onboarded regardless of who owns them |

- A repository permitted by neither is refused at onboarding, naming which setting would have
  permitted it.
- Ownership is determined by looking up **the repository being named**. Enumerating the author's
  repositories is not required and is not done (research R5).
- The allowlist governs onboarding only. A repository already onboarded keeps working if the setting
  that permitted it later changes — revoking access means removing the onboarding record.

**This is a mistake guard, not a security boundary** (FR-026). It catches a typo and a wrong owner.
The security boundary is and remains the issue-author check, which cannot be disabled and is
untouched by this milestone. Any documentation that implies otherwise is wrong.

## Corrected

Three places describe `include_owned` as controlling polling. All three are wrong in the same two
ways — the key never controlled polling, and polling is not what it should govern.

| File | Current text | Becomes |
|---|---|---|
| `share/config.example.toml` | `include_owned = true  # poll every repo you own` | `# any repo you own may be onboarded` |
| `specs/001-minimum-daemon/contracts/config.md` | `include_owned = true  # enumerate the authenticated user's own repos` | the allowlist meaning above |
| `README.md` | describes per-repository configuration as required before a repository can be used | onboarding is enough; sections are for exceptions |

`specs/001-minimum-daemon/spec.md:576` records the original decision — "the author's own repositories
are enumerated from GitHub for the authenticated user, and a configured list adds repositories the
author does not own. Both still require explicit onboarding per FR-001." The second sentence is what
this milestone implements. The first is superseded: nothing enumerates, because nothing needs to.

## A configuration file after this milestone

The author's file today has one `[repos.*]` section per usable repository. After 005 it has one per
*exception*: the five whose derived path is another repository, the handful in nested grouping
directories, and the ~15 needing bespoke preparation steps.

```toml
[paths]
repo_root = "~/GIT"

[hooks]
post_create = [ { run = "uv sync", timeout = 120 } ]

# An exception: the derived path holds upstream's clone, not mine.
[repos."jantman/zoneminder"]
path = "~/GIT/jantman-zoneminder"

# An exception: this one needs something other than the shared step.
[repos."jantman/some-node-thing"]
post_create = [ { run = "npm ci", timeout = 300 } ]

# Everything else: nothing here at all. Onboarded, and that is enough.
```

## Validation summary

Following the existing rule that a typo inside a section that matters is an error, not a warning:

| Condition | Result |
|---|---|
| `repo_root` absent or not a directory | problem, refuses to start |
| Unknown key in `[paths]` | problem, as today |
| `[hooks] post_create` not an array of tables | problem, same message shape as the per-repository form |
| A step in `[hooks] post_create` with an unknown key | problem, as the per-repository form already gives |
| `[repos.*]` with no `path` | **valid** — this is the change |
| `[repos.*]` with a `path` that does not exist | problem at load, as today |
| Inherited step timeouts exceeding the startup budget | warning, per repository that inherits them |
