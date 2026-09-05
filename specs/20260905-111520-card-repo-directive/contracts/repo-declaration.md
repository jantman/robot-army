# Contract: the `robot-army:` declaration on a card

This is the grammar the author writes and the behaviour it is guaranteed to produce. It is
the surface a person interacts with, so it is written in terms of what is on the card, not in
terms of what the code does.

## The line

```
robot-army: <reference>
```

A line of a card's text is a **declaration** when, after every backtick on that line has been
removed, the whole line consists of:

1. optional spaces or tabs,
2. the word `robot-army` in any letter case,
3. optional spaces or tabs,
4. a colon,
5. optional spaces or tabs,
6. a **reference**: one unbroken run of non-whitespace characters,
7. optional spaces or tabs, and nothing more.

"A line of a card's text" means a line of the card's title or of its description. The title is
one line, so a title that is nothing but a declaration is one.

### Accepted

| On the card | Why |
|---|---|
| `robot-army: jantman/demo` | the plain form |
| `robot-army: https://github.com/jantman/demo` | a pasted browser URL |
| `robot-army: github.com/jantman/demo.git` | scheme and `.git` are both optional |
| `robot-army: /home/jantman/git/demo` | the local clone |
| `robot-army: /home/jantman/git/demo/src/thing.py` | a file inside the clone names the clone |
| `robot-army: ~/git/demo` | `~` is expanded |
| `  Robot-Army :  jantman/demo  ` | case and padding are tolerated |
| `` `robot-army: jantman/demo` `` | the documentation renders it this way; the backticks are ignored |

### Not a declaration

| On the card | Why |
|---|---|
| `see robot-army: jantman/demo for context` | the line has other content; this is prose |
| `robot-army: jantman/demo (the new one)` | likewise — the reference is one run of non-space |
| `robot-army:` | no reference; treated as though the line were absent |
| `robot-army jantman/demo` | no colon |
| `robot-army: jantman/demo` *split across two lines* | the pattern is anchored to one line |

A line that is not a declaration changes nothing. The card resolves exactly as it would have
without it.

## What a reference selects

A reference selects **at most one repository**, and only ever an **onboarded** one. The three
accepted spellings and the filter applied to them are exactly those the rest of a card's text
already gets:

- a `github.com/<owner>/<name>` URL, with or without a scheme, `www.`, or a `.git` suffix;
- a bare `<owner>/<name>`;
- a filesystem path equal to, or inside, a repository's local clone, with `~` expanded.

A reference that does not resolve to an onboarded repository selects **nothing**. It does not
select a repository by partial match, by last path segment, or by any similarity rule. This is
the property that makes the declaration safe on a card containing pasted log output: the
parser can be fooled, and it still cannot cause an issue to be filed anywhere the author did
not onboard.

## What the card as a whole resolves to

Let *D* be the declarations on the card, and *K* the set of distinct repositories their
references select.

| | Outcome |
|---|---|
| *D* is empty | **Unchanged.** The card's whole text is scanned as it is today, and resolves, or is held, exactly as before this feature. |
| every reference in *D* selects a repository, and \|*K*\| = 1 | **Resolved** to that repository. Every other repository reference anywhere in the card's text is disregarded. |
| every reference in *D* selects a repository, and \|*K*\| ≥ 2 | **Held.** The reason names the repositories the lines selected. |
| any reference in *D* selects nothing | **Held.** The reason quotes that reference and lists the onboarded repositories. This holds even when another declaration on the same card would have resolved. |

Two consequences worth stating plainly, because both are deliberate:

- **A declaration overrides; it does not break a tie.** It has the same effect on a card that
  would have resolved as on one that would have been held. An override that only applied when
  the system was confused could not be tested by the author, who cannot see whether the system
  is confused.
- **A declaration is never silently ignored.** If the author wrote one and it does not work,
  the card is held and told so. It does not quietly fall back to guessing from the text.

## What a held card is told

The reason appears in `robot-army cards`, on `/cards`, in the `trello.needs_info` audit
record, and — once per distinct reason — as a comment on the card itself.

- A reference that selected nothing:

  > the `robot-army:` line on this card names 'jantmna/demo', which is not an onboarded
  > repository — onboarded: jantman/demo, jantman/other

- Declarations that disagree:

  > this card gives more than one `robot-army:` line and they name different repositories
  > (jantman/demo, jantman/other); it must name exactly one

- No declaration and nothing recognised in the text — **unchanged from today**:

  > no onboarded repository could be identified from this card. Name one by its GitHub URL,
  > its owner/name, or its local path — onboarded: jantman/demo, jantman/other

- No declaration and two repositories named in the text — **unchanged from today**:

  > this card names 2 onboarded repositories (jantman/demo, jantman/other); it must name
  > exactly one

The comment left on the card carries the reason and then tells the author how to fix it,
including the shape of the line, so the card alone is enough to act on.

## What is unchanged

- **The card's description reaches the filed issue verbatim.** The declaration is not
  stripped, moved, or rewritten. It stays in the quoted block, where it records which
  repository was chosen and why.
- **A card parked in an ignored column stays unacted-on.** A declaration says which
  repository, not whether to act.
- **The held machinery.** The `needs_info` state, one comment per distinct reason,
  re-evaluation when the card is edited, and `robot-army rescan` all behave as they do today.
  This feature adds reasons for holding a card, not a new way of holding one.

## What the record says

The existing `trello.evaluated` audit record, written once per card evaluation, gains one key:

```json
{"resolvable": true, "repo_key": "jantman/demo", "candidates": ["jantman/demo"],
 "reason": null, "source": "declaration"}
```

`source` is `"declaration"` when the outcome — resolved or held — came from the card's
declarations, and `"scan"` when it came from the ordinary reading of the card's text. From the
log alone it is therefore always answerable whether the author named the repository or the
system inferred it.
