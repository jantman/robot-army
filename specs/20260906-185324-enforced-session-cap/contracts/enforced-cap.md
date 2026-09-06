# Contract: which cap a surface reports against, and what it says about it

This is the whole of the behaviour. Anything a renderer, a command, or a consumer needs to
know is here; nothing derives its own answer from the two integers.

## 1. The decision table

Inputs: does a daemon hold the lock, can its heartbeat be read, does that heartbeat carry a
usable cap, and what is in the reading process's own configuration.

| Lock held | Heartbeat | Published cap | Cap reported | Disagreement reported |
|---|---|---|---|---|
| no | — | — | configured | never |
| yes | unreadable / absent | — | configured | **never** (§4) |
| yes | readable, fresh | absent or unusable | configured | never |
| yes | readable, fresh | *n* = configured | *n* | no |
| yes | readable, fresh | *n* ≠ configured | ***n*** | **yes** |
| yes | readable, **stale** | *n* ≠ configured | ***n*** | **yes** |

A stale heartbeat from the process holding the lock is authoritative about the cap, for the
reason it is authoritative about the effect level: a daemon's cap is fixed when it starts and
cannot change while it runs.

"Usable" means: an `int`, not a `bool`, at least 1. Anything else is *not published*.

## 2. What each surface shows

| Surface | Shows |
|---|---|
| Web chrome pill, every page | `<total>/<cap in force> sessions (<n> ours, <m> other)` — unchanged in shape. Styled "at capacity" only when `total >= cap in force`. |
| Web notices, every page | The disagreement sentence, as a `banner warn`, when and only when there is one. Placed with the daemon-not-running and effect-level banners, after them. |
| `robot-army capacity` | `capacity     : <total> of <cap in force> sessions running`, unchanged in shape, plus one `cap          : ` line carrying the sentence when there is one. It has no `--json` flag and never had one. |
| `robot-army status` | The existing `capacity     : <describe()>` line, whose text now carries the disagreement as a trailing clause. |
| `status --json` / web JSON | `global_cap`, `configured_cap`, `cap_disagreement` (§3). |
| Queue view / `status` queue | Planned against the snapshot carrying the cap in force, so a per-item "at capacity" reason cannot contradict the fraction above it. |

## 3. The machine-readable shape

Present in `_capacity_dict`, which the web chrome, the `/queue` payload and `status --json`
all render from, and in the document `capacity` assembles for itself:

```json
{
  "total": 6,
  "global_cap": 7,
  "configured_cap": 5,
  "cap_disagreement": "SESSION CAP MISMATCH: …",
  "…": "every other key unchanged"
}
```

- `global_cap` is **always** the cap in force. A consumer that reads only this key — which is
  every consumer that exists today — is correct without knowing this feature exists.
- `configured_cap` is `null` unless this process's configuration disagrees with the cap in
  force. Its presence *is* the disagreement; no consumer compares numbers.
- `cap_disagreement` is `null` or the exact sentence in §5.

`capacity`'s own document carries the same three keys. It has no `--json` flag on the CLI —
`status --json` is where that payload is read from — but the two documents must not be free
to disagree about the same three facts.

When capacity is unobservable, the keys are present and behave the same way: the cap in force
is still resolved and still reported, because "how full is it?" being unanswerable does not
make "what is the limit?" unanswerable.

## 4. Silence, and where it is deliberate

No disagreement is reported when:

- **No daemon holds the lock.** Nothing is enforcing a cap; the reader's configuration is the
  best available answer and is not in conflict with anything.
- **A daemon holds the lock but no heartbeat can be read.** The interface already renders a
  prominent banner for this state saying nothing about the daemon can be read and that
  actions are refused. A second banner about one field of an unreadable file competes with it
  and adds nothing.
- **The heartbeat carries no cap.** A build that did not publish one; the next tick of the
  current build supplies it.

## 5. The sentence

One string, built in one place (`CapacitySnapshot.cap_disagreement`), rendered verbatim by
every surface:

```text
SESSION CAP MISMATCH: the running daemon is enforcing a cap of {enforced}, and this process
is configured for {configured}. The cap shown is the daemon's, because the daemon is what
enforces it. One of the two has been running since before the configuration changed —
restart that one and they will agree.
```

Rendered as a single line (no newlines); wrapped here only for reading.

Three properties are load-bearing:

- **Both numbers appear**, so the reader can tell which of the two is the one in their
  editor.
- **It says which is in force and why.** Without that, showing two numbers is worse than
  showing one.
- **It does not claim to know which process is stale**, because it cannot. Both directions
  are reachable, the remedies are opposite ("restart the interface" / "restart the daemon"),
  and a confident wrong instruction here would send the operator to restart the daemon —
  which kills nothing, but does nothing either, and would leave them looking at the same
  page.

## 6. The launch gate

`dispatch.check_launch_gate` runs in whichever process is about to launch — the daemon, the
web on a *Resume*, the terminal on `resume`/`restart` — so the cap is enforced in more than
one place and a *refusal* is a surface like any other.

| Caller | `enforced_cap` passed | Effect |
|---|---|---|
| `select_and_dispatch` (the daemon) | none | unchanged: it is the authority and consults nothing |
| `web.require_dispatchable` | resolved, fresh | a refusal cites the number the page is showing |
| `operations.resume` / `restart` | resolved, fresh | the same, from the terminal or the web's worker |

Resolved fresh at the gate rather than taken from the request's reading, deliberately: this
gate's documented character is that the check at the launch decides, and minutes can pass
between a render and a press.

Both directions matter, and only one of them is about the display:

- **The reading process is the stale, lower one.** Without this it refuses a launch the
  daemon would allow, citing a cap the page has stopped showing — issue #30's `6/5`,
  relocated into a `409`.
- **The reading process is the higher one.** Without this it launches past the cap the
  daemon is enforcing. That is an over-dispatch, which is the harmful direction: it
  oversubscribes the one subscription the cap exists to protect.

## 7. What this never does

- **A disagreement never refuses, blocks, or alters an action.** No control is disabled and
  no page is withheld because two processes hold different numbers. What a gate refuses, it
  refuses against the enforced cap — which is what the daemon would have refused anyway.
- **It never changes a dispatch decision made by the daemon.** The daemon plans and gates
  against its own configuration with no `enforced_cap` supplied, exactly as before.
- **It writes nothing.** No audit record, no anomaly, no state file — reporting capacity is a
  read.
