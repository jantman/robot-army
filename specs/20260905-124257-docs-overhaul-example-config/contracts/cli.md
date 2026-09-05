# Contract: `robot-army example-config`

## Synopsis

```
robot-army example-config [--output PATH] [--force]
```

## Placement

Routed in `cli.main()` **before** `load_config` is called, beside `run` and `serve` — the
two commands already handled there. Not in the `_dispatch` table.

This is a correctness requirement, not a style preference. `main()` loads the configuration
and builds a `Context` before consulting the table, so every command in the table presumes a
valid config already exists. This command's entire purpose is to be run on a machine that
has none; routed through the table it would fail with `config file not found`, which is the
exact condition it exists to resolve.

It follows that a global `--config PATH` is accepted by the parser (it is defined on the
root parser) but **has no effect** on this command. Nothing is read from it.

## Arguments

| Argument | Default | Meaning |
|---|---|---|
| *(none)* | — | Render to standard output. |
| `--output PATH` | — | Render to `PATH` instead of standard output. |
| `--force` | off | Permit `--output` to replace an existing file. |

`--force` without `--output` is a usage error (exit 2): there is nothing to force.

## Streams

| Stream | Carries |
|---|---|
| stdout | The rendered document, and only that, when no `--output` is given. Nothing at all when `--output` is given. |
| stderr | Every message: the confirmation of a written file, and every refusal. |

The split matters: `robot-army example-config > config.toml` must produce a file containing
the document and nothing else, so no banner, no "wrote N bytes", and no warning may reach
stdout.

## Exit codes

Following the existing contract in `cli.py`'s module docstring.

| Code | Condition |
|---|---|
| 0 | The document was rendered to stdout, or written to `--output`. |
| 1 | The write failed — permission denied, no such directory, disk full. The error names the path and the reason. |
| 2 | Usage error: `--force` without `--output`. |
| 3 | `--output` names an existing path and `--force` was not given. |

Exit 3 rather than 1 for the refusal, because the operation did not fail — a precondition
was not met, which is the meaning exit 3 already carries in this CLI.

## Side effects

| Invocation | Writes a file | Writes an audit record |
|---|---|---|
| no `--output` | no | **no** — the documented Principle III exception: nothing outside the process changed |
| `--output`, success | yes, atomically | yes: `example_config.write`, outcome `success` |
| `--output`, refused (exists, no `--force`) | no | yes: outcome `failure`, detail naming the refusal |
| `--output`, failed (I/O error) | no | yes: outcome `failure`, detail carrying the error |

The audit record goes to the **default** layout's log directory. This command runs before any
configuration is read, so a non-default `[paths] state_dir` cannot be honoured; the plan
records this as the second, smaller documented exception. Failing to open the audit log does
not fail the write — the file is the point, and a lost record is reported on stderr rather
than turned into a lost config.

## Guarantees

1. Never reads the author's configuration, environment, or state (FR-020).
2. Output is byte-identical across machines and runs (FR-016).
3. The written file is complete or absent, never partial (research R7).
4. The document loads through `config.load()` unmodified, on a machine where the
   directories it names exist (FR-013, research R4).
