# Contract: `[pushover]`

## The section

```toml
[pushover]
token_file    = "~/.config/robot-army/pushover-token"   # the application's API token
user_key_file = "~/.config/robot-army/pushover-user"    # the account's user key
```

Absent by default. With no section, `config.pushover is None`, no channel is built, and no request to
Pushover is ever constructed — the same inert-when-unconfigured shape `[trello]` has.

Both paths accept `~`. Both files must exist and be mode 0600. Neither may contain anything but the
credential; trailing whitespace is stripped, because a file written with `echo` ends in a newline and a
newline in a form parameter is a 4xx nobody enjoys diagnosing.

## Why a separate section

`[notifications]` answers *what to say and how often*. `[health] webhook_url` and `[pushover]` answer
*where to say it*. Putting credentials under `[notifications]` would also make that section's meaning
depend on which of its keys were set.

## Why `token_file` and `user_key_file`

`token_file` matches `[github] token_file` and `[trello] token_file`, and matches Pushover's own `token`
parameter. `user_key_file` matches Pushover's `user` parameter and its dashboard label, and avoids the
ambiguity of calling both credentials "key". The issue's "api key and user key" maps to these two.

## Why files only

The issue asks for files, and Principle I forbids the knob with no caller. `[github]` and `[trello]`
carry both `*_env` and `*_file` because both were asked for; nothing asks here. Adding `*_env` twins
later is a two-line change if a need ever appears.

## Validation

Every rule is checked at load, aggregated with all other problems rather than raised on the first, and
each message names the offending key.

| # | Rule | Message shape | Spec |
|---|---|---|---|
| 1 | Both keys set, or neither | `[pushover] both token_file and user_key_file must be set (found only <key>)` | FR-004 |
| 2 | Each file exists | `[pushover] <key> does not exist: <path>` | FR-005 |
| 3 | Each file is mode 0600 | `[pushover] <key> must be mode 0600, found <mode>: <path>` | FR-005 |
| 4 | No literal credential inline | `[pushover] <key> appears to contain a literal credential. Credentials must come from a mode-0600 file, never this file — the repository is public` | FR-006 |
| 5 | No unknown keys | `[pushover] unknown key '<key>'` | — |

Rule 1 is an **error**, not a warning: a half-configured channel cannot send, and a warning would produce
a channel that silently never fires — the quiet lie milestone 004's contract argues against.

Rule 5 makes `[pushover]` a member of `_STRICT_KEY_SECTIONS`, joining `trello`, `dispatch`, `cleanup`,
`notifications`, and `speckit`. The rule that section states: a typo in a section that exists is a setting
that quietly does nothing, which is worse than one that is missing, because it looks applied.

### The literal-credential guard

`_TOKEN_PATTERNS` (`config.py:44`) cannot currently recognise a Pushover credential — they are 30
alphanumeric characters, matching none of the GitHub prefixes or the 32/64-hex Trello shapes. This is the
same gap milestone 003 named for Trello, where the guard could not match the credential it guarded and the
test that was supposed to cover it pasted a *GitHub*-shaped token into a Trello field.

The 30-character pattern is consulted **only** when scanning `[pushover]`, not added to the shared tuple.
Widening the shared tuple would apply it to `[github]` and `[trello]`, where a legitimate 30-character
alphanumeric value is improbable but possible — and there the failure mode is an error the author cannot
clear. Inside `[pushover]` the only legitimate values are paths, so a false positive is not reachable.

### Shared with `[trello]`

`_trello_credential` (`config.py:975`) already does exists-and-mode. That half is extracted into
`_secret_file(section_name, key, raw, problems)` and used by both, so this section gains a **caller**
rather than a copy. `[trello]`'s extra rule — exactly one of `*_env` or `*_file` — stays where it is,
because Pushover has no `*_env` form.

## The reworded warning

`config.py:794` warns today when `[notifications] events` is non-empty and `[health] webhook_url` is
empty. The condition becomes "no channel is configured" — webhook *or* Pushover satisfies it (FR-015):

```
[notifications] events are configured but no notification channel is set;
set [health] webhook_url or [pushover], or clear the events
```

Still a warning, not an error. The intent is legible and the resolution is obvious.

## Reachable configurations

| `[health] webhook_url` | `[pushover]` | Result |
|---|---|---|
| unset | absent | Nothing is sent. `events` non-empty → warning |
| set | absent | Today's behaviour, unchanged in every respect |
| unset | present | Every message goes to Pushover only |
| set | present | Every message goes to both, outcomes recorded independently |
