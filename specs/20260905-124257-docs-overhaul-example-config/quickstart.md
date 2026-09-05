# Quickstart: validating the docs overhaul and example config

Run in order. Each step proves one requirement group and says what "working" looks like.

## Prerequisites

```bash
cd /path/to/robot-army
uv sync            # or: pip install -e '.[dev]'
```

## 1. The generator produces a document (FR-010, FR-012)

```bash
uv run robot-army example-config | head -40
```

**Expect**: TOML on stdout, starting with a preamble comment, every key carrying a
same-line comment. Nothing on stderr.

## 2. It is complete (FR-011)

```bash
uv run robot-army example-config > /tmp/ex.toml
# every section the loader knows about is present:
python - <<'PY'
from robot_army.config import _KNOWN_KEYS, _REPO_KEYS
text = open("/tmp/ex.toml").read()
missing = [f"{s}.{k}" for s, keys in _KNOWN_KEYS.items() for k in keys
           if f"{k} = " not in text and f"# {k} = " not in text]
missing += [f"repos.{k}" for k in _REPO_KEYS
            if f"{k} = " not in text and f"# {k} = " not in text]
print("missing:", missing or "none")
PY
```

**Expect**: `missing: none`. The real check is `tests/unit/test_example_config.py`, which
does this per-section rather than across the whole file.

## 3. It loads unmodified (FR-013)

```bash
export HOME=$(mktemp -d)
mkdir -p "$HOME/GIT" "$HOME/worktrees"
uv run python -c "
from pathlib import Path
from robot_army.config import load
c = load(Path('/tmp/ex.toml'))
print('loaded:', c.path)
print('warnings:', c.warnings or 'none')
print('trello:', c.trello, 'pushover:', c.pushover)
"
```

**Expect**: it loads; `warnings: none`; `trello: None pushover: None` — proving FR-015, the
inert-on-copy guarantee. Creating `~/GIT` is arranging the machine the file describes, not
editing the file (research R4).

## 4. It is reproducible (FR-016)

```bash
uv run robot-army example-config > /tmp/a.toml
XDG_RUNTIME_DIR=/run/user/9999 uv run robot-army example-config > /tmp/b.toml
diff /tmp/a.toml /tmp/b.toml && echo "identical"
```

**Expect**: `identical`. A difference means an environment-derived value leaked into the
output — the `[terminal] socket_glob` hazard from research R2.

## 5. Writing to a file is safe and recorded (FR-017, FR-026)

```bash
uv run robot-army example-config --output /tmp/out.toml; echo "exit=$?"   # 0
uv run robot-army example-config --output /tmp/out.toml; echo "exit=$?"   # 3, refused
uv run robot-army example-config --output /tmp/out.toml --force; echo "exit=$?"  # 0
uv run robot-army example-config --force; echo "exit=$?"                 # 2, usage
grep example_config ~/.local/state/robot-army/logs/audit-$(date -u +%F).jsonl
```

**Expect**: exits 0, 3, 0, 2 in that order; the refusal message on **stderr**; three audit
records — success, failure, success. No record for step 1's stdout run.

## 6. The committed copy is current (FR-018, FR-023)

```bash
uv run robot-army example-config | diff -u share/config.example.toml - && echo "in sync"
```

**Expect**: `in sync`. This is exactly what `tests/unit/test_example_config_drift.py`
asserts. When it fails, the fix is:

```bash
uv run robot-army example-config --output share/config.example.toml --force
```

## 7. The documentation links resolve (FR-008, FR-025)

```bash
uv run pytest tests/unit/test_docs_links.py -q
```

**Expect**: pass. It walks `README.md`, `docs/index.md` and every `docs/guide/*.md`, and
resolves each relative link against the filesystem.

## 8. The whole suite (SC-007)

```bash
uv run pytest -q
```

**Expect**: pass, with three new test modules included.

## 9. The one manual step — enabling GitHub Pages (FR-001)

This cannot be committed; it is a repository setting.

**Settings → Pages → Build and deployment**: Source = *Deploy from a branch*,
Branch = `main`, Folder = `/docs`. Save.

**Expect**, a minute later, `https://jantman.github.io/robot-army/` showing the landing
page, with the guide reachable from it and the roadmap, incident notes, verification notes
and `initial-planning/` absent from the site while still present in the repository
(FR-007). No workflow file of ours runs; the build is GitHub's own
`pages-build-deployment`.

## 10. Read it as a stranger would

Open the published guide and confirm, by eye, what no test can assert:

- Every subject the old README covered is reachable in two clicks (SC-004).
- No page much exceeds 350 lines (SC-003): `wc -l docs/guide/*.md`.
- Nothing has acquired a product voice, a contribution guide, a support channel, or a
  badge (FR-009, Principle V).
