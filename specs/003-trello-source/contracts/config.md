# Contract: Configuration Additions

One new section. Absent by default: an installation that does not write it makes no board request at
all, and behaves exactly as it did in milestone 002 (FR-001).

```toml
[trello]
board_id      = "5f3a..."          # required when the section is present
label         = "AI-task"          # spec.md calls this the "tag" — see the note below
in_progress_list = "In Progress"   # list the card is moved to while a session runs
done_list        = "Done"          # list the card is moved to when the issue closes
poll_seconds  = 300                # R13: no conditional-request economy, so slower than GitHub
timeout_seconds = 20
max_retries   = 4
api_base      = "https://api.trello.com/1"

# Exactly one of each pair, mirroring [github]
key_env       = "TRELLO_API_KEY"
token_env     = "TRELLO_API_TOKEN"
# key_file    = "~/.config/robot-army/trello-key"
# token_file  = "~/.config/robot-army/trello-token"
```

**On the word "tag".** The spec says *tag* throughout, deliberately: this project already has a
`label`, the GitHub one that is the human gate, and calling both by the same word in a document about
a system that must never confuse them would be careless. The configuration key stays `label` because
that is what Trello's own API calls it, and every task and module follows the API. The two words name
the same thing; the spec's usage protects the reader, and this key's usage protects the implementer.

## Validation, at load

Following the existing rule that a typo inside a section that matters is an **error** rather than a
warning:

- `board_id` must be present and non-empty when `[trello]` exists.
- Exactly one of `key_env` / `key_file`, and exactly one of `token_env` / `token_file`.
- A value that looks like a literal credential in `key_env` or `token_env` is rejected with the same
  message the `[github]` equivalents use — the env *name* goes in the config, never the secret.
- A `*_file` must exist and be mode `0600`, as `[github] token_file` already requires.
- Unknown keys inside `[trello]` are an error.

## Validation, at startup

Beyond config syntax, and against the live board (R10, R11). These are preconditions, checked once
per process, and their failure disables **ingestion only** — dispatch of issues the author wrote
themselves is unaffected:

| Check | Failure |
|---|---|
| Board is reachable and the credentials work | Refuse ingestion, anomaly, loud log line |
| `prefs.permissionLevel == "private"` | Refuse ingestion, anomaly naming the actual level |
| Board members are recorded | **Never** a failure. Who else is on a private board is the author's decision; the list is logged so an unexpected card can be traced, not gated on |
| The configured label exists on the board | Refuse ingestion — a renamed label is otherwise indistinguishable from an empty board |
| Both lifecycle lists exist on the board | Refuse ingestion — a missing list is otherwise discovered mid-lifecycle, after the issue exists |

`robot-army doctor` performs all five and reports them, so the author can check the board without
starting the daemon.

## Secrets

Trello's documented authentication is a query string. This project uses the header form
(`Authorization: OAuth oauth_consumer_key="…", oauth_token="…"`) instead, and R3 explains why at
length: `audit.py` redacts by field name, so a secret embedded in a URL under a key called `url` would
pass straight through the choke point that exists to catch it.
