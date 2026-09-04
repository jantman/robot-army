# Data model: the composed prompt and its trust levels

There is no persistent data in this feature — no table, no column, no file. The "model" is the
structure of one string, and the useful thing to write down about it is *who wrote each part*,
because that is the distinction the whole feature exists to make visible.

## The prompt, section by section

| # | Section | Written by | Optional? | Fenced? |
|---|---|---|---|---|
| 1 | `.claude/robot-army.md` | the repository (RA-02: not yet integrity-checked) | yes | no |
| 2 | Spec Kit block | this system, plus the operator's configured per-command instructions | yes | no |
| 3 | `DELIVERY` | this system | no | no |
| 4 | Issue framing — repo key, issue number, branch, URL | this system, from its own config and the GitHub API | no | no |
| 5 | Issue payload — title, labels, body | **the issue's author** | no | **yes** |

Sections 1–4 are joined by `\n\n---\n\n`, unchanged. Section 5 sits inside section 4, wrapped in
the fence.

**Precedence** is by position, earliest outranking latest, and every section says so in its own
words where position alone would be ambiguous:

- Section 1 outranks everything because it is first. Unchanged.
- Section 2 says "the instruction above wins", scoped to what is above it. Unchanged.
- Section 3 **used to** cede to section 5 in its last paragraph. After this feature it asserts
  the opposite: position now tells the truth for it too.
- Section 5 outranks nothing. It is data.

## The fence

| Field | Value |
|---|---|
| Label | `ROBOT-ARMY-ISSUE` |
| Nonce | 16 lowercase hex characters, `secrets.token_hex(8)`, fresh per `compose` call |
| Opening line | `<<<ROBOT-ARMY-ISSUE {nonce}>>>` |
| Closing line | `<<<END-ROBOT-ARMY-ISSUE {nonce}>>>` |
| Scope | title, labels, body |

### Invariants

1. Both marker lines appear exactly once, in order, in every composed prompt.
2. The nonce does not occur between them.
3. The nonce is the only part of a composed prompt that varies between two calls with identical
   arguments.
4. The region between the markers contains no C0 control character except line feed and tab, and
   no DEL.
5. The region between the markers is at most `MAX_BODY_CHARS` + the title line + the labels line
   + the truncation notice — bounded, so the prompt stays inside one `argv` entry.

## Transformations applied to issue text

Applied in this order; each is total, and none can fail.

| Step | Input | Output |
|---|---|---|
| Normalise line endings | `\r\n`, `\r` | `\n` |
| Strip control characters | `[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]` | removed |
| Collapse whitespace (**title and each label**) | runs of whitespace | one space |
| Drop empties (**labels only**) | a label that sanitised to nothing | removed from the list |
| Strip | leading/trailing whitespace | removed |
| Truncate (**body only**, if over the limit) | `body[MAX_BODY_CHARS:]` | replaced by `\n\n[truncated at 60000 characters]` |
| De-nonce | every occurrence of the nonce | removed |

Nothing about these is reversible, and nothing about them is meant to be: the prompt is what
the session reads, and a reader of the prompt should be seeing exactly what the session saw.
The issue's real text remains where it always was — on the issue.
